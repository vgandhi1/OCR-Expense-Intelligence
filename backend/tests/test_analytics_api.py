"""API tests for the analytics router aggregations and tenant scoping."""

from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.asyncio


async def _seed(receipts):
    await receipts.insert_many(
        [
            {
                "tenant_id": "acme",
                "merchant_name": "Walmart",
                "total_amount": 47.83,
                "date": datetime(2026, 4, 15, tzinfo=timezone.utc),
            },
            {
                "tenant_id": "acme",
                "merchant_name": "Walmart",
                "total_amount": 12.00,
                "date": datetime(2026, 4, 20, tzinfo=timezone.utc),
            },
            {
                "tenant_id": "acme",
                "merchant_name": "Shell",
                "total_amount": 52.40,
                "date": datetime(2026, 5, 1, tzinfo=timezone.utc),
            },
            {
                "tenant_id": "other",
                "merchant_name": "Secret",
                "total_amount": 999.0,
                "date": datetime(2026, 4, 1, tzinfo=timezone.utc),
            },
        ]
    )


async def test_merchant_spend_scoped_to_tenant(client, fake_collections):
    await _seed(fake_collections["receipts"])
    resp = await client.get("/analytics/merchant", headers={"X-Tenant-ID": "acme"})
    assert resp.status_code == 200
    data = {row["name"]: row["value"] for row in resp.json()}

    assert "Secret" not in data  # other tenant's data must not leak
    assert data["Walmart"] == pytest.approx(59.83)
    assert data["Shell"] == pytest.approx(52.40)


async def test_monthly_spend_groups_by_month(client, fake_collections):
    await _seed(fake_collections["receipts"])
    resp = await client.get("/analytics/monthly", headers={"X-Tenant-ID": "acme"})
    assert resp.status_code == 200
    by_month = {row["name"]: row["value"] for row in resp.json()}

    assert by_month["2026-04"] == pytest.approx(59.83)
    assert by_month["2026-05"] == pytest.approx(52.40)


async def test_category_spend_grouped_and_scoped(client, fake_collections):
    await fake_collections["receipts"].insert_many(
        [
            {"tenant_id": "acme", "category": "Groceries", "total_amount": 20.0,
             "date": datetime(2026, 4, 1, tzinfo=timezone.utc)},
            {"tenant_id": "acme", "category": "Groceries", "total_amount": 30.0,
             "date": datetime(2026, 4, 2, tzinfo=timezone.utc)},
            {"tenant_id": "acme", "category": "Dining", "total_amount": 45.0,
             "date": datetime(2026, 4, 3, tzinfo=timezone.utc)},
            # No category → bucketed as "Uncategorized".
            {"tenant_id": "acme", "category": None, "total_amount": 5.0,
             "date": datetime(2026, 4, 4, tzinfo=timezone.utc)},
            {"tenant_id": "other", "category": "Groceries", "total_amount": 999.0,
             "date": datetime(2026, 4, 5, tzinfo=timezone.utc)},
        ]
    )
    resp = await client.get("/analytics/category", headers={"X-Tenant-ID": "acme"})
    assert resp.status_code == 200
    rows = {r["name"]: r for r in resp.json()}

    assert 999.0 not in [r["value"] for r in rows.values()]  # tenant isolation
    assert rows["Groceries"]["value"] == pytest.approx(50.0)
    assert rows["Groceries"]["count"] == 2
    assert rows["Dining"]["value"] == pytest.approx(45.0)
    assert rows["Uncategorized"]["value"] == pytest.approx(5.0)


async def test_analytics_empty_for_new_tenant(client, fake_collections):
    await _seed(fake_collections["receipts"])
    resp = await client.get("/analytics/merchant", headers={"X-Tenant-ID": "brand-new"})
    assert resp.status_code == 200
    assert resp.json() == []


# --- line-item analytics --------------------------------------------------


async def _seed_line_items(line_items):
    recent = datetime.now(timezone.utc) - timedelta(days=10)
    stale = datetime.now(timezone.utc) - timedelta(days=200)
    await line_items.insert_many(
        [
            {"tenant_id": "acme", "vendor_raw": "Walmart", "category": "Groceries",
             "amount": 4.99, "period": recent},
            {"tenant_id": "acme", "vendor_raw": "Walmart", "category": "Groceries",
             "amount": 5.01, "period": recent},
            {"tenant_id": "acme", "vendor_raw": "Shell", "category": "Transport",
             "amount": 40.0, "period": recent},
            # Older than the 90-day window — must be excluded from /vendors default.
            {"tenant_id": "acme", "vendor_raw": "OldCo", "category": "Shopping",
             "amount": 100.0, "period": stale},
            # Different tenant — must never leak.
            {"tenant_id": "other", "vendor_raw": "Secret", "category": "Groceries",
             "amount": 999.0, "period": recent},
        ]
    )


