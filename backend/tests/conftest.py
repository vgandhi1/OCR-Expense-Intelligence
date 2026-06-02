"""Shared pytest fixtures.

These fixtures swap the real MongoDB (Motor) collections for an in-memory
mongomock instance and stub out the Celery enqueue call, so the API layer can be
exercised without Docker, MongoDB, or Redis running.
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

# Make the backend package importable when running `pytest` from the repo root.
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Point uploads at a throwaway directory before anything imports storage_paths.
_TMP_UPLOAD_DIR = tempfile.mkdtemp(prefix="extracta_test_uploads_")
os.environ.setdefault("UPLOAD_ROOT", _TMP_UPLOAD_DIR)

# Disable rate limiting for the suite so a shared in-memory limiter can't leak state
# across tests. The limiter mechanism itself is covered in test_rate_limit.py.
# (Our own toggle, distinct from slowapi's global RATELIMIT_ENABLED.)
os.environ.setdefault("APP_RATELIMIT_ENABLED", "0")

FIXTURE_DIR = BACKEND_DIR.parent / "test_fixtures"


@pytest.fixture
def fake_collections(monkeypatch):
    """Replace Motor collections everywhere they are referenced with mongomock."""
    from mongomock_motor import AsyncMongoMockClient

    client = AsyncMongoMockClient()
    db = client["expense_intelligence"]
    jobs = db["jobs"]
    receipts = db["receipts"]
    line_items = db["line_items"]
    tenants = db["tenants"]
    vendors = db["vendors"]
    budgets = db["budgets"]

    import database
    import routes.analytics as analytics_route
    import routes.expenses as expenses_route
    import routes.receipts as receipts_route
    import routes.vendors as vendors_route

    monkeypatch.setattr(database, "collection_jobs", jobs, raising=False)
    monkeypatch.setattr(database, "collection_receipts", receipts, raising=False)
    monkeypatch.setattr(database, "collection_line_items", line_items, raising=False)
    # auth.py and routes/admin.py resolve tenants via database.collection_tenants
    # at call time, so patching it on the database module is sufficient.
    monkeypatch.setattr(database, "collection_tenants", tenants, raising=False)
    # line_items_writer's async path reads database.collection_vendors at call time.
    monkeypatch.setattr(database, "collection_vendors", vendors, raising=False)
    monkeypatch.setattr(database, "collection_budgets", budgets, raising=False)
    monkeypatch.setattr(receipts_route, "collection_jobs", jobs, raising=False)
    monkeypatch.setattr(receipts_route, "collection_receipts", receipts, raising=False)
    monkeypatch.setattr(
        receipts_route, "collection_line_items", line_items, raising=False
    )
    monkeypatch.setattr(analytics_route, "collection_receipts", receipts, raising=False)
    monkeypatch.setattr(
        analytics_route, "collection_line_items", line_items, raising=False
    )
    monkeypatch.setattr(analytics_route, "collection_budgets", budgets, raising=False)
    monkeypatch.setattr(expenses_route, "collection_receipts", receipts, raising=False)
    monkeypatch.setattr(expenses_route, "collection_budgets", budgets, raising=False)
    monkeypatch.setattr(vendors_route, "collection_vendors", vendors, raising=False)

    return {
        "jobs": jobs,
        "receipts": receipts,
        "line_items": line_items,
        "tenants": tenants,
        "vendors": vendors,
        "budgets": budgets,
    }


@pytest.fixture
def stub_enqueue(monkeypatch):
    """Record enqueued job ids instead of dispatching to a real Celery broker."""
    import routes.receipts as receipts_route

    enqueued = []

    class _StubTask:
        def delay(self, job_id):
            enqueued.append(job_id)
            return None

    monkeypatch.setattr(receipts_route, "process_receipt_job", _StubTask())
    return enqueued


@pytest.fixture
async def client(fake_collections, stub_enqueue):
    """An httpx AsyncClient wired to the FastAPI app with mocked dependencies."""
    from httpx import ASGITransport, AsyncClient

    import main

    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
