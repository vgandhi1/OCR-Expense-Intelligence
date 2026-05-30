"""API tests for the analytics router aggregations and tenant scoping."""

from datetime import datetime, timezone

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


async def test_analytics_empty_for_new_tenant(client, fake_collections):
    await _seed(fake_collections["receipts"])
    resp = await client.get("/analytics/merchant", headers={"X-Tenant-ID": "brand-new"})
    assert resp.status_code == 200
    assert resp.json() == []
