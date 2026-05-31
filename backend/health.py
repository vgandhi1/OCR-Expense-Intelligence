"""Dependency health probes for the readiness endpoint.

Kept in their own module (rather than inline in main.py) so they can be
monkeypatched in tests without standing up real MongoDB/Redis, and reused by any
future ops tooling. Each probe raises on failure; the caller decides the response.
"""

import os

import database


async def check_mongodb() -> None:
    """Ping MongoDB via the existing Motor client. Raises on failure."""
    await database.client.admin.command("ping")


async def check_redis() -> None:
    """Ping Redis using a short-lived async client. Raises on failure."""
    import redis.asyncio as aioredis

    url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    redis_client = aioredis.from_url(url, socket_connect_timeout=2)
    try:
        await redis_client.ping()
    finally:
        await redis_client.aclose()
