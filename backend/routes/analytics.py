import calendar
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException

import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auth import get_tenant_id
from database import collection_receipts, collection_line_items, collection_budgets

router = APIRouter()


def _tenant_match_stage(tenant_id: str) -> Dict[str, Any]:
    if tenant_id == "default":
        return {
            "$match": {
                "$or": [
                    {"tenant_id": "default"},
                    {"tenant_id": {"$exists": False}},
                ]
            }
        }
    return {"$match": {"tenant_id": tenant_id}}


@router.get("/monthly")
async def get_monthly_spend(
    tenant_id: str = Depends(get_tenant_id),
):
    pipeline: List[Dict[str, Any]] = [
        _tenant_match_stage(tenant_id),
        {
            "$group": {
                "_id": {
                    "$dateToString": {"format": "%Y-%m", "date": "$date"},
                },
                "total": {"$sum": "$total_amount"},
                "count": {"$sum": 1},
            }
        },
        {"$sort": {"_id": 1}},
    ]
    results = await collection_receipts.aggregate(pipeline).to_list(length=None)
    return [
        {"name": r["_id"] or "Unknown", "value": r["total"], "count": r["count"]}
        for r in results
    ]


@router.get("/merchant")
async def get_merchant_spend(
    tenant_id: str = Depends(get_tenant_id),
):
    pipeline: List[Dict[str, Any]] = [
        _tenant_match_stage(tenant_id),
        {
            "$group": {
                "_id": "$merchant_name",
                "total": {"$sum": "$total_amount"},
                "count": {"$sum": 1},
            }
        },
        {"$sort": {"total": -1}},
        {"$limit": 5},
    ]
    results = await collection_receipts.aggregate(pipeline).to_list(length=None)
    return [{"name": r["_id"] or "Unknown", "value": r["total"]} for r in results]


@router.get("/category")
async def get_category_spend(
    tenant_id: str = Depends(get_tenant_id),
):
    """Total spend grouped by category, from receipts.

    Uses the receipt-level ``category`` (always populated by the classifier), so
    it works even for receipts that were never itemized — ideal for a pie chart.
    """
    pipeline: List[Dict[str, Any]] = [
        _tenant_match_stage(tenant_id),
        {
            "$group": {
                "_id": "$category",
                "total": {"$sum": "$total_amount"},
                "count": {"$sum": 1},
            }
        },
        {"$sort": {"total": -1}},
    ]
    results = await collection_receipts.aggregate(pipeline).to_list(length=None)
    return [
        {
            "name": r["_id"] or "Uncategorized",
            "value": round(r["total"] or 0, 2),
            "count": r["count"],
        }
        for r in results
    ]


@router.get("/vendors")
async def get_vendor_spend(
    days: int = 90,
    tenant_id: str = Depends(get_tenant_id),
):
    """Top vendors by total spend over the last N days, from line items."""
    days = max(1, min(days, 3650))  # clamp to a sane range
    since = datetime.now(timezone.utc) - timedelta(days=days)
    pipeline: List[Dict[str, Any]] = [
        _tenant_match_stage(tenant_id),
        {"$match": {"period": {"$gte": since}}},
        {
            "$group": {
                # Group by the normalised vendor so "WALMART"/"Wal-Mart #21" collapse
                # into one row; fall back to the raw name for pre-normalisation items.
                "_id": {"$ifNull": ["$vendor_canonical", "$vendor_raw"]},
                "total": {"$sum": "$amount"},
                "count": {"$sum": 1},
                "avg_amount": {"$avg": "$amount"},
            }
        },
        {"$sort": {"total": -1}},
        {"$limit": 20},
    ]
    results = await collection_line_items.aggregate(pipeline).to_list(length=None)
    return [
        {
            "name": r["_id"] or "Unknown",
            "value": round(r["total"] or 0, 2),
            "count": r["count"],
            "avg": round(r["avg_amount"] or 0, 2),
        }
        for r in results
    ]


