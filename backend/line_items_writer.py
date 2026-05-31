"""Fan a parsed receipt's line items out into the `line_items` collection.

The Celery worker is synchronous (PyMongo), so this writer is synchronous too and
takes the worker's PyMongo database handle. The API has an async equivalent
(`write_line_items_async`) used by the on-demand `/itemize` endpoint so existing
receipts can be backfilled without re-uploading.

`find_line_items` emits ``{description, amount, qty}`` (no per-item ``unit_price``
or ``confidence``), so each item inherits the receipt-level confidence.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from vendor_normaliser import resolve_vendor_async, resolve_vendor_sync

logger = logging.getLogger(__name__)

# Items at or below this OCR confidence are flagged for human review.
REVIEW_CONFIDENCE_THRESHOLD = 0.75


def build_line_item_docs(
    receipt_doc: Dict[str, Any],
    items: List[Dict[str, Any]],
    tenant_id: str,
    vendor_id: Optional[str] = None,
    vendor_canonical: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Shape parsed items into line_items documents. Shared by sync + async writers.

    ``vendor_id``/``vendor_canonical`` come from vendor normalisation (resolved once
    per receipt by the caller) and are denormalised onto every item so analytics can
    group by canonical vendor without a join."""
    overall_conf = receipt_doc.get("confidence")
    period = receipt_doc.get("date")  # datetime or None; consumed by $dateToString
    now = datetime.now(timezone.utc)

    docs: List[Dict[str, Any]] = []
    for item in items:
        try:
            amount = float(item.get("amount") or 0.0)
        except (TypeError, ValueError):
            amount = 0.0
        conf = item.get("confidence", overall_conf)
        # Cast to native float: confidence may be a numpy scalar inherited from the
        # receipt, and numpy types (incl. the numpy.bool_ from `conf < threshold`)
        # are not BSON-encodable by PyMongo.
        if conf is not None:
            conf = float(conf)
        docs.append(
            {
                "tenant_id": tenant_id,
                "job_id": receipt_doc.get("job_id"),
                "receipt_id": receipt_doc.get("_id"),
                "vendor_raw": receipt_doc.get("merchant_name"),
                "vendor_id": vendor_id,
                "vendor_canonical": vendor_canonical or receipt_doc.get("merchant_name"),
                "description": item.get("description"),
                "quantity": float(item.get("qty") or 1),
                "unit_price": item.get("unit_price"),
                "amount": amount,
                "currency": receipt_doc.get("currency", "USD"),
                "category": receipt_doc.get("category", "Uncategorized"),
                "period": period,
                "confidence": conf,
                "needs_review": bool(
                    conf is not None and conf < REVIEW_CONFIDENCE_THRESHOLD
                ),
                "created_at": now,
            }
        )
    return docs


def write_line_items(
    db, receipt_doc: Dict[str, Any], items: List[Dict[str, Any]], tenant_id: str
) -> int:
    """Synchronous (PyMongo) writer for the Celery worker. Returns docs written."""
    if not items:
        return 0
    # Resolve the vendor once per receipt; never fail item writing on a vendor error.
    vendor_id = vendor_canonical = None
    try:
        resolved = resolve_vendor_sync(db, receipt_doc.get("merchant_name"), tenant_id)
        if resolved:
            vendor_id, vendor_canonical = resolved
    except Exception:
        logger.exception("vendor resolve (sync) failed tenant=%s", tenant_id)
    docs = build_line_item_docs(
        receipt_doc, items, tenant_id, vendor_id, vendor_canonical
    )
    result = db["line_items"].insert_many(docs)
    count = len(result.inserted_ids)
    logger.info(
        "line_items written count=%d job_id=%s", count, receipt_doc.get("job_id")
    )
    return count


async def write_line_items_async(
    collection, receipt_doc: Dict[str, Any], items: List[Dict[str, Any]], tenant_id: str
) -> int:
    """Async (Motor) writer for the API. Replaces any existing rows for the receipt
    so re-itemizing a receipt stays idempotent. Returns docs written."""
    receipt_id = receipt_doc.get("_id")
    if receipt_id is not None:
        await collection.delete_many(
            {"tenant_id": tenant_id, "receipt_id": receipt_id}
        )
    if not items:
        return 0
    # Resolve the vendor once per receipt; never fail item writing on a vendor error.
    vendor_id = vendor_canonical = None
    try:
        import database

        resolved = await resolve_vendor_async(
            database.collection_vendors, receipt_doc.get("merchant_name"), tenant_id
        )
        if resolved:
            vendor_id, vendor_canonical = resolved
    except Exception:
        logger.exception("vendor resolve (async) failed tenant=%s", tenant_id)
    docs = build_line_item_docs(
        receipt_doc, items, tenant_id, vendor_id, vendor_canonical
    )
    result = await collection.insert_many(docs)
    count = len(result.inserted_ids)
    logger.info(
        "line_items rewritten count=%d receipt_id=%s", count, receipt_id
    )
    return count
