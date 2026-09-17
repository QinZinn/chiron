"""Text detection with PaddleOCR, recognition with VietOCR, loaded once.

Why two models: no official PaddleOCR recognition model can write Vietnamese.
The dictionaries of PP-OCRv6 (what `lang="vi"` resolves to) and latin
PP-OCRv5 have "ư" and "đ" but none of the tone-marked vowels (ợ, ạ, ệ, … —
U+1EA0–U+1EF9), so "Quang hợp ở thực vật" came back as "Quang hp  thc vt".
PaddleOCR's detector finds the text lines well regardless of script; VietOCR
(vgg_transformer, trained on printed and handwritten Vietnamese) reads each
line crop.

Loading takes seconds and hundreds of MB, so it happens once, in the background
at startup — `/health` reports `ready: false` until it finishes, and the compose
healthcheck waits on that rather than on the port being open.

Inference is not documented as thread-safe for either library, and on CPU two
concurrent pages would only fight over the same cores. A lock serialises them;
this serves one learner, not a queue.
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from chiron_ocr.layout import box_from_polygon, group_lines, page_summary

log = logging.getLogger("chiron_ocr")

DET_MODEL = os.environ.get("OCR_DET_MODEL", "PP-OCRv6_medium_det")
ORIENTATION_MODEL = "PP-LCNet_x1_0_doc_ori"
# Phone photos of a notebook are often taken sideways or upside down; the page
# orientation classifier fixes 90/180/270° before detection.
USE_ORIENTATION = os.environ.get("OCR_DOC_ORIENTATION", "1") == "1"
# Paddle 3.3's oneDNN path fails on the PP-OCRv6 detector under the PIR executor
# ("ConvertPirAttribute2RuntimeAttribute not support"), so it is off by default.
USE_MKLDNN = os.environ.get("OCR_ENABLE_MKLDNN", "0") == "1"

VIETOCR_CONFIG = "vgg_transformer"
# Config and weights are written here at image build time (see Dockerfile), so
# the running container never fetches anything. Outside Docker the first load
# downloads them.
VIETOCR_DIR = Path(os.environ.get("OCR_VIETOCR_DIR", Path.home() / ".cache" / "chiron-ocr" / "vietocr"))
BATCH_SIZE = 16

ENGINE_NAME = f"{DET_MODEL} + vietocr/{VIETOCR_CONFIG}"

_lock = threading.Lock()
_models: dict[str, Any] | None = None
_error: str | None = None


def _vietocr_config() -> Any:
    from vietocr.tool.config import Cfg

    config_path = VIETOCR_DIR / "config.yml"
    weights_path = VIETOCR_DIR / "weights.pth"
    if config_path.exists() and weights_path.exists():
        config = Cfg.load_config_from_file(str(config_path))
    else:
        from vietocr.tool.utils import download_weights

        VIETOCR_DIR.mkdir(parents=True, exist_ok=True)
        config = Cfg.load_config_from_name(VIETOCR_CONFIG)
        shutil.copyfile(download_weights(config["weights"]), weights_path)
        config["weights"] = str(weights_path)
        # The backbone's ImageNet weights (a 548 MB download) are overwritten
        # by the VietOCR checkpoint anyway.
        config["cnn"]["pretrained"] = False
        config.save(str(config_path))
    config["device"] = "cpu"
    # predict_batch always decodes greedily; this only affects predict().
    config["predictor"]["beamsearch"] = False
    return config


def load() -> None:
    global _models, _error
    started = time.monotonic()
    try:
        from paddleocr import DocImgOrientationClassification, TextDetection
        from vietocr.tool.predictor import Predictor

        models = {
            "det": TextDetection(model_name=DET_MODEL, enable_mkldnn=USE_MKLDNN),
            "orientation": (
                DocImgOrientationClassification(model_name=ORIENTATION_MODEL, enable_mkldnn=USE_MKLDNN)
                if USE_ORIENTATION
                else None
            ),
            "rec": Predictor(_vietocr_config()),
        }
        with _lock:
            _models = models
        _error = None
        log.info("OCR ready (%s) in %.1fs", ENGINE_NAME, time.monotonic() - started)
    except Exception as exc:  # noqa: BLE001 — reported through /health
        _error = f"{type(exc).__name__}: {exc}"
        log.exception("OCR models failed to load")


def load_in_background() -> None:
    threading.Thread(target=load, name="ocr-load", daemon=True).start()


def status() -> dict:
    return {"ready": _models is not None, "engine": ENGINE_NAME, "error": _error}


class NotReady(RuntimeError):
    pass


def _first(results: Any) -> Any:
    return next(iter(results))


def recognise(image_path: str) -> dict:
    """OCR one page image → the page summary from layout.page_summary."""
    import cv2
    from PIL import Image

    from chiron_ocr.geometry import crop_quad, upright

    if _models is None:
        raise NotReady(_error or "Mô hình OCR đang tải, thử lại sau ít giây")
    image = cv2.imread(image_path, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"không đọc được ảnh trang {image_path}")
    with _lock:
        if _models["orientation"] is not None:
            label = _first(_models["orientation"].predict(image))["label_names"][0]
            image = upright(image, int(label))
        polys = _first(_models["det"].predict(image))["dt_polys"]
        crops = [crop_quad(image, poly) for poly in polys]
        # VietOCR expects RGB PIL images; OpenCV decodes BGR.
        pil = [Image.fromarray(cv2.cvtColor(c, cv2.COLOR_BGR2RGB)) for c in crops]
        texts: list[str] = []
        scores: list[float] = []
        for i in range(0, len(pil), BATCH_SIZE):
            batch_texts, batch_scores = _models["rec"].predict_batch(pil[i : i + BATCH_SIZE], return_prob=True)
            texts.extend(batch_texts)
            scores.extend(float(s) for s in batch_scores)
    boxes = [
        box_from_polygon(text.strip(), score, poly)
        for text, score, poly in zip(texts, scores, polys)
        if text.strip()
    ]
    return page_summary(group_lines(boxes))
