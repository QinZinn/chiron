"""Upload handling: what is accepted, what is refused, and the page limit."""

import io

import pytest
from PIL import Image

from chiron_ocr import documents
from chiron_ocr.documents import DocumentError, TooManyPages, kind_of, to_pages


def png_bytes(size=(60, 40)):
    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, format="PNG")
    return buf.getvalue()


def test_kind_is_decided_by_content_not_by_name():
    assert kind_of(png_bytes()) == "image"
    assert kind_of(b"%PDF-1.7\n...") == "pdf"


def test_something_that_is_neither_is_refused_clearly():
    with pytest.raises(DocumentError):
        kind_of(b"not an image at all")


def test_images_become_one_page_each(tmp_path):
    pages = to_pages([("a.png", png_bytes()), ("b.png", png_bytes())], tmp_path)
    assert [(p.source, p.number) for p in pages] == [("a.png", 1), ("b.png", 1)]
    assert all(p.path.exists() for p in pages)


def test_nothing_uploaded_is_an_error(tmp_path):
    with pytest.raises(DocumentError):
        to_pages([], tmp_path)


def test_the_page_limit_is_enforced(tmp_path, monkeypatch):
    monkeypatch.setattr(documents, "MAX_PAGES", 2)
    with pytest.raises(TooManyPages):
        to_pages([(f"{i}.png", png_bytes()) for i in range(3)], tmp_path)


def test_huge_photos_are_downscaled(tmp_path, monkeypatch):
    monkeypatch.setattr(documents, "MAX_IMAGE_SIDE", 100)
    [page] = to_pages([("big.png", png_bytes((500, 300)))], tmp_path)
    with Image.open(page.path) as img:
        assert max(img.size) == 100
