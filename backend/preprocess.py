"""OpenCV pre-processing to improve EasyOCR accuracy on real-world receipts.

Runs in the worker only (CPU-only, adds latency, never on the API request path).
Every stage is defensive: a failure in any single step falls back to the input of
that step, and a total failure returns the original image, so pre-processing can
never fail an OCR job — at worst it is a no-op.

Pipeline: deskew (Hough) -> denoise (fast NL means) -> contrast (CLAHE in LAB).
"""

import logging

import cv2
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# Denoising is the slow stage and scales with pixel count. Cap the long edge so a
# high-DPI scan/photo doesn't make a single receipt take many seconds.
MAX_EDGE_FOR_DENOISE = 2000


def preprocess_receipt(pil_image: Image.Image) -> Image.Image:
    """Apply deskew, denoise, and contrast normalisation. Returns a PIL image.

    Falls back to the original image on any error.
    """
    try:
        img = np.array(pil_image.convert("RGB"))
    except Exception:
        logger.exception("preprocess: could not read image; returning original")
        return pil_image

    img = _safe(_deskew, img, "deskew")
    img = _safe(_denoise, img, "denoise")
    img = _safe(_normalise_contrast, img, "contrast")

    try:
        return Image.fromarray(img)
    except Exception:
        logger.exception("preprocess: could not rebuild image; returning original")
        return pil_image


def _safe(fn, img: np.ndarray, name: str) -> np.ndarray:
    """Run a stage, returning its input unchanged if it raises."""
    try:
        return fn(img)
    except Exception:
        logger.exception("preprocess stage failed stage=%s", name)
        return img


def _deskew(img: np.ndarray) -> np.ndarray:
    """Detect and correct small page rotation using the Hough line transform."""
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    lines = cv2.HoughLinesP(
        edges, 1, np.pi / 180, threshold=100, minLineLength=100, maxLineGap=10
    )
    if lines is None:
        return img

    angles = []
    for x1, y1, x2, y2 in lines[:, 0]:
        angle = np.degrees(np.arctan2(y2 - y1, x2 - x1))
        if abs(angle) < 45:
            angles.append(angle)

    if not angles:
        return img

    median_angle = float(np.median(angles))
    if abs(median_angle) < 0.5:  # already straight enough; don't soften the image
        return img

    h, w = img.shape[:2]
    matrix = cv2.getRotationMatrix2D((w // 2, h // 2), median_angle, 1.0)
    return cv2.warpAffine(
        img, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )


def _denoise(img: np.ndarray) -> np.ndarray:
    """Remove sensor/scan noise while preserving text edges.

    Downscales oversized images first so this stage stays bounded in time.
    """
    h, w = img.shape[:2]
    long_edge = max(h, w)
    if long_edge > MAX_EDGE_FOR_DENOISE:
        scale = MAX_EDGE_FOR_DENOISE / long_edge
        img = cv2.resize(
            img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA
        )
    return cv2.fastNlMeansDenoisingColored(img, None, 10, 10, 7, 21)


def _normalise_contrast(img: np.ndarray) -> np.ndarray:
    """Apply CLAHE contrast enhancement on the luminance channel (LAB space)."""
    lab = cv2.cvtColor(img, cv2.COLOR_RGB2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l_channel = clahe.apply(l_channel)
    merged = cv2.merge([l_channel, a_channel, b_channel])
    return cv2.cvtColor(merged, cv2.COLOR_LAB2RGB)
