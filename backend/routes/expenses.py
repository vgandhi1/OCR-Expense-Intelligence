"""Expense-tracker endpoints: manual expense entry and per-category budgets.

Manual expenses are the *proactive* counterpart to the OCR pipeline: they skip
Redis/Celery entirely and are validated and written straight into the same
``receipts`` collection (stamped ``source = manual``), so existing analytics,
export, and edit/delete all work on them for free.

Budgets live in their own ``budgets`` collection, one document per
(tenant, month, category), and feed the ``/analytics/budget-progress`` view.
"""

import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auth import get_tenant_id
from database import collection_budgets, collection_receipts
from models import BudgetUpsert, ExpenseSource, ManualExpenseCreate

router = APIRouter()


@router.post("/manual", status_code=201)
async def create_manual_expense(
    payload: ManualExpenseCreate,
    tenant_id: str = Depends(get_tenant_id),
) -> Dict[str, Any]:
    """Create a manually-entered expense (no OCR). Server stamps tenant + source."""
    now = datetime.now(timezone.utc)
    doc = payload.model_dump()
    doc.update(
        {
            "tenant_id": tenant_id,
            "source": ExpenseSource.MANUAL.value,
            "items": [],
            "confidence": None,
            "needs_review": False,
            "created_at": now,
            "updated_at": now,
        }
    )
    result = await collection_receipts.insert_one(doc)
    return {"id": str(result.inserted_id), "status": "created", "source": "manual"}


@router.post("/budgets", status_code=200)
async def upsert_budget(
    payload: BudgetUpsert,
    tenant_id: str = Depends(get_tenant_id),
) -> Dict[str, Any]:
    """Set (or update) the spending limit for one category in a given month.

    Idempotent upsert keyed on (tenant, month, category) — matching the unique
    index — so re-submitting simply overwrites the limit.
    """
    now = datetime.now(timezone.utc)
    await collection_budgets.update_one(
        {"tenant_id": tenant_id, "month": payload.month, "category": payload.category},
        {
            "$set": {"limit_amount": payload.limit_amount, "updated_at": now},
            "$setOnInsert": {
                "tenant_id": tenant_id,
                "month": payload.month,
                "category": payload.category,
                "created_at": now,
            },
        },
        upsert=True,
    )
    return {
        "status": "saved",
        "month": payload.month,
        "category": payload.category,
        "limit_amount": payload.limit_amount,
    }


@router.get("/budgets/{month}")
async def list_budgets(
    month: str,
    tenant_id: str = Depends(get_tenant_id),
) -> List[Dict[str, Any]]:
    """List the tenant's budgets for a month (``YYYY-MM``)."""
    cursor = collection_budgets.find({"tenant_id": tenant_id, "month": month})
    docs = await cursor.to_list(length=500)
    return [
        {
            "category": d.get("category"),
            "limit_amount": round(d.get("limit_amount", 0) or 0, 2),
            "month": d.get("month"),
        }
        for d in docs
    ]
