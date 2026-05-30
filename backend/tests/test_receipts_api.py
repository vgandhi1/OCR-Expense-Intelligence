"""API tests for the receipts router (upload, job status, tenancy)."""

from datetime import datetime, timezone

import pytest
from bson import ObjectId

pytestmark = pytest.mark.asyncio


async def _seed_receipt(
    receipts, tenant_id="default", merchant="Walmart", total=47.83, raw_text=None
):
    oid = ObjectId()
    doc = {
        "_id": oid,
        "tenant_id": tenant_id,
        "merchant_name": merchant,
        "total_amount": total,
        "category": "Groceries",
        "date": datetime(2026, 4, 15, tzinfo=timezone.utc),
        "created_at": datetime.now(timezone.utc),
        "items": [],
    }
    if raw_text is not None:
        doc["raw_text"] = raw_text
    await receipts.insert_one(doc)
    return str(oid)


_WALMART_RAW = (
    "WALMART\nSupercenter #1234\n04/15/2026\n"
    "Eggs\n3.49\nMilk\n2.99\nBread\n2.50\n"
    "SUBTOTAL\n8 . 98\nTAX\n0 . 85\nTOTAL\n47.83"
)


def _png_bytes():
    # Minimal valid 1x1 PNG so UploadFile content sniffing/saving succeeds.
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (1, 1), "white").save(buf, format="PNG")
    return buf.getvalue()


