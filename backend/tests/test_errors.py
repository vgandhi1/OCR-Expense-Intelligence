"""Tests for the error-handling layer (Priority 10).

Uses a throwaway app wired with the same handlers as main, so we can trigger raises
without depending on real routes.
"""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from errors import (
    ExtractaError,
    NotFoundError,
    extracta_error_handler,
    unhandled_error_handler,
)


def _app() -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(ExtractaError, extracta_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    @app.get("/boom")
    async def boom():
        raise RuntimeError("secret db connection string leaked here")

    @app.get("/missing")
    async def missing():
        raise NotFoundError("Vendor not found.")

    @app.get("/custom")
    async def custom():
        raise ExtractaError("Nope.", status_code=409, code="CONFLICT")

    return app


@pytest.mark.asyncio
async def test_unhandled_exception_is_generic_and_hides_internals():
    transport = ASGITransport(app=_app(), raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/boom")
    assert resp.status_code == 500
    body = resp.json()
    assert body["detail"] == "An unexpected error occurred."
    assert body["code"] == "INTERNAL_ERROR"
    assert "error_id" in body
    # The real exception text must never reach the client.
    assert "secret" not in resp.text


@pytest.mark.asyncio
async def test_typed_not_found_renders_detail_and_code():
    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/missing")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Vendor not found.", "code": "NOT_FOUND"}


@pytest.mark.asyncio
async def test_custom_status_and_code():
    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/custom")
    assert resp.status_code == 409
    assert resp.json()["code"] == "CONFLICT"