@router.get("/categories")
async def get_category_by_month(
    tenant_id: str = Depends(get_tenant_id),
):
    """Monthly spend broken down by category, from line items."""
    pipeline: List[Dict[str, Any]] = [
        _tenant_match_stage(tenant_id),
        {"$match": {"period": {"$ne": None}}},
        {
            "$group": {
                "_id": {
                    "category": "$category",
                    "month": {
                        "$dateToString": {"format": "%Y-%m", "date": "$period"}
                    },
                },
                "total": {"$sum": "$amount"},
                "count": {"$sum": 1},
            }
        },
        {"$sort": {"_id.month": -1, "total": -1}},
    ]
    results = await collection_line_items.aggregate(pipeline).to_list(length=None)
    return [
        {
            "category": r["_id"]["category"] or "Uncategorized",
            "month": r["_id"]["month"],
            "value": round(r["total"] or 0, 2),
            "count": r["count"],
        }
        for r in results
    ]


def _month_bounds(month: str):
    """Parse ``YYYY-MM`` into (start, end) UTC datetimes, or raise 400."""
    try:
        year_str, month_str = month.split("-")
        year, month_int = int(year_str), int(month_str)
        last_day = calendar.monthrange(year, month_int)[1]
    except (ValueError, IndexError):
        raise HTTPException(status_code=400, detail="Invalid month; expected YYYY-MM")
    start = datetime(year, month_int, 1, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(year, month_int, last_day, 23, 59, 59, tzinfo=timezone.utc)
    return start, end


@router.get("/budget-progress/{month}")
async def get_budget_progress(
    month: str,
    tenant_id: str = Depends(get_tenant_id),
):
    """Budget vs. actual spend per category for a month (``YYYY-MM``).

    Unions categories from both sides so the UI surfaces (a) budgets with no
    spend yet and (b) spend in categories with no budget ("unbudgeted leaks").
    Implemented as actual-spend aggregation + a budgets fetch merged in Python
    (instead of a ``$lookup`` sub-pipeline) so it runs on the in-memory test DB.
    """
    start, end = _month_bounds(month)

    match = dict(_tenant_match_stage(tenant_id)["$match"])
    match["date"] = {"$gte": start, "$lte": end}
    spend_pipeline: List[Dict[str, Any]] = [
        {"$match": match},
        {
            "$group": {
                "_id": {"$ifNull": ["$category", "Uncategorized"]},
                "actual_amount": {"$sum": "$total_amount"},
            }
        },
    ]
    spend_rows = await collection_receipts.aggregate(spend_pipeline).to_list(length=500)
    actual = {r["_id"] or "Uncategorized": round(r["actual_amount"] or 0, 2) for r in spend_rows}

    budgets = await collection_budgets.find(
        {"tenant_id": tenant_id, "month": month}
    ).to_list(length=500)
    limits = {b["category"]: round(b.get("limit_amount", 0) or 0, 2) for b in budgets}

    rows = [
        {"category": category, "actual": actual.get(category, 0.0), "limit": limits.get(category, 0.0)}
        for category in (set(actual) | set(limits))
    ]
    rows.sort(key=lambda r: r["actual"], reverse=True)
    return rows


@router.get("/extraction-failures")
async def get_extraction_failures(
    tenant_id: str = Depends(get_tenant_id),
):
    """Receipts where OCR missed a key field — surfaces a review queue at the
    receipt level (item-level needs_review lands with vendor normalisation)."""
    tenant_filter = _tenant_match_stage(tenant_id)["$match"]
    query = {
        "$and": [
            tenant_filter,
            {
                "$or": [
                    {"merchant_name": None},
                    {"total_amount": None},
                    {"date": None},
                ]
            },
        ]
    }
    cursor = collection_receipts.find(query).sort("created_at", -1).limit(50)
    docs = await cursor.to_list(length=50)
    failures = []
    for d in docs:
        failures.append(
            {
                "id": str(d["_id"]),
                "merchant_name": d.get("merchant_name"),
                "total_amount": d.get("total_amount"),
                "date": d.get("date"),
                "confidence": d.get("confidence"),
                "created_at": d.get("created_at"),
            }
        )
    return failures
