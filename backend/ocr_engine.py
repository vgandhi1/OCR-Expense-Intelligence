import logging
import easyocr
import numbers
import re
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
import numpy as np
from PIL import Image
import io

from receipt_parsing import find_line_items

logger = logging.getLogger(__name__)

# Lazily initialise the reader. Building it eagerly at import time downloads
# model weights and loads torch, which makes the module impossible to import in
# unit tests and slows every worker boot. Defer it until the first OCR call.
_reader = None


def get_reader() -> "easyocr.Reader":
    global _reader
    if _reader is None:
        # gpu=False: deployment is CPU-only (no CUDA in the containers). This skips
        # EasyOCR's GPU probe and the "Using CPU" warning on every worker boot.
        _reader = easyocr.Reader(['en'], gpu=False)
    return _reader

def extract_text_and_coords(image_bytes: bytes) -> List[Tuple[List[List[int]], str, float]]:
    """
    Returns list of (bbox, text, prob).
    bbox = [[x1, y1], [x2, y2], [x3, y3], [x4, y4]]
    """
    image = Image.open(io.BytesIO(image_bytes))
    return extract_text_and_coords_from_image(image)


def extract_text_and_coords_from_image(
    image: "Image.Image",
) -> List[Tuple[List[List[int]], str, float]]:
    """OCR a PIL image. Lets the worker pre-process (deskew/denoise) and convert
    PDFs to images before extraction without re-encoding to bytes."""
    image_np = np.array(image)
    # detail=1 gives extracted text with bounding box and confidence
    result = get_reader().readtext(image_np, detail=1)
    logger.debug("OCR extracted %d text regions", len(result))
    return result

def parse_receipt(ocr_result: List[Tuple[List[List[int]], str, float]]) -> Dict[str, Any]:
    data = {}
    
    # Flatten text for simple regex checks (Date, Merchant)
    full_text = "\n".join([res[1] for res in ocr_result])
    lower_text = full_text.lower()
    
    # 1. Total Amount via Geometry
    # Look for "Total" label, then find numbers on the same horizontal line (similar Y coordinates)
    total_amount = find_total_geometric(ocr_result)
    if total_amount:
        data['total_amount'] = total_amount
    else:
        # Fallback to regex on full text if geometry fails
        pass # The geometric finder has its own internal fallback logic or we can add one here

    # 2. Date (Regex on full text is usually fine)
    date_match = re.search(r'(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})', full_text)
    if date_match:
        date_str = date_match.group(1)
        for fmt in ["%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y", "%Y-%m-%d"]:
            try:
                data['date'] = datetime.strptime(date_str, fmt)
                break
            except ValueError:
                continue

    # 3. Merchant Name (Topmost text line usually)
    # Filter for lines with reasonable confidence and length
    valid_lines = [res[1] for res in ocr_result if len(res[1].strip()) > 1]
    if valid_lines:
        data['merchant_name'] = valid_lines[0]

    # 4. Categorization
    data['category'] = classify_receipt(data.get('merchant_name', ''), lower_text)

    data["raw_text"] = full_text
    data["items"] = find_line_items(full_text, data.get("total_amount"))
    data["confidence"] = extract_confidence(ocr_result)
    data["currency"] = detect_currency(full_text)
    logger.debug(
        "Parsed receipt fields: merchant_set=%s total_set=%s date_set=%s conf=%s",
        bool(data.get("merchant_name")),
        data.get("total_amount") is not None,
        bool(data.get("date")),
        data.get("confidence"),
    )
    return data


# Map a currency symbol seen in OCR text to an ISO 4217 code. First match wins.
_CURRENCY_BY_SYMBOL = (
    ("$", "USD"),
    ("€", "EUR"),
    ("£", "GBP"),
    ("¥", "JPY"),
    ("₹", "INR"),
)


