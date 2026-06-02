"""API tests for the expense-tracker router: manual expenses + budgets."""

from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.asyncio


# --- manual expenses -------------------------------------------------------


async def test_manual_expense_is_stored_with_source(client, fake_collections):
    resp = await client.post(
        "/expenses/manual",
        headers={"X-Tenant-ID": "acme"},
        json={
            "merchant_name": "Corner Cafe",
            "total_amount": 12.5,
            "date": "2026-06-03T00:00:00Z",
            "category": "Dining",
            "notes": "Team coffee",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "created" and body["source"] == "manual"

    doc = await fake_collections["receipts"].find_one({"_id": __import__("bson").ObjectId(body["id"])})
    assert doc["source"] == "manual"
    assert doc["tenant_id"] == "acme"
    assert doc["total_amount"] == 12.5
    assert doc["items"] == []


async def test_manual_expense_shows_in_receipts_list(client, fake_collections):
    await client.post(
        "/expenses/manual",
        headers={"X-Tenant-ID": "acme"},
        json={
            "merchant_name": "Bus Pass",
            "total_amount": 30.0,
            "date": "2026-06-01T00:00:00Z",
            "category": "Transport",
        },
    )
    resp = await client.get("/receipts/", headers={"X-Tenant-ID": "acme"})
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["source"] == "manual"
    assert rows[0]["merchant_name"] == "Bus Pass"


async def test_manual_expense_rejects_negative_amount(client, fake_collections):
    resp = await client.post(
        "/expenses/manual",
        headers={"X-Tenant-ID": "acme"},
        json={
            "merchant_name": "Refund?",
            "total_amount": -5.0,
            "date": "2026-06-01T00:00:00Z",
            "category": "Shopping",
        },
    )
    assert resp.status_code == 422  # pydantic validation (ge=0)


# --- budgets ---------------------------------------------------------------


async def test_budget_upsert_is_idempotent(client, fake_collections):
    payload = {"category": "Groceries", "limit_amount": 500.0, "month": "2026-06"}
    r1 = await client.post("/expenses/budgets", headers={"X-Tenant-ID": "acme"}, json=payload)
    assert r1.status_code == 200

    # Re-submit with a new limit → overwrite, not duplicate.
    payload["limit_amount"] = 650.0
    r2 = await client.post("/expenses/budgets", headers={"X-Tenant-ID": "acme"}, json=payload)
    assert r2.status_code == 200

    count = await fake_collections["budgets"].count_documents(
        {"tenant_id": "acme", "month": "2026-06", "category": "Groceries"}
    )
    assert count == 1

    listed = await client.get("/expenses/budgets/2026-06", headers={"X-Tenant-ID": "acme"})
    rows = {r["category"]: r["limit_amount"] for r in listed.json()}
    assert rows["Groceries"] == 650.0


async def test_budget_rejects_bad_month_format(client, fake_collections):
    resp = await client.post(
        "/expenses/budgets",
        headers={"X-Tenant-ID": "acme"},
        json={"category": "Dining", "limit_amount": 100.0, "month": "June-2026"},
    )
    assert resp.status_code == 422


async def test_budgets_scoped_to_tenant(client, fake_collections):
    await client.post(
        "/expenses/budgets",
        headers={"X-Tenant-ID": "acme"},
        json={"category": "Dining", "limit_amount": 100.0, "month": "2026-06"},
    )
    resp = await client.get("/expenses/budgets/2026-06", headers={"X-Tenant-ID": "other"})
    assert resp.json() == []
