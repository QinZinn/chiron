"""Calls the OCR service (Chiron/ocr). Standard library only — KS pulls in no extra dependency.

Protocol + fake, like LLMProvider and CardClient: tests swap OcrClient for a fake
function and need no real PaddleOCR.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from ks import settings


class OcrError(Exception):
    """OCR returned no result. `status` suggests the HTTP code to return to the client."""

    def __init__(self, message: str, status: int = 502, code: str = "ocr_failed"):
        super().__init__(message)
        self.status = status
        self.code = code


class OcrNotConfigured(OcrError):
    def __init__(self):
        super().__init__(f"{settings.OCR_URL_ENV} is not configured", status=503, code="ocr_not_configured")


@dataclass(frozen=True)
class Upload:
    filename: str
    content_type: str
    data: bytes


class OcrClient(Protocol):
    def recognise(self, uploads: list[Upload]) -> dict[str, Any]:
        """→ {"pages": [...], "page_count": n, "elapsed_ms": ms}"""
        ...


def _multipart(uploads: list[Upload]) -> tuple[bytes, str]:
    boundary = f"chiron-{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for up in uploads:
        # Non-ASCII file names: filename*=UTF-8'' per RFC 5987, plus an ASCII
        # fallback filename — Werkzeug reads both.
        safe = up.filename.encode("ascii", "replace").decode().replace('"', "_")
        quoted = urllib.request.quote(up.filename)
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="files"; filename="{safe}";'
                f" filename*=UTF-8''{quoted}\r\n"
                f"Content-Type: {up.content_type or 'application/octet-stream'}\r\n\r\n"
            ).encode()
            + up.data
            + b"\r\n"
        )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


class HttpOcrClient:
    def __init__(self, base_url: str, timeout: float = settings.OCR_TIMEOUT_SECONDS):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def recognise(self, uploads: list[Upload]) -> dict[str, Any]:
        body, content_type = _multipart(uploads)
        req = urllib.request.Request(
            f"{self._base_url}/ocr",
            data=body,
            headers={"Content-Type": content_type},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # File errors (400/413) and "model still loading" (503) are passed
            # through to the learner as-is — they can fix them. Anything else is the OCR service's.
            try:
                payload = json.loads(exc.read().decode("utf-8"))
                detail, code = payload.get("detail", str(exc)), payload.get("error", "ocr_failed")
            except Exception:  # noqa: BLE001
                detail, code = str(exc), "ocr_failed"
            status = exc.code if exc.code in (400, 413, 503) else 502
            raise OcrError(detail, status=status, code=code) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise OcrError(f"Could not reach the OCR service: {exc}", status=502, code="ocr_unreachable") from exc


def client_from_env(env: dict[str, str] | None = None) -> OcrClient:
    env = os.environ if env is None else env
    url = env.get(settings.OCR_URL_ENV, "").strip()
    if not url:
        raise OcrNotConfigured()
    return HttpOcrClient(url)
