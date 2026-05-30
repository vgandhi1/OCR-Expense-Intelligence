import logging
import os
import sys
import time
from datetime import datetime, timezone

from bson import ObjectId
from pymongo import MongoClient

# Ensure the app directory is importable from Celery prefork workers, whose
# sys.path does not reliably include the working directory. Mirrors the pattern
# used in routes/*.py so `import ocr_engine` resolves regardless of how the
# worker process was spawned.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from celery_app import celery_app

logger = logging.getLogger(__name__)

_mongo_url = os.getenv("MONGODB_URL", "mongodb://mongo:27017")


def _db():
    client = MongoClient(_mongo_url)
    return client.expense_intelligence


@celery_app.task(name="tasks.process_receipt_job", bind=True)
def process_receipt_job(self, job_id: str) -> None:
    db = _db()
    jobs = db.jobs
    receipts = db.receipts
    start = time.perf_counter()
    try:
        jid = ObjectId(job_id)
    except Exception:
        logger.exception("Invalid job_id passed to worker")
        return

    job = jobs.find_one({"_id": jid})
    if not job:
        logger.error("Job not found: %s", job_id)
        return

    jobs.update_one(
        {"_id": jid},
        {"$set": {"status": "processing", "updated_at": datetime.now(timezone.utc)}},
    )

    path = job.get("raw_storage_path")
    tenant_id = job.get("tenant_id", "default")
    if not path or not os.path.isfile(path):
        jobs.update_one(
            {"_id": jid},
            {
                "$set": {
                    "status": "failed",
                    "error_message": "Source file missing",
                    "completed_at": datetime.now(timezone.utc),
                    "processing_ms": int((time.perf_counter() - start) * 1000),
                }
            },
        )
        return

    try:
        import ocr_engine

        with open(path, "rb") as f:
            contents = f.read()
        ocr_result = ocr_engine.extract_text_and_coords(contents)
        parsed = ocr_engine.parse_receipt(ocr_result)
        parsed["tenant_id"] = tenant_id
        parsed["created_at"] = datetime.now(timezone.utc)
        ins = receipts.insert_one(parsed)
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        jobs.update_one(
            {"_id": jid},
            {
                "$set": {
                    "status": "complete",
                    "receipt_id": ins.inserted_id,
                    "completed_at": datetime.now(timezone.utc),
                    "processing_ms": elapsed_ms,
                    "error_message": None,
                }
            },
        )
    except Exception:
        logger.exception("OCR job failed job_id=%s", job_id)
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        jobs.update_one(
            {"_id": jid},
            {
                "$set": {
                    "status": "failed",
                    "error_message": "Processing failed",
                    "completed_at": datetime.now(timezone.utc),
                    "processing_ms": elapsed_ms,
                }
            },
        )
