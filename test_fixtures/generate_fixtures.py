"""Generate deterministic synthetic receipt images for local testing.

The OCR pipeline (`backend/ocr_engine.py`) is the heart of this project, but the
repository ships no sample receipts, so none of the smoke tests in
`LOCAL_TESTING.md` can run out of the box. This script renders a handful of
legible receipts (one per expense category) plus an intentionally degraded one
so contributors can exercise upload -> OCR -> categorisation end to end.

Usage:
    python test_fixtures/generate_fixtures.py

Re-running is safe and idempotent; images are overwritten in place.
"""

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FIXTURE_DIR = Path(__file__).resolve().parent

_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for candidate in _FONT_CANDIDATES:
        if os.path.exists(candidate):
            return ImageFont.truetype(candidate, size)
    # Fallback keeps the script runnable even without DejaVu installed,
    # though OCR accuracy on the bitmap default font will be poor.
    return ImageFont.load_default()


def _render_receipt(lines: list[str], width: int = 480, line_height: int = 38) -> Image.Image:
    """Render receipt text as black-on-white, sized to fit the content."""
    font = _load_font(28)
    top_pad, bottom_pad, left_pad = 30, 30, 30
    height = top_pad + bottom_pad + line_height * len(lines)
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    y = top_pad
    for line in lines:
        draw.text((left_pad, y), line, fill="black", font=font)
        y += line_height
    return img


# Each fixture maps to a category exercised by classify_receipt() in ocr_engine.py.
RECEIPTS: dict[str, list[str]] = {
    "receipt_walmart.jpg": [
        "WALMART",
        "Supercenter #1234",
        "04/15/2026",
        "Eggs        3.49",
        "Milk        2.99",
        "Bread       2.50",
        "------------------",
        "SUBTOTAL    8.98",
        "TAX         0.85",
        "TOTAL      47.83",
    ],
    "receipt_starbucks.jpg": [
        "STARBUCKS COFFEE",
        "Store 0456",
        "04/18/2026",
        "Latte       5.25",
        "Muffin      1.50",
        "------------------",
        "TOTAL       6.75",
    ],
    "receipt_shell.jpg": [
        "SHELL",
        "Station 88",
        "04/20/2026",
        "Fuel Unleaded",
        "Gallons    12.5",
        "------------------",
        "TOTAL      52.40",
    ],
}


def main() -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    generated: list[str] = []

    for filename, lines in RECEIPTS.items():
        img = _render_receipt(lines)
        out = FIXTURE_DIR / filename
        img.save(out, quality=95)
        generated.append(out.name)

    # Intentionally poor-quality scan for the quality-classifier path.
    blurry = _render_receipt(RECEIPTS["receipt_walmart.jpg"])
    blurry = blurry.filter(ImageFilter.GaussianBlur(radius=2.2))
    blurry_out = FIXTURE_DIR / "receipt_blurry.jpg"
    blurry.save(blurry_out, quality=40)
    generated.append(blurry_out.name)

    print("Generated fixtures in", FIXTURE_DIR)
    for name in generated:
        print("  -", name)


if __name__ == "__main__":
    main()
