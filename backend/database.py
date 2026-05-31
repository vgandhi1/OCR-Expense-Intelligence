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
collection_line_items = database.line_items
collection_tenants = database.tenants
collection_vendors = database.vendors


async def ensure_indexes() -> None:
    """Compound indexes aligned with multi-tenant job polling, receipts list, and
    line-item analytics. `tenant_id` leads every index to keep tenants isolated and
    aggregations index-backed."""
    try:
        await collection_jobs.create_index([("tenant_id", 1), ("created_at", -1)])
        await collection_jobs.create_index([("tenant_id", 1), ("status", 1)])
        await collection_receipts.create_index([("tenant_id", 1), ("created_at", -1)])
        await collection_receipts.create_index([("tenant_id", 1), ("needs_review", 1)])
        await collection_line_items.create_index([("tenant_id", 1), ("period", -1)])
        await collection_line_items.create_index(
            [("tenant_id", 1), ("vendor_id", 1), ("period", -1)]
        )
        await collection_line_items.create_index(
            [("tenant_id", 1), ("category", 1), ("period", -1)]
        )
        await collection_line_items.create_index(
            [("tenant_id", 1), ("needs_review", 1)]
        )
        await collection_line_items.create_index(
            [("tenant_id", 1), ("receipt_id", 1)]
        )
        # Auth lookups: api_key_hash and tenant_id are both unique identifiers.
        await collection_tenants.create_index("api_key_hash", unique=True)
        await collection_tenants.create_index("tenant_id", unique=True)
        # Vendor normalisation: alias fuzzy-match scan + review queue, both tenant-scoped.
        await collection_vendors.create_index([("tenant_id", 1), ("aliases", 1)])
        await collection_vendors.create_index([("tenant_id", 1), ("needs_review", 1)])
    except Exception:
        logger.exception("Failed to create MongoDB indexes")
