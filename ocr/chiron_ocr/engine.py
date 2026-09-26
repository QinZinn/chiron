"""Text detection and recognition with PaddleOCR, loaded once.

Three models: a page-orientation classifier, a text-line detector and an
English recogniser. The recogniser is PP-OCRv6_small_rec — chosen by measurement
(ocr/NOTES.md, 2026-09-26): among six PaddleOCR recognisers run on the same line
crops it had the lowest character error rate on both printed English (0.35 %)
and real handwritten English notes (28 %), and was the fastest.

Loading takes seconds and hundreds of MB, so it happens once, in the background
at startup — `/health` reports `ready: false` until it finishes, and the compose
healthcheck waits on that rather than on the port being open.

Inference is not documented as thread-safe, and on CPU two concurrent pages
would only fight over the same cores. A lock serialises them; this serves one
learner, not a queue.
"""

from __future__ import annotations

import logging
import os
import threading
import time
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

REC_MODEL = os.environ.get("OCR_REC_MODEL", "PP-OCRv6_small_rec")

ENGINE_NAME = f"{DET_MODEL} + {REC_MODEL}"

_lock = threading.Lock()
_models: dict[str, Any] | None = None
_error: str | None = None


def load() -> None:
    global _models, _error
    started = time.monotonic()
    try:
        from paddleocr import DocImgOrientationClassification, TextDetection, TextRecognition

        models = {
            "det": TextDetection(model_name=DET_MODEL, enable_mkldnn=USE_MKLDNN),
            "orientation": (
                DocImgOrientationClassification(model_name=ORIENTATION_MODEL, enable_mkldnn=USE_MKLDNN)
                if USE_ORIENTATION
                else None
            ),
            "rec": TextRecognition(model_name=REC_MODEL, enable_mkldnn=USE_MKLDNN),
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

    from chiron_ocr.geometry import crop_quad, upright

    if _models is None:
        raise NotReady(_error or "OCR models are still loading; try again in a few seconds")
    image = cv2.imread(image_path, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"could not read page image {image_path}")
    with _lock:
        if _models["orientation"] is not None:
            label = _first(_models["orientation"].predict(image))["label_names"][0]
            image = upright(image, int(label))
        polys = _first(_models["det"].predict(image))["dt_polys"]
        crops = [crop_quad(image, poly) for poly in polys]
        results = list(_models["rec"].predict(crops)) if crops else []
    texts = [str(r["rec_text"]) for r in results]
    scores = [float(r["rec_score"]) for r in results]
    boxes = [
        box_from_polygon(text.strip(), score, poly)
        for text, score, poly in zip(texts, scores, polys)
        if text.strip()
    ]
    return page_summary(group_lines(boxes))
