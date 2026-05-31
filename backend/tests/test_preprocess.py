"""Unit tests for the OpenCV pre-processing pipeline.

These run on small synthetic PIL images (no EasyOCR involved) and only require
opencv-python-headless, which is already a project dependency.
"""

from PIL import Image, ImageDraw

from preprocess import _denoise, preprocess_receipt


def _receipt_like(width=400, height=200, angle=0.0) -> Image.Image:
    img = Image.new("RGB", (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.text((40, 80), "WALMART", fill=(0, 0, 0))
    draw.text((40, 120), "TOTAL  $47.83", fill=(0, 0, 0))
    if angle:
        img = img.rotate(angle, expand=False, fillcolor=(255, 255, 255))
    return img


def test_preprocess_returns_pil_image():
    result = preprocess_receipt(_receipt_like())
    assert isinstance(result, Image.Image)


def test_preprocess_preserves_mode_rgb():
    result = preprocess_receipt(_receipt_like(angle=4.0))
    assert result.mode == "RGB"


def test_preprocess_clean_image_does_not_crash():
    clean = Image.new("RGB", (300, 150), color=(255, 255, 255))
    assert preprocess_receipt(clean) is not None


def test_preprocess_handles_grayscale_input():
    gray = Image.new("L", (300, 150), color=200)
    result = preprocess_receipt(gray)
    assert isinstance(result, Image.Image)
    assert result.mode == "RGB"


def test_denoise_downscales_oversized_image():
    big = Image.new("RGB", (4000, 1000), color=(255, 255, 255))
    import numpy as np

    out = _denoise(np.array(big))
    # Long edge clamped to MAX_EDGE_FOR_DENOISE (2000).
    assert max(out.shape[:2]) <= 2000
