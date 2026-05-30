from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException

import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import collection_receipts
from storage_paths import validate_tenant_id

router = APIRouter()


def _parse_tenant_header(x_tenant_id: Optional[str]) -> str:
    raw = (x_tenant_id or "default").strip()
    try:
        return validate_tenant_id(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid tenant id")


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
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = _parse_tenant_header(x_tenant_id)
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
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = _parse_tenant_header(x_tenant_id)
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
