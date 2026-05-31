"""Vendor review endpoints.

Vendor normalisation auto-creates a vendor (flagged ``needs_review``) the first time
it sees a merchant string. These endpoints let a tenant list those vendors and confirm
them (optionally folding in another alias so future receipts match), all tenant-scoped.
"""

import os
import sys
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auth import get_tenant_id
from database import collection_vendors
from vendor_normaliser import confirm_vendor_alias

router = APIRouter()


class ConfirmVendorRequest(BaseModel):
    alias: Optional[str] = None


def _serialise(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(doc["_id"]),
        "canonical_name": doc.get("canonical_name"),
        "aliases": doc.get("aliases", []),
        "category_default": doc.get("category_default"),
        "needs_review": doc.get("needs_review", False),
        "created_at": doc.get("created_at"),
    }


@router.get("/")
async def list_vendors(
    needs_review: Optional[bool] = None,
    tenant_id: str = Depends(get_tenant_id),
) -> List[Dict[str, Any]]:
    """List the tenant's vendors, newest first. Pass ?needs_review=true for the queue."""
    query: Dict[str, Any] = {"tenant_id": tenant_id}
    if needs_review is not None:
        query["needs_review"] = needs_review
    cursor = collection_vendors.find(query).sort("created_at", -1).limit(200)
    docs = await cursor.to_list(length=200)
    return [_serialise(d) for d in docs]


@router.post("/{vendor_id}/confirm")
async def confirm_vendor(
    vendor_id: str,
    payload: ConfirmVendorRequest = ConfirmVendorRequest(),
    tenant_id: str = Depends(get_tenant_id),
) -> Dict[str, Any]:
    """Mark a vendor reviewed and optionally add an alias so future names match it."""
    updated = await confirm_vendor_alias(
        collection_vendors, vendor_id, tenant_id, payload.alias
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Vendor not found")
    return {"status": "confirmed", "vendor_id": vendor_id}
