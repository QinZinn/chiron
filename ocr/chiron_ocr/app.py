"""HTTP API for the OCR service.

    GET  /health   {"status": "ok", "ready": bool, ...}
    POST /ocr      multipart, one or more `files` (images or PDFs) → pages

Internal only: no host port in docker-compose.yml, and its one caller is the
Knowledge Store. It does not store anything — the upload lives in a temporary
directory for the length of the request and is gone when it returns.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from flask import Flask, jsonify, request

from chiron_ocr import engine
from chiron_ocr.documents import DocumentError, TooManyPages, to_pages, work_dir

MAX_UPLOAD_MB = int(os.environ.get("OCR_MAX_UPLOAD_MB", "40"))


def create_app(load_model: bool = True) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
    if load_model:
        engine.load_in_background()

    @app.get("/health")
    def health():
        return jsonify({"status": "ok", **engine.status()}), 200

    @app.errorhandler(413)
    def too_large(_):
        return jsonify({"error": "too_large", "detail": f"Tổng dung lượng tối đa {MAX_UPLOAD_MB} MB"}), 413

    @app.post("/ocr")
    def ocr():
        uploads = request.files.getlist("files")
        files = [(f.filename or "tệp", f.read()) for f in uploads]
        started = time.monotonic()
        try:
            with work_dir() as tmp:
                pages = to_pages(files, Path(tmp))
                out = []
                for page in pages:
                    summary = engine.recognise(str(page.path))
                    out.append({"source": page.source, "number": page.number, **summary})
        except TooManyPages as exc:
            return jsonify({"error": "too_many_pages", "detail": str(exc)}), 413
        except DocumentError as exc:
            return jsonify({"error": "invalid_document", "detail": str(exc)}), 400
        except engine.NotReady as exc:
            return jsonify({"error": "not_ready", "detail": str(exc)}), 503
        except Exception as exc:  # noqa: BLE001 — a model failure, not the caller's fault
            logging.getLogger("chiron_ocr").exception("OCR failed")
            return jsonify({"error": "ocr_failed", "detail": f"{type(exc).__name__}: {exc}"}), 500
        return jsonify({
            "pages": out,
            "page_count": len(out),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }), 200

    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    port = int(os.environ.get("OCR_PORT", "8866"))
    host = os.environ.get("OCR_HOST", "127.0.0.1")
    # threaded: /health must answer while a long OCR request is running; the
    # engine lock still keeps predictions one at a time.
    create_app().run(host=host, port=port, threaded=True)


if __name__ == "__main__":
    main()
