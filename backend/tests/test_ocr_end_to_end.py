"""Opt-in end-to-end OCR test against a generated fixture image.

This runs the real EasyOCR engine, which downloads ~100MB of model weights on
first use, so it is skipped unless RUN_OCR=1 is set:

    RUN_OCR=1 pytest backend/tests/test_ocr_end_to_end.py -v
"""

import os
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).resolve().parent.parent.parent / "test_fixtures"

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_OCR") != "1",
    reason="Set RUN_OCR=1 to run the heavy EasyOCR end-to-end test.",
)


def test_walmart_receipt_extraction():
    import ocr_engine

    image_path = FIXTURE_DIR / "receipt_walmart.jpg"
    assert image_path.exists(), (
        "Run `python test_fixtures/generate_fixtures.py` to create fixtures first."
    )

    ocr_result = ocr_engine.extract_text_and_coords(image_path.read_bytes())
    parsed = ocr_engine.parse_receipt(ocr_result)

    # We assert on robust signals rather than exact OCR output.
    assert "WALMART" in parsed["raw_text"].upper()
    assert parsed.get("total_amount") == pytest.approx(47.83, abs=0.5)
    assert parsed["category"] == "Groceries"
