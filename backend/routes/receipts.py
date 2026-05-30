import logging
import os
from datetime import datetime, timezone
from typing import List, Optional

from bson import ObjectId
from fastapi import APIRouter, UploadFile, File, HTTPException, Header, Response
from pymongo import ReturnDocument

import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import collection_receipts, collection_jobs
from models import Receipt, ReceiptUpdate, JobEnqueueResponse, JobStatusResponse
from receipt_parsing import find_line_items
from storage_paths import validate_tenant_id, save_job_upload
from tasks import process_receipt_job

logger = logging.getLogger(__name__)

router = APIRouter()


def _suffix_for_content_type(content_type: str) -> str:
    mapping = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }
    return mapping.get(content_type.split(";")[0].strip().lower(), ".bin")


def _tenant_query_filter(tenant_id: str) -> dict:
    if tenant_id == "default":
        return {"$or": [{"tenant_id": "default"}, {"tenant_id": {"$exists": False}}]}
    return {"tenant_id": tenant_id}


def _parse_tenant_header(x_tenant_id: Optional[str]) -> str:
    raw = (x_tenant_id or "default").strip()
    try:
        return validate_tenant_id(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid tenant id")


@router.post("/upload", response_model=JobEnqueueResponse)
async def upload_receipt(
    file: UploadFile = File(...),
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-ID"),
):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")

    tenant_id = _parse_tenant_header(x_tenant_id)
    contents = await file.read()
    if len(contents) > 15 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large")

    job_oid = ObjectId()
    job_id = str(job_oid)
    suffix = _suffix_for_content_type(file.content_type)

    try:
        storage_path = save_job_upload(tenant_id, job_id, contents, suffix)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid upload parameters")
    except OSError:
        logger.exception("Failed to persist upload job_id=%s", job_id)
        raise HTTPException(status_code=500, detail="Storage error")

    now = datetime.now(timezone.utc)
    job_doc = {
        "_id": job_oid,
        "tenant_id": tenant_id,
        "status": "queued",
        "raw_storage_path": storage_path,
        "original_filename": (file.filename or "upload")[:255],
        "content_type": file.content_type,
        "receipt_id": None,
        "error_message": None,
        "processing_ms": None,
        "created_at": now,
        "updated_at": now,
    }
    await collection_jobs.insert_one(job_doc)

    try:
        process_receipt_job.delay(job_id)
    except Exception:
        logger.exception("Failed to enqueue job_id=%s", job_id)
        await collection_jobs.update_one(
            {"_id": job_oid},
            {
                "$set": {
                    "status": "failed",
                    "error_message": "Queue unavailable",
                    "updated_at": datetime.now(timezone.utc),
                }
            },
        )
        raise HTTPException(status_code=503, detail="Processing queue unavailable")

    return JobEnqueueResponse(job_id=job_id, status="queued")


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(
    job_id: str,
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = _parse_tenant_header(x_tenant_id)
    try:
        oid = ObjectId(job_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid job id")

    doc = await collection_jobs.find_one({"_id": oid, "tenant_id": tenant_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Job not found")

    receipt_id = str(doc["receipt_id"]) if doc.get("receipt_id") else None
    return JobStatusResponse(
        job_id=str(doc["_id"]),
        tenant_id=doc.get("tenant_id", "default"),
        status=doc.get("status", "unknown"),
        receipt_id=receipt_id,
        error_message=doc.get("error_message"),
        processing_ms=doc.get("processing_ms"),
        created_at=doc.get("created_at"),
        completed_at=doc.get("completed_at"),
    )


@router.get("/", response_model=List[Receipt])
async def get_receipts(
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = _parse_tenant_header(x_tenant_id)
    receipts: List[Receipt] = []
    cursor = collection_receipts.find(_tenant_query_filter(tenant_id)).sort(
        "created_at", -1
    )
    async for document in cursor:
        document["id"] = str(document["_id"])
        if document.get("tenant_id") is None:
            document["tenant_id"] = "default"
        receipts.append(Receipt(**document))
    return receipts


def _receipt_object_id(receipt_id: str) -> ObjectId:
    try:
        return ObjectId(receipt_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid receipt id")


@router.patch("/{receipt_id}", response_model=Receipt)
async def update_receipt(
    receipt_id: str,
    payload: ReceiptUpdate,
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = _parse_tenant_header(x_tenant_id)
    oid = _receipt_object_id(receipt_id)

    update_fields = payload.model_dump(exclude_unset=True)
    if not update_fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    update_fields["updated_at"] = datetime.now(timezone.utc)

    query = {"_id": oid, **_tenant_query_filter(tenant_id)}
    document = await collection_receipts.find_one_and_update(
        query,
        {"$set": update_fields},
        return_document=ReturnDocument.AFTER,
    )
    if not document:
        raise HTTPException(status_code=404, detail="Receipt not found")

    document["id"] = str(document["_id"])
    if document.get("tenant_id") is None:
        document["tenant_id"] = "default"
    return Receipt(**document)


@router.post("/{receipt_id}/itemize", response_model=Receipt)
async def itemize_receipt(
    receipt_id: str,
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-ID"),
):
    """Derive an itemized product list from the receipt's stored OCR text."""
    tenant_id = _parse_tenant_header(x_tenant_id)
    oid = _receipt_object_id(receipt_id)

    query = {"_id": oid, **_tenant_query_filter(tenant_id)}
    existing = await collection_receipts.find_one(query)
    if not existing:
        raise HTTPException(status_code=404, detail="Receipt not found")

    items = find_line_items(existing.get("raw_text") or "")
    document = await collection_receipts.find_one_and_update(
        query,
        {"$set": {"items": items, "updated_at": datetime.now(timezone.utc)}},
        return_document=ReturnDocument.AFTER,
    )

    document["id"] = str(document["_id"])
    if document.get("tenant_id") is None:
        document["tenant_id"] = "default"
    return Receipt(**document)


@router.delete("/{receipt_id}", status_code=204)
async def delete_receipt(
    receipt_id: str,
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = _parse_tenant_header(x_tenant_id)
    oid = _receipt_object_id(receipt_id)

    query = {"_id": oid, **_tenant_query_filter(tenant_id)}
    result = await collection_receipts.delete_one(query)
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Receipt not found")
    return Response(status_code=204)
