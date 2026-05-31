"""Load an uploaded source file into a PIL image for the OCR pipeline.

Images are opened directly; PDFs are rasterised via ``pdf2image`` (poppler).
Only the first page is OCR'd for now, but the total page count is returned so
the job document can record it. ``pdf2image`` is imported lazily so the module
(and the API process) can be imported without poppler present.
"""

import logging
from pathlib import Path
from typing import Tuple, Union

from PIL import Image

logger = logging.getLogger(__name__)

# Render PDFs at a resolution high enough for OCR without ballooning memory.
PDF_RENDER_DPI = 300


def load_image(file_path: Union[str, Path]) -> Tuple[Image.Image, int]:
    """Return ``(first_page_image, page_count)`` for an image or PDF path.

    For images, ``page_count`` is always 1. For PDFs, only the first page is
    returned but the true page count is reported.
    """
    path = Path(file_path)
    if path.suffix.lower() == ".pdf":
        pages = _pdf_to_images(path)
        if not pages:
            raise ValueError("PDF contained no rasterisable pages")
        return pages[0], len(pages)
    return Image.open(path).convert("RGB"), 1


def _pdf_to_images(path: Path, dpi: int = PDF_RENDER_DPI) -> list:
    """Rasterise a PDF to a list of RGB PIL images (one per page)."""
    from pdf2image import convert_from_path  # lazy: requires poppler at runtime

    pages = convert_from_path(str(path), dpi=dpi)
    logger.info("PDF rasterised pages=%d path=%s", len(pages), path.name)
    return [p.convert("RGB") for p in pages]
