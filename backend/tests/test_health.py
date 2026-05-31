"""Tests for the liveness/readiness endpoints (Priority 9)."""

import pytest


@pytest.mark.asyncio
async def test_health_liveness_ok(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "timestamp" in body


@pytest.mark.asyncio
async def test_readiness_ok_when_dependencies_up(client, monkeypatch):
    import health

    async def _ok():
        return None

    monkeypatch.setattr(health, "check_mongodb", _ok)
    monkeypatch.setattr(health, "check_redis", _ok)

    resp = await client.get("/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["checks"] == {"mongodb": "ok", "redis": "ok"}


@pytest.mark.asyncio
async def test_readiness_degraded_when_redis_down(client, monkeypatch):
    import health

    async def _ok():
        return None

    async def _fail():
        raise RuntimeError("connection refused")

    monkeypatch.setattr(health, "check_mongodb", _ok)
    monkeypatch.setattr(health, "check_redis", _fail)

    resp = await client.get("/health/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["checks"]["mongodb"] == "ok"
    assert body["checks"]["redis"] == "error"
