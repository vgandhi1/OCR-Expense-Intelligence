"""Authentication + tenant resolution.

Resolution order for every request (via the ``get_tenant_id`` dependency):

  1. ``X-API-Key``  → SHA-256 hashed, looked up in the ``tenants`` collection
                      (the production path). Unknown/revoked keys → 401.
  2. ``X-Tenant-ID``→ dev/test convenience fallback. Remove this branch (set
                      ``REQUIRE_API_KEY=1``) before exposing the API publicly.
  3. neither        → the ``"default"`` tenant (local dev only).

Only the SHA-256 *hash* of a key is ever stored or compared; raw keys are shown
to the caller exactly once at creation time and never logged.
"""

import hashlib
import logging
import os
import secrets
from datetime import datetime, timezone
from typing import Optional, Tuple

from fastapi import Header, HTTPException

import database
from storage_paths import validate_tenant_id

logger = logging.getLogger(__name__)

API_KEY_PREFIX = "ext_"

# When set truthy, the X-Tenant-ID dev fallback and the anonymous "default" tenant
# are disabled, so a valid X-API-Key becomes mandatory.
def _require_api_key() -> bool:
    return os.getenv("REQUIRE_API_KEY", "").lower() in ("1", "true", "yes")


def hash_api_key(raw_key: str) -> str:
    """SHA-256 hex digest of a raw key. Never store/compare the raw value."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def generate_api_key() -> Tuple[str, str]:
    """Return ``(raw_key, hashed_key)``. Persist only the hash; reveal raw once."""
    raw = API_KEY_PREFIX + secrets.token_hex(32)
    return raw, hash_api_key(raw)


def _validate_tenant_or_400(value: str) -> str:
    try:
        return validate_tenant_id(value)
    except ValueError:
        # Generic message: do not leak validation internals to clients.
        raise HTTPException(status_code=400, detail="Invalid tenant id")


async def get_tenant_id(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-ID"),
) -> str:
    """FastAPI dependency that resolves the caller's tenant_id (server-trusted)."""
    if x_api_key:
        tenant = await database.collection_tenants.find_one(
            {"api_key_hash": hash_api_key(x_api_key), "active": True}
        )
        if not tenant:
            # Generic 401 — don't reveal whether the key existed but was revoked.
            raise HTTPException(status_code=401, detail="Invalid API key")
        try:
            await database.collection_tenants.update_one(
                {"_id": tenant["_id"]},
                {"$set": {"last_seen_at": datetime.now(timezone.utc)}},
            )
        except Exception:
            # last_seen is best-effort telemetry; never fail the request on it.
            logger.exception("failed to update tenant last_seen_at")
        return tenant.get("tenant_id", "default")

    if _require_api_key():
        raise HTTPException(status_code=401, detail="API key required")

    # Dev/test fallback — remove by setting REQUIRE_API_KEY=1 in production.
    if x_tenant_id:
        return _validate_tenant_or_400(x_tenant_id.strip())

    return "default"
