"""Lightweight receipt text parsing helpers.

Kept free of the EasyOCR/torch import so it can be used from the API process
(e.g. the /itemize endpoint) without loading model weights. OCR-specific,
bounding-box based logic stays in ocr_engine.py.
"""

import re
from typing import Any, Dict, List

# Lines whose description matches these are receipt totals/metadata, not products.
_NON_ITEM_KEYWORDS = (
    "total", "subtotal", "sub total", "tax", "balance", "due", "change",
    "cash", "card", "visa", "mastercard", "amex", "debit", "credit",
    "payment", "paid", "tip", "gratuity", "service charge", "amount",
    "auth", "approval", "account", "ref", "invoice", "receipt", "order",
)

# A line that is *only* a price, e.g. "3.49", "$5.25", "8 . 98", "1,50".
_PRICE_ONLY_RE = re.compile(r"^\$?\s*\d{1,5}\.\d{2}$")
_DATE_RE = re.compile(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}")


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
    if not re.search(r"[a-zA-Z]", text):  # must contain letters (skip pure ids)
        return False
    if _DATE_RE.search(text):
        return False
    return True


def find_line_items(text: str) -> List[Dict[str, Any]]:
    """Pair descriptive lines with the price line that follows them.

    OCR emits text top-to-bottom, so a product label is typically immediately
    followed by its price on the next line. Total/tax/metadata lines are
    filtered out so only purchasable products remain.
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

    return items
