"""Unit tests for the parsing/categorisation logic in ocr_engine.

These exercise the pure functions using synthetic OCR output, so they do not
require EasyOCR model weights. The full image->text path is covered separately
by the (opt-in) test in test_ocr_end_to_end.py.
"""

import pytest

import ocr_engine


def _box(x_min, y_min, x_max, y_max):
    return [[x_min, y_min], [x_max, y_min], [x_max, y_max], [x_min, y_max]]


def test_classify_receipt_by_merchant():
    assert ocr_engine.classify_receipt("Walmart Supercenter", "") == "Groceries"
    assert ocr_engine.classify_receipt("Starbucks", "") == "Dining"
    assert ocr_engine.classify_receipt("Shell", "") == "Transport"
    assert ocr_engine.classify_receipt("Amazon", "") == "Shopping"


def test_classify_receipt_falls_back_to_text():
    assert ocr_engine.classify_receipt("Unknown Store", "fresh milk and bread") == "Groceries"


def test_classify_receipt_uncategorized():
    assert ocr_engine.classify_receipt("Mystery LLC", "widget purchase") == "Uncategorized"


def test_find_total_geometric_picks_largest_on_line():
    ocr_result = [
        (_box(10, 300, 80, 320), "SUBTOTAL", 0.9),
        (_box(200, 300, 260, 320), "8.98", 0.9),
        (_box(10, 340, 80, 360), "TOTAL", 0.9),
        (_box(200, 340, 260, 360), "47.83", 0.9),
    ]
    assert ocr_engine.find_total_geometric(ocr_result) == 47.83


def test_parse_receipt_extracts_fields():
    ocr_result = [
        (_box(10, 5, 200, 30), "WALMART", 0.95),
        (_box(10, 50, 200, 70), "04/15/2026", 0.9),
        (_box(10, 340, 80, 360), "TOTAL", 0.9),
        (_box(200, 340, 260, 360), "47.83", 0.9),
    ]
    parsed = ocr_engine.parse_receipt(ocr_result)

    assert parsed["merchant_name"] == "WALMART"
    assert parsed["total_amount"] == 47.83
    assert parsed["date"].year == 2026 and parsed["date"].month == 4 and parsed["date"].day == 15
    assert parsed["category"] == "Groceries"
    assert "WALMART" in parsed["raw_text"]
    assert parsed["items"] == []
    assert parsed["confidence"] == pytest.approx(0.9125)
    assert parsed["currency"] == "USD"


def test_extract_confidence_averages_token_probs():
    ocr_result = [
        (_box(0, 0, 1, 1), "A", 0.8),
        (_box(0, 0, 1, 1), "B", 1.0),
    ]
    assert ocr_engine.extract_confidence(ocr_result) == pytest.approx(0.9)


def test_extract_confidence_none_when_empty():
    assert ocr_engine.extract_confidence([]) is None


def test_extract_confidence_returns_native_float_for_numpy_probs():
    # EasyOCR emits numpy.float32/64 probabilities. The result must be a native
    # Python float so PyMongo can BSON-encode it (numpy scalars cannot be encoded,
    # and `numpy_float < threshold` yields a non-encodable numpy.bool_).
    import numpy as np

    ocr_result = [
        (_box(0, 0, 1, 1), "A", np.float64(0.8)),
        (_box(0, 0, 1, 1), "B", np.float32(1.0)),
    ]
    conf = ocr_engine.extract_confidence(ocr_result)
    assert conf == pytest.approx(0.9)
    assert type(conf) is float


def test_detect_currency_from_symbol():
    assert ocr_engine.detect_currency("TOTAL £12.50") == "GBP"
    assert ocr_engine.detect_currency("TOTAL €9,99") == "EUR"
    assert ocr_engine.detect_currency("TOTAL ¥1200") == "JPY"
    assert ocr_engine.detect_currency("TOTAL 12.50") == "USD"  # default
