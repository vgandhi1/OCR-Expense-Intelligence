"""Unit tests for line_items_writer.

The synchronous writer is what the Celery worker calls, so it is tested against a
synchronous in-memory mongomock client (not the async motor mock).
"""

from datetime import datetime, timezone

import mongomock
import pytest
from bson import ObjectId

from line_items_writer import build_line_item_docs, write_line_items


def _receipt(**overrides):
    doc = {
        "_id": ObjectId(),
        "job_id": ObjectId(),
        "merchant_name": "Walmart",
        "category": "Groceries",
        "currency": "USD",
        "confidence": 0.91,
        "date": datetime(2026, 4, 15, tzinfo=timezone.utc),
    }
    doc.update(overrides)
    return doc


def test_build_docs_inherits_receipt_fields():
    receipt = _receipt()
    items = [{"description": "Eggs", "amount": 3.49, "qty": 1}]
    docs = build_line_item_docs(receipt, items, "acme")

    assert len(docs) == 1
    d = docs[0]
    assert d["tenant_id"] == "acme"
    assert d["receipt_id"] == receipt["_id"]
    assert d["job_id"] == receipt["job_id"]
    assert d["vendor_raw"] == "Walmart"
    assert d["category"] == "Groceries"
    assert d["currency"] == "USD"
    assert d["amount"] == 3.49
    assert d["quantity"] == 1.0
    assert d["period"] == receipt["date"]
    assert d["confidence"] == 0.91
    assert d["needs_review"] is False
    # Without an explicit vendor, vendor_canonical falls back to the raw merchant.
    assert d["vendor_id"] is None
    assert d["vendor_canonical"] == "Walmart"


def test_build_docs_sets_resolved_vendor_fields():
    receipt = _receipt()
    docs = build_line_item_docs(
        receipt,
        [{"description": "Eggs", "amount": 3.49}],
        "acme",
        vendor_id="v123",
        vendor_canonical="Walmart",
    )
    assert docs[0]["vendor_id"] == "v123"
    assert docs[0]["vendor_canonical"] == "Walmart"


def test_build_docs_flags_low_confidence_for_review():
    receipt = _receipt(confidence=0.5)
    docs = build_line_item_docs(receipt, [{"description": "X", "amount": 1.0}], "acme")
    assert docs[0]["needs_review"] is True


def test_build_docs_defaults_quantity_and_handles_bad_amount():
    receipt = _receipt()
    docs = build_line_item_docs(
        receipt, [{"description": "Mystery", "amount": None}], "acme"
    )
    assert docs[0]["quantity"] == 1.0
    assert docs[0]["amount"] == 0.0


def test_write_line_items_persists_to_collection():
    db = mongomock.MongoClient()["expense_intelligence"]
    receipt = _receipt()
    items = [
        {"description": "Eggs", "amount": 3.49, "qty": 1},
        {"description": "Milk", "amount": 2.99, "qty": 2},
    ]
    count = write_line_items(db, receipt, items, "acme")

    assert count == 2
    stored = list(db["line_items"].find({"tenant_id": "acme"}))
    assert len(stored) == 2
    assert {s["description"] for s in stored} == {"Eggs", "Milk"}
    # Vendor normalisation ran: a vendor was created and stamped on each line item.
    assert db["vendors"].count_documents({"tenant_id": "acme"}) == 1
    assert all(s["vendor_id"] is not None for s in stored)
    assert all(s["vendor_canonical"] == "Walmart" for s in stored)


def test_write_line_items_empty_is_noop():
    db = mongomock.MongoClient()["expense_intelligence"]
    assert write_line_items(db, _receipt(), [], "acme") == 0
    assert db["line_items"].count_documents({}) == 0
