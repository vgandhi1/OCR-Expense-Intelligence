"""Tests for rate limiting (Priority 8).

Builds an isolated app with its own enabled limiter and a deliberately tiny limit, so
the mechanism (limit enforcement, per-key isolation, 429 shape) is verified without
enabling limits on the real routes (which would leak counter state across the suite).
"""

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded

from rate_limit import rate_limit_exceeded_handler, rate_limit_key


def _app() -> FastAPI:
    limiter = Limiter(key_func=rate_limit_key, storage_uri="memory://", enabled=True)
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

    @app.get("/limited")
    @limiter.limit("2/minute")
    async def limited(request: Request):
        return {"ok": True}

    return app


@pytest.mark.asyncio
async def test_third_request_is_rate_limited():
    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        headers = {"X-Tenant-ID": "acme"}
        assert (await c.get("/limited", headers=headers)).status_code == 200
        assert (await c.get("/limited", headers=headers)).status_code == 200
        blocked = await c.get("/limited", headers=headers)
    assert blocked.status_code == 429
    body = blocked.json()
    assert body["code"] == "RATE_LIMITED"
    assert "retry" in body["detail"].lower()


@pytest.mark.asyncio
async def test_limits_are_per_tenant_key():
    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        # acme exhausts its budget...
        await c.get("/limited", headers={"X-Tenant-ID": "acme"})
        await c.get("/limited", headers={"X-Tenant-ID": "acme"})
        acme_blocked = await c.get("/limited", headers={"X-Tenant-ID": "acme"})
        # ...but globex still has its own.
        globex_ok = await c.get("/limited", headers={"X-Tenant-ID": "globex"})
    assert acme_blocked.status_code == 429
    assert globex_ok.status_code == 200
