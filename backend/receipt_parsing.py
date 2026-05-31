"""Lightweight receipt text parsing helpers.

Kept free of the EasyOCR/torch import so it can be used from the API process
(e.g. the /itemize endpoint) without loading model weights. OCR-specific,
bounding-box based logic stays in ocr_engine.py.
"""

import re
from typing import Any, Dict, List, Optional

# Lines whose description matches these are receipt totals/metadata, not products.
_NON_ITEM_KEYWORDS = (
    "total", "subtotal", "sub total", "tax", "balance", "due", "change",
    "cash", "card", "visa", "mastercard", "amex", "debit", "credit",
    "payment", "paid", "tip", "gratuity", "service charge", "amount",
    "auth", "approval", "account", "ref", "invoice", "receipt", "order",
)

# OCR frequently mangles these metadata labels (e.g. "Tota] ;" -> "tota"), so a
# short alphabetic prefix of one of these is also treated as a non-product line.
_NON_ITEM_PREFIXES = ("total", "subtotal", "balance")

# A line that is *only* a price, e.g. "3.49", "$5.25", "8 . 98", "1,50".
_PRICE_ONLY_RE = re.compile(r"^\$?\s*\d{1,5}\.\d{2}$")
_DATE_RE = re.compile(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}")

# Tolerance (in currency units) when comparing an item amount to the receipt total.
_TOTAL_TOLERANCE = 0.011


def _price_from_line(line: str):
    """Return a float if the line is purely a price token, else None."""
    # Normalise spaced/comma decimals: "8 . 98" / "1,50" -> "8.98" / "1.50".
    norm = re.sub(r"(\d)\s*[.,]\s*(\d)", r"\1.\2", line.strip())
    if not _PRICE_ONLY_RE.match(norm):
        return None
    try:
        return round(float(norm.lstrip("$").strip()), 2)
    except ValueError:
        return None


def _is_item_description(text: str) -> bool:
    low = text.lower().strip()
    if not low:
        return False
    if any(kw in low for kw in _NON_ITEM_KEYWORDS):
        return False
    letters = re.sub(r"[^a-z]", "", low)
    if len(letters) < 2:  # skip single-char / pure-id garbage like "F" or "lb."
        return False
    # Catch OCR-mangled metadata labels: "tota" is a prefix of "total", etc.
    # Require >=4 letters so real short items (e.g. "Sub") aren't dropped.
    if len(letters) >= 4 and any(kw.startswith(letters) for kw in _NON_ITEM_PREFIXES):
        return False
    if _DATE_RE.search(text):
        return False
    return True


def _filter_against_total(
    items: List[Dict[str, Any]], total: Optional[float]
) -> List[Dict[str, Any]]:
    """Drop pseudo-items that are really the receipt total.

    A genuine single line item can never exceed the grand total, and when other
    items are present it can't *equal* it either (the total is their sum plus
    tax). Such rows are almost always the total/subtotal line leaking through
    OCR noise, so we discard them — otherwise they'd be flagged "most expensive".
    """
    if not items or total is None:
        return items
    try:
        total_val = float(total)
    except (TypeError, ValueError):
        return items
    if total_val <= 0:
        return items

    multi = len(items) > 1
    kept: List[Dict[str, Any]] = []
    for item in items:
        amount = item.get("amount")
        if amount is None:
            kept.append(item)
            continue
        if amount > total_val + _TOTAL_TOLERANCE:
            continue  # impossible for a real product line
        if multi and abs(amount - total_val) <= _TOTAL_TOLERANCE:
            continue  # the grand total leaking in among real items
        kept.append(item)
    return kept


def find_line_items(text: str, total: Optional[float] = None) -> List[Dict[str, Any]]:
    """Pair descriptive lines with the price line that follows them.

    OCR emits text top-to-bottom, so a product label is typically immediately
    followed by its price on the next line. Total/tax/metadata lines are
    filtered out so only purchasable products remain. When the receipt ``total``
    is known, items at/above it are dropped as leaked totals.
    """
    lines = [ln.strip() for ln in (text or "").split("\n") if ln.strip()]
    items: List[Dict[str, Any]] = []
    pending = None  # most recent non-price (candidate description) line

    for line in lines:
        price = _price_from_line(line)
        if price is not None:
            if pending and _is_item_description(pending):
                items.append({"description": pending[:200], "amount": price, "qty": 1})
            pending = None
        else:
            pending = line

    return _filter_against_total(items, total)
