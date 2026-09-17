"""Uploaded files → page images on disk, ready for OCR.

Pure file handling, no Paddle, so the limits can be tested cheaply. Images are
checked by their bytes, not their filename or declared content type: a phone
happily uploads a HEIC photo named `.jpg`, and a clear "unsupported" beats a
Paddle stack trace.
"""

from __future__ import annotations

import io
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

MAX_PAGES = int(os.environ.get("OCR_MAX_PAGES", "30"))
# 200 DPI: enough for PaddleOCR to read 10–11pt print and ordinary handwriting,
# without the 4× memory and time of 400 DPI on a 30-page PDF.
PDF_DPI = int(os.environ.get("OCR_PDF_DPI", "200"))
# Very large phone photos (50 MP) are downscaled before OCR. Detection works on
# a resized image anyway; decoding and moving 12k-pixel bitmaps only costs time.
MAX_IMAGE_SIDE = int(os.environ.get("OCR_MAX_IMAGE_SIDE", "4000"))


class DocumentError(ValueError):
    """A problem with what was uploaded — reported to the caller as a 4xx."""


class TooManyPages(DocumentError):
    pass


@dataclass(frozen=True)
class Page:
    source: str  # original filename
    number: int  # 1-based within that file (always 1 for an image)
    path: Path


def kind_of(data: bytes) -> str:
    """'pdf', 'image', or raise. Decided from magic bytes."""
    if data[:5] == b"%PDF-":
        return "pdf"
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
        return "image"
    except Exception as exc:  # noqa: BLE001 — PIL raises many unrelated types
        raise DocumentError(
            "Không đọc được tệp: chỉ nhận ảnh (JPG, PNG, WebP…) hoặc PDF"
        ) from exc


def _save_image(img: Image.Image, directory: Path, name: str) -> Path:
    # exif_transpose: phone photos store "rotate 90°" as metadata rather than
    # rotating the pixels; without this, portrait notes reach the OCR sideways.
    img = ImageOps.exif_transpose(img).convert("RGB")
    if max(img.size) > MAX_IMAGE_SIDE:
        img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    path = directory / name
    img.save(path, format="PNG")
    return path


def to_pages(files: list[tuple[str, bytes]], directory: Path) -> list[Page]:
    """Expand uploads into pages, in upload order. Enforces MAX_PAGES."""
    if not files:
        raise DocumentError("Chưa có tệp nào được gửi lên")
    pages: list[Page] = []
    for index, (filename, data) in enumerate(files):
        kind = kind_of(data)
        if kind == "image":
            _guard(len(pages) + 1)
            with Image.open(io.BytesIO(data)) as img:
                path = _save_image(img, directory, f"{index:03d}-001.png")
            pages.append(Page(filename, 1, path))
            continue

        import pypdfium2 as pdfium  # imported lazily: only PDFs need it

        try:
            pdf = pdfium.PdfDocument(data)
        except Exception as exc:  # noqa: BLE001
            raise DocumentError(f"PDF “{filename}” bị hỏng hoặc có mật khẩu") from exc
        try:
            _guard(len(pages) + len(pdf))
            for n in range(len(pdf)):
                bitmap = pdf[n].render(scale=PDF_DPI / 72)
                path = _save_image(bitmap.to_pil(), directory, f"{index:03d}-{n + 1:03d}.png")
                pages.append(Page(filename, n + 1, path))
        finally:
            pdf.close()
    return pages


def _guard(total: int) -> None:
    if total > MAX_PAGES:
        raise TooManyPages(f"Tối đa {MAX_PAGES} trang mỗi lần quét")


def work_dir() -> tempfile.TemporaryDirectory:
    return tempfile.TemporaryDirectory(prefix="chiron-ocr-")
