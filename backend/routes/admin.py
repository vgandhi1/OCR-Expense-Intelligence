"""Admin-only endpoints for issuing tenant API keys.

Protected by a static ``X-Admin-Key`` compared against the ``ADMIN_KEY`` env var
using a constant-time comparison. The endpoints are hidden from the OpenAPI schema.
This is the minimum viable issuance path; a self-serve signup flow is a separate
product decision.
"""

import logging
import os
import re
import secrets
import sys
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
from auth import generate_api_key
from storage_paths import validate_tenant_id

logger = logging.getLogger(__name__)

router = APIRouter()

_ALLOWED_PLANS = {"starter", "growth", "enterprise"}


class CreateTenantRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: Optional[str] = Field(default=None, max_length=200)
    plan: str = "starter"


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.strip().lower()).strip("-")
    return slug[:64] or "tenant"


def _require_admin(x_admin_key: Optional[str]) -> None:
    configured = os.getenv("ADMIN_KEY")
    if not configured:
        # Fail closed: if no admin key is configured, the endpoint is unusable.
        raise HTTPException(status_code=503, detail="Admin API not configured")
    if not x_admin_key or not secrets.compare_digest(x_admin_key, configured):
        raise HTTPException(status_code=403, detail="Forbidden")


@router.post("/tenants", include_in_schema=False)
async def create_tenant(
    payload: CreateTenantRequest,
    x_admin_key: Optional[str] = Header(default=None, alias="X-Admin-Key"),
):
    """Issue a new tenant + API key. Returns the raw key exactly once."""
    _require_admin(x_admin_key)

    if payload.plan not in _ALLOWED_PLANS:
        raise HTTPException(status_code=400, detail="Invalid plan")

    try:
        tenant_id = validate_tenant_id(_slugify(payload.name))
    except ValueError:
        raise HTTPException(status_code=400, detail="Could not derive a valid tenant id")

    # Keep tenant_id unique; if the slug is taken, disambiguate with a short suffix.
    if await database.collection_tenants.find_one({"tenant_id": tenant_id}):
        tenant_id = validate_tenant_id(f"{tenant_id[:55]}-{secrets.token_hex(4)}")

    raw_key, hashed_key = generate_api_key()
    await database.collection_tenants.insert_one(
        {
            "tenant_id": tenant_id,
            "name": payload.name,
            "email": payload.email,
            "api_key_hash": hashed_key,
            "active": True,
            "plan": payload.plan,
            "created_at": datetime.now(timezone.utc),
            "last_seen_at": None,
        }
    )
    logger.info("issued api key for tenant_id=%s plan=%s", tenant_id, payload.plan)

    # Raw key is returned once and never persisted or logged.
    return {
        "tenant_id": tenant_id,
        "api_key": raw_key,
        "warning": "Store this key securely. It will not be shown again.",
    }
