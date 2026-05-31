"""Unit tests for vendor_normaliser.

The pure matcher and the sync resolver are exercised against synthetic data /
mongomock; the async resolver runs against mongomock-motor.
"""

import mongomock
import pytest
from bson import ObjectId

import vendor_normaliser as vn


def test_normalise_name_collapses_and_uppercases():
    assert vn.normalise_name("  Wal-Mart   Supercenter ") == "WAL-MART SUPERCENTER"
    assert vn.normalise_name("") is None
    assert vn.normalise_name(None) is None


def test_best_match_matches_close_variant():
    pairs = [("WALMART", "v1", "Walmart"), ("STARBUCKS", "v2", "Starbucks")]
    # "WALMART SUPERCENTER" should token-sort-match the WALMART alias.
    assert vn.best_match("WALMART SUPERCENTER", pairs) == ("v1", "Walmart")


def test_best_match_returns_none_below_threshold():
    pairs = [("WALMART", "v1", "Walmart")]
    assert vn.best_match("TARGET", pairs) is None


def test_best_match_empty_inputs():
    assert vn.best_match("", []) is None
    assert vn.best_match("WALMART", []) is None


def test_resolve_sync_creates_then_reuses_vendor():
    db = mongomock.MongoClient()["expense_intelligence"]

    first = vn.resolve_vendor_sync(db, "WALMART SUPERCENTER", "acme")
    assert first is not None
    vendor_id, canonical = first
    assert canonical == "WALMART SUPERCENTER"
    # A new, unseen vendor is flagged for review.
    created = db["vendors"].find_one({"_id": ObjectId(vendor_id)})
    assert created["needs_review"] is True
    assert db["vendors"].count_documents({"tenant_id": "acme"}) == 1

    # A close variant resolves to the SAME vendor — no duplicate created.
    second = vn.resolve_vendor_sync(db, "Walmart #4821", "acme")
    assert second[0] == vendor_id
    assert db["vendors"].count_documents({"tenant_id": "acme"}) == 1


def test_resolve_sync_is_tenant_scoped():
    db = mongomock.MongoClient()["expense_intelligence"]
    a = vn.resolve_vendor_sync(db, "WALMART", "acme")
    b = vn.resolve_vendor_sync(db, "WALMART", "globex")
    assert a[0] != b[0]
    assert db["vendors"].count_documents({}) == 2


def test_resolve_sync_none_for_blank_name():
    db = mongomock.MongoClient()["expense_intelligence"]
    assert vn.resolve_vendor_sync(db, "  ", "acme") is None
    assert db["vendors"].count_documents({}) == 0


@pytest.mark.asyncio
async def test_resolve_async_creates_then_reuses():
    from mongomock_motor import AsyncMongoMockClient

    vendors = AsyncMongoMockClient()["expense_intelligence"]["vendors"]
    first = await vn.resolve_vendor_async(vendors, "STARBUCKS COFFEE", "acme")
    assert first is not None
    again = await vn.resolve_vendor_async(vendors, "Starbucks", "acme")
    assert again[0] == first[0]
    assert await vendors.count_documents({"tenant_id": "acme"}) == 1


@pytest.mark.asyncio
async def test_confirm_vendor_alias_marks_reviewed_and_adds_alias():
    from mongomock_motor import AsyncMongoMockClient

    vendors = AsyncMongoMockClient()["expense_intelligence"]["vendors"]
    vid, _ = await vn.resolve_vendor_async(vendors, "WALMART", "acme")

    ok = await vn.confirm_vendor_alias(vendors, vid, "acme", new_alias="WM SUPERCENTER")
    assert ok is True
    doc = await vendors.find_one({"_id": ObjectId(vid)})
    assert doc["needs_review"] is False
    assert "WM SUPERCENTER" in doc["aliases"]


@pytest.mark.asyncio
async def test_confirm_vendor_alias_rejects_other_tenant():
    from mongomock_motor import AsyncMongoMockClient

    vendors = AsyncMongoMockClient()["expense_intelligence"]["vendors"]
    vid, _ = await vn.resolve_vendor_async(vendors, "WALMART", "acme")
    # Wrong tenant must not be able to confirm someone else's vendor.
    assert await vn.confirm_vendor_alias(vendors, vid, "globex") is False


@pytest.mark.asyncio
async def test_confirm_vendor_alias_bad_id():
    from mongomock_motor import AsyncMongoMockClient

    vendors = AsyncMongoMockClient()["expense_intelligence"]["vendors"]
    assert await vn.confirm_vendor_alias(vendors, "not-an-objectid", "acme") is False
