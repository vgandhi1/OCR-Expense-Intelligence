"""Tests for API-key auth + tenant resolution and the admin issuance endpoint."""

from datetime import datetime, timezone

import pytest
from bson import ObjectId

import auth


# --- key primitives (pure) ------------------------------------------------


def test_hash_is_deterministic_and_sha256_hex():
    h1 = auth.hash_api_key("ext_abc")
    h2 = auth.hash_api_key("ext_abc")
    assert h1 == h2
    assert len(h1) == 64 and all(c in "0123456789abcdef" for c in h1)


def test_generate_api_key_prefix_and_hash_match():
    raw, hashed = auth.generate_api_key()
    assert raw.startswith("ext_")
    assert auth.hash_api_key(raw) == hashed
    # Two calls never collide.
    assert auth.generate_api_key()[0] != raw


# --- API-key resolution via the dependency (through the app) --------------


async def _seed_tenant(tenants, tenant_id, raw_key, active=True):
    await tenants.insert_one(
        {
            "tenant_id": tenant_id,
            "name": tenant_id,
            "api_key_hash": auth.hash_api_key(raw_key),
            "active": active,
            "plan": "starter",
            "created_at": datetime.now(timezone.utc),
            "last_seen_at": None,
        }
    )


async def test_api_key_resolves_to_its_tenant(client, fake_collections):
    await _seed_tenant(fake_collections["tenants"], "acme", "ext_acmekey")
    await fake_collections["receipts"].insert_many(
        [
            {"_id": ObjectId(), "tenant_id": "acme", "merchant_name": "Walmart",
             "total_amount": 10.0, "created_at": datetime.now(timezone.utc)},
            {"_id": ObjectId(), "tenant_id": "other", "merchant_name": "Secret",
             "total_amount": 99.0, "created_at": datetime.now(timezone.utc)},
        ]
    )
    resp = await client.get("/receipts/", headers={"X-API-Key": "ext_acmekey"})
    assert resp.status_code == 200
    merchants = [r["merchant_name"] for r in resp.json()]
    assert merchants == ["Walmart"]  # only the key's tenant, no leak


async def test_invalid_api_key_is_401(client, fake_collections):
    resp = await client.get("/receipts/", headers={"X-API-Key": "ext_nope"})
    assert resp.status_code == 401


async def test_revoked_api_key_is_401(client, fake_collections):
    await _seed_tenant(fake_collections["tenants"], "acme", "ext_dead", active=False)
    resp = await client.get("/receipts/", headers={"X-API-Key": "ext_dead"})
    assert resp.status_code == 401


async def test_api_key_updates_last_seen(client, fake_collections):
    await _seed_tenant(fake_collections["tenants"], "acme", "ext_seen")
    await client.get("/receipts/", headers={"X-API-Key": "ext_seen"})
    doc = await fake_collections["tenants"].find_one({"tenant_id": "acme"})
    assert doc["last_seen_at"] is not None


async def test_tenant_header_fallback_still_works(client, fake_collections):
    # No API key: the dev X-Tenant-ID fallback must still resolve (back-compat).
    await fake_collections["receipts"].insert_one(
        {"_id": ObjectId(), "tenant_id": "acme", "merchant_name": "Walmart",
         "total_amount": 10.0, "created_at": datetime.now(timezone.utc)}
    )
    resp = await client.get("/receipts/", headers={"X-Tenant-ID": "acme"})
    assert resp.status_code == 200
    assert len(resp.json()) == 1


async def test_require_api_key_disables_header_fallback(client, monkeypatch):
    monkeypatch.setenv("REQUIRE_API_KEY", "1")
    resp = await client.get("/receipts/", headers={"X-Tenant-ID": "acme"})
    assert resp.status_code == 401


# --- admin issuance endpoint ----------------------------------------------


async def test_admin_issue_key_then_use_it(client, fake_collections, monkeypatch):
    monkeypatch.setenv("ADMIN_KEY", "super-secret-admin")
    resp = await client.post(
        "/admin/tenants",
        json={"name": "Acme Corp", "email": "a@acme.com", "plan": "growth"},
        headers={"X-Admin-Key": "super-secret-admin"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["tenant_id"] == "acme-corp"
    assert body["api_key"].startswith("ext_")

    # The freshly issued key authenticates.
    used = await client.get("/receipts/", headers={"X-API-Key": body["api_key"]})
    assert used.status_code == 200


async def test_admin_requires_correct_key(client, monkeypatch):
    monkeypatch.setenv("ADMIN_KEY", "super-secret-admin")
    resp = await client.post(
        "/admin/tenants",
        json={"name": "Acme Corp"},
        headers={"X-Admin-Key": "wrong"},
    )
    assert resp.status_code == 403


async def test_admin_unconfigured_is_503(client, monkeypatch):
    monkeypatch.delenv("ADMIN_KEY", raising=False)
    resp = await client.post(
        "/admin/tenants",
        json={"name": "Acme Corp"},
        headers={"X-Admin-Key": "anything"},
    )
    assert resp.status_code == 503


async def test_admin_rejects_invalid_plan(client, monkeypatch):
    monkeypatch.setenv("ADMIN_KEY", "super-secret-admin")
    resp = await client.post(
        "/admin/tenants",
        json={"name": "Acme Corp", "plan": "enterprise-plus"},
        headers={"X-Admin-Key": "super-secret-admin"},
    )
    assert resp.status_code == 400