def extract_confidence(
    ocr_result: List[Tuple[List[List[int]], str, float]]
) -> Optional[float]:
    """Mean of per-token EasyOCR probabilities; None when nothing was read."""
    # Use numbers.Real so numpy float32/float64 (not subclasses of `float`) are
    # included rather than silently dropped.
    probs = [p for _, _, p in ocr_result if isinstance(p, numbers.Real)]
    if not probs:
        return None
    # Cast to a native float: EasyOCR probs are numpy scalars, and numpy types
    # (incl. the numpy.bool_ from comparisons) are not BSON-encodable by PyMongo.
    return round(float(sum(probs) / len(probs)), 4)


def detect_currency(text: str) -> str:
    """Best-effort currency detection from symbols in the OCR text. Defaults to USD."""
    for symbol, code in _CURRENCY_BY_SYMBOL:
        if symbol in text:
            return code
    return "USD"

def find_total_geometric(ocr_result: List[Tuple[List[List[int]], str, float]]) -> float:
    """
    Finds 'Total' label and looks for number to the right of it.
    """
    # Keywords indicating a total label
    total_labels = ["total", "subtotal", "balance", "due", "amount", "ota", "sbtotal"]
    
    potential_totals = []

    for i, (bbox, text, prob) in enumerate(ocr_result):
        clean_text = text.lower().replace(':', '').replace('.', '').strip()
        
        if any(label in clean_text for label in total_labels):
            # This is a label candidate. Get its Y coordinates.
            # bbox is typically [[tl], [tr], [br], [bl]]
            # Y range is roughly min_y to max_y
            y_min = min(p[1] for p in bbox)
            y_max = max(p[1] for p in bbox)
            y_center = (y_min + y_max) / 2
            height = y_max - y_min

            # Look for other blocks that are loosely on the same line
            # Tolerance: Center of candidate is within the vertical range of the label
            for j, (v_bbox, v_text, v_prob) in enumerate(ocr_result):
                if i == j: continue # Skip self
                
                # Check if it looks like a price
                # Regex for price: optional $, digits, dot, two digits
                # Also handle spaces "9 . 99"
                v_clean = re.sub(r'(\d)\s*\.\s*(\d)', r'\1.\2', v_text)
                price_match = re.search(r'[$]?\s*(\d{1,5}(?:[.,]\d{2}))', v_clean)
                
                if price_match:
                    # Check geometry
                    v_y_min = min(p[1] for p in v_bbox)
                    v_y_max = max(p[1] for p in v_bbox)
                    v_y_center = (v_y_min + v_y_max) / 2
                    
                    # Overlap check
                    if abs(v_y_center - y_center) < (height * 0.8): # Within 80% line height
                        # It's on the same line!
                        try:
                            val_str = price_match.group(1).replace(',', '.') # Normalize comma
                            val = float(val_str)
                            potential_totals.append(val)
                        except ValueError:
                            continue

    if potential_totals:
        # If multiple matches, max is usually the safest bet for "Total" (vs tax, subtotal)
        return max(potential_totals)
    
    return None

def classify_receipt(merchant: str, text: str) -> str:
    merchant = merchant.lower()
    text = text.lower()
    
    categories = {
        "Groceries": ["walmart", "kroger", "safeway", "publix", "whole foods", "trader joe", "aldi", "food", "market", "eggs", "milk", "bread"],
        "Dining": ["restaurant", "cafe", "coffee", "starbucks", "mcdonald", "burger", "pizza", "taco", "grill", "bar", "bistro", "diner"],
        "Transport": ["shell", "bp", "exxon", "chevron", "arco", "gas", "fuel", "uber", "lyft", "taxi", "parking"],
        "Shopping": ["amazon", "target", "costco", "best buy", "clothing", "shoe", "mall", "department store"],
        "Utilities": ["electric", "water", "gas", "internet", "comcast", "att", "verizon", "t-mobile"],
        "Entertainment": ["movie", "cinema", "theatre", "netflix", "hulu", "spotify", "ticket", "event"],
        "Health": ["pharmacy", "cvs", "walgreens", "doctor", "hospital", "clinic", "dental", "medical"]
    }
    
    # 1. Check merchant name first (high confidence)
    for category, keywords in categories.items():
        if any(k in merchant for k in keywords):
            return category
            
    # 2. Check full text content (medium confidence)
    for category, keywords in categories.items():
        if any(k in text for k in keywords):
            return category
            
    return "Uncategorized"
