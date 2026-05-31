"""API tests for the vendor review endpoints (/vendors)."""

from datetime import datetime, timezone

import pytest


async def _seed_vendor(vendors, tenant_id="acme", needs_review=True, name="Walmart"):
    res = await vendors.insert_one(
        {
            "tenant_id": tenant_id,
            "canonical_name": name,
            "aliases": [name.upper()],
            "category_default": None,
            "needs_review": needs_review,
            "created_at": datetime.now(timezone.utc),
        }
    )
    return str(res.inserted_id)


@pytest.mark.asyncio
async def test_list_vendors_scoped_to_tenant(client, fake_collections):
    vendors = fake_collections["vendors"]
    await _seed_vendor(vendors, "acme", name="Walmart")
    await _seed_vendor(vendors, "globex", name="Target")

    resp = await client.get("/vendors/", headers={"X-Tenant-ID": "acme"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["canonical_name"] == "Walmart"


@pytest.mark.asyncio
async def test_list_vendors_needs_review_filter(client, fake_collections):
    vendors = fake_collections["vendors"]
    await _seed_vendor(vendors, "acme", needs_review=True, name="Walmart")
    await _seed_vendor(vendors, "acme", needs_review=False, name="Costco")

    resp = await client.get(
        "/vendors/", params={"needs_review": "true"}, headers={"X-Tenant-ID": "acme"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert {v["canonical_name"] for v in data} == {"Walmart"}


@pytest.mark.asyncio
async def test_confirm_vendor_marks_reviewed_and_adds_alias(client, fake_collections):
    vendors = fake_collections["vendors"]
    vid = await _seed_vendor(vendors, "acme", needs_review=True)

    resp = await client.post(
        f"/vendors/{vid}/confirm",
        json={"alias": "WM SUPERCENTER"},
        headers={"X-Tenant-ID": "acme"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "confirmed"

    from bson import ObjectId

    doc = await vendors.find_one({"_id": ObjectId(vid)})
    assert doc["needs_review"] is False
    assert "WM SUPERCENTER" in doc["aliases"]


@pytest.mark.asyncio
async def test_confirm_unknown_vendor_404(client, fake_collections):
    from bson import ObjectId

    resp = await client.post(
        f"/vendors/{ObjectId()}/confirm", headers={"X-Tenant-ID": "acme"}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_confirm_other_tenant_vendor_404(client, fake_collections):
    vendors = fake_collections["vendors"]
    vid = await _seed_vendor(vendors, "globex")
    # acme must not be able to confirm globex's vendor.
    resp = await client.post(
        f"/vendors/{vid}/confirm", headers={"X-Tenant-ID": "acme"}
    )
    assert resp.status_code == 404
