"""Gọi service OCR (Chiron/ocr). Chỉ thư viện chuẩn — KS không kéo thêm dependency.

Protocol + fake như LLMProvider và CardClient: test thay OcrClient bằng hàm giả,
không cần PaddleOCR thật.
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
    """OCR không trả được kết quả. `status` gợi ý mã HTTP nên trả cho client."""

    def __init__(self, message: str, status: int = 502, code: str = "ocr_failed"):
        super().__init__(message)
        self.status = status
        self.code = code


class OcrNotConfigured(OcrError):
    def __init__(self):
        super().__init__(f"Chưa cấu hình {settings.OCR_URL_ENV}", status=503, code="ocr_not_configured")


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
        # Tên tệp tiếng Việt: dạng filename*=UTF-8'' theo RFC 5987, kèm filename
        # ASCII dự phòng — Werkzeug đọc được cả hai.
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
            # Lỗi do tệp (400/413) và "model đang tải" (503) được chuyển nguyên
            # cho người học — họ sửa được. Lỗi khác là của service OCR.
            try:
                payload = json.loads(exc.read().decode("utf-8"))
                detail, code = payload.get("detail", str(exc)), payload.get("error", "ocr_failed")
            except Exception:  # noqa: BLE001
                detail, code = str(exc), "ocr_failed"
            status = exc.code if exc.code in (400, 413, 503) else 502
            raise OcrError(detail, status=status, code=code) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise OcrError(f"Không kết nối được service OCR: {exc}", status=502, code="ocr_unreachable") from exc


def client_from_env(env: dict[str, str] | None = None) -> OcrClient:
    env = os.environ if env is None else env
    url = env.get(settings.OCR_URL_ENV, "").strip()
    if not url:
        raise OcrNotConfigured()
    return HttpOcrClient(url)
