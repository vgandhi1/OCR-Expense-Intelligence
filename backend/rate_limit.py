"""Rate limiting (slowapi).

Limits are keyed by API key / tenant rather than raw IP, so one shared egress IP
(corporate NAT, cloud proxy) doesn't throttle unrelated tenants and a single noisy key
can't starve others. Storage defaults to in-process memory (fine for a single API
replica / tests); point ``RATELIMIT_STORAGE_URI`` at Redis for multi-replica deploys.
Set ``APP_RATELIMIT_ENABLED=0`` to disable (used by the test suite to avoid cross-test
state); the mechanism itself is covered by an isolated test. (We deliberately avoid the
name ``RATELIMIT_ENABLED`` because slowapi/limits consumes that one globally.)
"""

import os

from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address


def _enabled() -> bool:
    return os.getenv("APP_RATELIMIT_ENABLED", "1").lower() not in ("0", "false", "no")


def rate_limit_key(request: Request) -> str:
    """Per-account key: API key, then tenant header, then client IP as a last resort."""
    return (
        request.headers.get("X-API-Key")
        or request.headers.get("X-Tenant-ID")
        or get_remote_address(request)
    )


limiter = Limiter(
    key_func=rate_limit_key,
    storage_uri=os.getenv("RATELIMIT_STORAGE_URI", "memory://"),
    enabled=_enabled(),
)


async def rate_limit_exceeded_handler(
    request: Request, exc: RateLimitExceeded
) -> JSONResponse:
    """Return the same ``{detail, code}`` shape as the rest of the API on 429."""
    return JSONResponse(
        status_code=429,
        content={
            "detail": "Rate limit exceeded. Please retry shortly.",
            "code": "RATE_LIMITED",
        },
    )
