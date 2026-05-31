"""Tests for pdf_converter.load_image.

The image branch always runs. The PDF branch needs poppler (pdftoppm) on PATH and
pdf2image installed, so it is skipped automatically when poppler is unavailable
(e.g. a CI image without poppler-utils)."""

import shutil

import pytest
from PIL import Image

from pdf_converter import load_image

_HAS_POPPLER = shutil.which("pdftoppm") is not None


def test_load_image_returns_single_page(tmp_path):
    p = tmp_path / "receipt.png"
    Image.new("RGB", (120, 80), color=(255, 255, 255)).save(p, "PNG")

    img, pages = load_image(p)
    assert isinstance(img, Image.Image)
    assert img.mode == "RGB"
    assert pages == 1


@pytest.mark.skipif(not _HAS_POPPLER, reason="poppler (pdftoppm) not installed")
def test_load_image_pdf_reports_page_count(tmp_path):
    pytest.importorskip("pdf2image")
    p = tmp_path / "doc.pdf"
    page1 = Image.new("RGB", (200, 280), color=(255, 255, 255))
    page2 = Image.new("RGB", (200, 280), color=(255, 255, 255))
    page1.save(p, "PDF", save_all=True, append_images=[page2])

    img, pages = load_image(p)
    assert isinstance(img, Image.Image)
    assert img.mode == "RGB"
    assert pages == 2  # both pages counted; first page returned for OCR