async def test_root_health(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    assert "running" in resp.json()["message"].lower()


async def test_upload_rejects_non_image(client):
    resp = await client.post(
        "/receipts/upload",
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "File must be an image"


async def test_upload_enqueues_job(client, fake_collections, stub_enqueue):
    resp = await client.post(
        "/receipts/upload",
        files={"file": ("r.png", _png_bytes(), "image/png")},
        headers={"X-Tenant-ID": "acme"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "queued"
    job_id = body["job_id"]

    assert stub_enqueue == [job_id]
    doc = await fake_collections["jobs"].find_one({"tenant_id": "acme"})
    assert doc is not None
    assert doc["status"] == "queued"


async def test_upload_rejects_bad_tenant(client):
    resp = await client.post(
        "/receipts/upload",
        files={"file": ("r.png", _png_bytes(), "image/png")},
        headers={"X-Tenant-ID": "bad/tenant"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid tenant id"


async def test_job_status_invalid_id(client):
    resp = await client.get("/receipts/jobs/not-an-oid")
    assert resp.status_code == 400


async def test_job_status_not_found(client):
    resp = await client.get("/receipts/jobs/" + "a" * 24)
    assert resp.status_code == 404


async def test_tenant_isolation_on_job_status(client, fake_collections):
    # Upload as tenant A, then tenant B must not be able to read the job.
    resp = await client.post(
        "/receipts/upload",
        files={"file": ("r.png", _png_bytes(), "image/png")},
        headers={"X-Tenant-ID": "tenant-a"},
    )
    job_id = resp.json()["job_id"]

    ok = await client.get(f"/receipts/jobs/{job_id}", headers={"X-Tenant-ID": "tenant-a"})
    assert ok.status_code == 200

    denied = await client.get(f"/receipts/jobs/{job_id}", headers={"X-Tenant-ID": "tenant-b"})
    assert denied.status_code == 404


# --- update (PATCH) -------------------------------------------------------


async def test_update_receipt_fields(client, fake_collections):
    rid = await _seed_receipt(fake_collections["receipts"])
    resp = await client.patch(
        f"/receipts/{rid}",
        json={"merchant_name": "Costco", "total_amount": 99.5, "category": "Shopping"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["merchant_name"] == "Costco"
    assert body["total_amount"] == 99.5
    assert body["category"] == "Shopping"
    assert body["updated_at"] is not None


async def test_update_receipt_invalid_id(client):
    resp = await client.patch("/receipts/not-an-oid", json={"merchant_name": "X"})
    assert resp.status_code == 400


async def test_update_receipt_not_found(client):
    resp = await client.patch(f"/receipts/{ObjectId()}", json={"merchant_name": "X"})
    assert resp.status_code == 404


async def test_update_receipt_empty_body(client, fake_collections):
    rid = await _seed_receipt(fake_collections["receipts"])
    resp = await client.patch(f"/receipts/{rid}", json={})
    assert resp.status_code == 400


async def test_update_receipt_negative_total_rejected(client, fake_collections):
    rid = await _seed_receipt(fake_collections["receipts"])
    resp = await client.patch(f"/receipts/{rid}", json={"total_amount": -5})
    assert resp.status_code == 422  # pydantic ge=0 validation


async def test_update_receipt_tenant_isolation(client, fake_collections):
    rid = await _seed_receipt(fake_collections["receipts"], tenant_id="tenant-a")
    resp = await client.patch(
        f"/receipts/{rid}",
        json={"merchant_name": "Hacked"},
        headers={"X-Tenant-ID": "tenant-b"},
    )
    assert resp.status_code == 404


# --- delete (DELETE) ------------------------------------------------------


async def test_delete_receipt(client, fake_collections):
    rid = await _seed_receipt(fake_collections["receipts"])
    resp = await client.delete(f"/receipts/{rid}")
    assert resp.status_code == 204

    # gone from the list
    listing = await client.get("/receipts/")
    assert all(r["id"] != rid for r in listing.json())


async def test_delete_receipt_invalid_id(client):
    resp = await client.delete("/receipts/not-an-oid")
    assert resp.status_code == 400


async def test_delete_receipt_not_found(client):
    resp = await client.delete(f"/receipts/{ObjectId()}")
    assert resp.status_code == 404


async def test_delete_receipt_tenant_isolation(client, fake_collections):
    rid = await _seed_receipt(fake_collections["receipts"], tenant_id="tenant-a")
    resp = await client.delete(f"/receipts/{rid}", headers={"X-Tenant-ID": "tenant-b"})
    assert resp.status_code == 404
    # still present for the owner
    assert await fake_collections["receipts"].find_one({"_id": ObjectId(rid)}) is not None


# --- itemize (POST /{id}/itemize) ----------------------------------------


async def test_itemize_receipt_extracts_products(client, fake_collections):
    rid = await _seed_receipt(fake_collections["receipts"], raw_text=_WALMART_RAW)
    resp = await client.post(f"/receipts/{rid}/itemize")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [(i["description"], i["amount"]) for i in items] == [
        ("Eggs", 3.49),
        ("Milk", 2.99),
        ("Bread", 2.50),
    ]


async def test_itemize_persists_items(client, fake_collections):
    rid = await _seed_receipt(fake_collections["receipts"], raw_text=_WALMART_RAW)
    await client.post(f"/receipts/{rid}/itemize")
    doc = await fake_collections["receipts"].find_one({"_id": ObjectId(rid)})
    assert len(doc["items"]) == 3


async def test_itemize_no_raw_text_yields_empty(client, fake_collections):
    rid = await _seed_receipt(fake_collections["receipts"])  # no raw_text
    resp = await client.post(f"/receipts/{rid}/itemize")
    assert resp.status_code == 200
    assert resp.json()["items"] == []


async def test_itemize_invalid_id(client):
    resp = await client.post("/receipts/not-an-oid/itemize")
    assert resp.status_code == 400


async def test_itemize_not_found(client):
    resp = await client.post(f"/receipts/{ObjectId()}/itemize")
    assert resp.status_code == 404


async def test_itemize_tenant_isolation(client, fake_collections):
    rid = await _seed_receipt(
        fake_collections["receipts"], tenant_id="tenant-a", raw_text=_WALMART_RAW
    )
    resp = await client.post(
        f"/receipts/{rid}/itemize", headers={"X-Tenant-ID": "tenant-b"}
    )
    assert resp.status_code == 404