async def test_vendors_spend_scoped_and_windowed(client, fake_collections):
    await _seed_line_items(fake_collections["line_items"])
    resp = await client.get("/analytics/vendors", headers={"X-Tenant-ID": "acme"})
    assert resp.status_code == 200
    rows = {r["name"]: r for r in resp.json()}

    assert "Secret" not in rows  # tenant isolation
    assert "OldCo" not in rows  # outside default 90-day window
    assert rows["Walmart"]["value"] == pytest.approx(10.0)
    assert rows["Walmart"]["count"] == 2
    assert rows["Shell"]["value"] == pytest.approx(40.0)


async def test_vendors_window_can_be_widened(client, fake_collections):
    await _seed_line_items(fake_collections["line_items"])
    resp = await client.get(
        "/analytics/vendors", params={"days": 365}, headers={"X-Tenant-ID": "acme"}
    )
    assert resp.status_code == 200
    names = {r["name"] for r in resp.json()}
    assert "OldCo" in names  # now inside the window


async def test_categories_grouped_by_month(client, fake_collections):
    await _seed_line_items(fake_collections["line_items"])
    resp = await client.get("/analytics/categories", headers={"X-Tenant-ID": "acme"})
    assert resp.status_code == 200
    cats = {r["category"] for r in resp.json()}
    assert "Groceries" in cats and "Transport" in cats
    assert all(r["month"] for r in resp.json())


# --- budget progress ------------------------------------------------------


async def test_budget_progress_merges_actual_and_limits(client, fake_collections):
    await fake_collections["receipts"].insert_many(
        [
            {"tenant_id": "acme", "category": "Groceries", "total_amount": 420.50,
             "date": datetime(2026, 6, 5, tzinfo=timezone.utc)},
            {"tenant_id": "acme", "category": "Dining", "total_amount": 185.0,
             "date": datetime(2026, 6, 10, tzinfo=timezone.utc)},
            # Outside the month → must be excluded.
            {"tenant_id": "acme", "category": "Groceries", "total_amount": 99.0,
             "date": datetime(2026, 5, 30, tzinfo=timezone.utc)},
            # Different tenant → must never leak.
            {"tenant_id": "other", "category": "Groceries", "total_amount": 999.0,
             "date": datetime(2026, 6, 6, tzinfo=timezone.utc)},
        ]
    )
    await fake_collections["budgets"].insert_many(
        [
            {"tenant_id": "acme", "month": "2026-06", "category": "Groceries", "limit_amount": 500.0},
            {"tenant_id": "acme", "month": "2026-06", "category": "Dining", "limit_amount": 150.0},
            # Budget with no spend yet → should still appear with actual 0.
            {"tenant_id": "acme", "month": "2026-06", "category": "Utilities", "limit_amount": 80.0},
        ]
    )
    resp = await client.get("/analytics/budget-progress/2026-06", headers={"X-Tenant-ID": "acme"})
    assert resp.status_code == 200
    rows = {r["category"]: r for r in resp.json()}

    assert rows["Groceries"]["actual"] == pytest.approx(420.50)  # May entry excluded
    assert rows["Groceries"]["limit"] == pytest.approx(500.0)
    assert rows["Dining"]["actual"] == pytest.approx(185.0)
    assert rows["Dining"]["limit"] == pytest.approx(150.0)  # over budget
    assert rows["Utilities"]["actual"] == pytest.approx(0.0)  # budget, no spend
    assert rows["Utilities"]["limit"] == pytest.approx(80.0)
    assert 999.0 not in [r["actual"] for r in rows.values()]  # tenant isolation


async def test_budget_progress_rejects_bad_month(client, fake_collections):
    resp = await client.get("/analytics/budget-progress/2026-13", headers={"X-Tenant-ID": "acme"})
    assert resp.status_code == 400


async def test_extraction_failures_lists_incomplete_receipts(client, fake_collections):
    await fake_collections["receipts"].insert_many(
        [
            {"tenant_id": "acme", "merchant_name": "Walmart",
             "total_amount": 10.0, "date": datetime.now(timezone.utc),
             "created_at": datetime.now(timezone.utc)},
            {"tenant_id": "acme", "merchant_name": None,
             "total_amount": None, "date": None,
             "created_at": datetime.now(timezone.utc)},
        ]
    )
    resp = await client.get(
        "/analytics/extraction-failures", headers={"X-Tenant-ID": "acme"}
    )
    assert resp.status_code == 200
    failures = resp.json()
    assert len(failures) == 1
    assert failures[0]["merchant_name"] is None
    assert "id" in failures[0]
