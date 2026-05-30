import os
import logging
from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger(__name__)

# MongoDB URL from environment (set in docker-compose)
MONGO_URL = os.getenv("MONGODB_URL", "mongodb://mongo:27017")

client = AsyncIOMotorClient(MONGO_URL)
database = client.expense_intelligence
collection_receipts = database.receipts
collection_jobs = database.jobs


async def ensure_indexes() -> None:
    """Compound indexes aligned with multi-tenant job polling and receipts list."""
    try:
        await collection_jobs.create_index([("tenant_id", 1), ("created_at", -1)])
        await collection_jobs.create_index([("tenant_id", 1), ("status", 1)])
        await collection_receipts.create_index([("tenant_id", 1), ("created_at", -1)])
    except Exception:
        logger.exception("Failed to create MongoDB indexes")
