# Codebase Improvement Guide — OCR Expense Intelligence

> Comprehensive improvement plan for the working MVP → production-ready B2B platform  
> Stack: FastAPI · React · MongoDB · Celery · Redis · EasyOCR · Docker  
> Read alongside: `docs/ARCHITECTURE.md`

---

## Current State Assessment

**What is working well and should not be touched:**

- Async job pattern (`POST /receipts/upload` → `job_id` → poll) is architecturally correct
- Celery + Redis queue keeps OCR off the request path — the right design
- `mongomock-motor` test strategy means the suite (120+ tests) runs without Docker — keep this
- `storage_paths.py` with traversal-safe tenant-prefixed paths — solid foundation
- Docker Compose single-command startup — good developer experience
- Pydantic models in `models.py` — right tool, extend rather than replace

**What needs improvement, in priority order:**

0. **Step 0 (prerequisite):** capture OCR `confidence` + `currency`, and add `job_id`/`currency`/`confidence`/`needs_review` to receipt documents. Priority 1's `needs_review`, anomaly, and review-queue features are inert without this.
1. `line_items` collection (data foundation)
2. PDF support (user-facing gap)
3. Real authentication (security boundary)
4. OpenCV pre-processing (extraction quality)
5. Vendor normalisation (analytics accuracy)
6. API versioning (future-proofing)
7. S3 / MinIO file storage (scalability)
8. Rate limiting (production hardening)
9. Health endpoints (operational visibility)
10. Error handling audit (production polish)

> [!NOTE]
> **Implementation status (current):** Step 0 and Priorities **1, 2, 3, 4, 5, 8, 9, 10 are
> shipped and tested** (120 passing tests). **Remaining:** Priority **6** (`/v1/` API
> versioning) and Priority **7** (S3/MinIO object storage).
>
> This guide was written *before* implementation, so some samples and the convention
> table below describe the pre-change codebase (e.g. "there is no `auth.py` yet"). Those
> pieces now exist — treat the real code in `backend/auth.py`, `backend/routes/`,
> `backend/vendor_normaliser.py`, `backend/rate_limit.py`, etc. as the source of truth.

---

## How to read this guide (conventions)

> [!IMPORTANT]
> The code samples below are **illustrative** and several use patterns the current
> codebase does **not** use. Adapt them to the real conventions before pasting:
>
> | Sample shows | This repo actually uses |
> |---|---|
> | `tenant_id = Depends(get_tenant_id)` | `x_tenant_id = Header(default=None, alias="X-Tenant-ID")` → `_parse_tenant_header(...)`. There is no `get_tenant_id` dependency or `auth.py` yet (that's Priority 3). |
> | `db = Depends(get_db)` | Module-global Motor collections imported from `database.py` (`collection_receipts`, `collection_jobs`, …). There is no `get_db` provider. |
> | `settings.REDIS_URL` / `settings.ADMIN_KEY` | Direct `os.getenv(...)`. There is no `settings` module. |
> | `await db["line_items"]...` in the worker | The **worker is synchronous** (`tasks.py` uses PyMongo via `_db()`), so writes from the worker must be sync. Only the **API** (`routes/*.py`) is async Motor. |
> | `{"tenant_id": tenant_id}` match | For the `"default"` tenant the API uses `_tenant_query_filter` / `_tenant_match_stage`, which also matches docs missing the field. New `line_items` always set `tenant_id`, so a plain match is fine there. |
>
> Tenant matching, `_parse_tenant_header`, and the `_tenant_match_stage` helper
> already exist in `routes/receipts.py` and `routes/analytics.py` — reuse them.

---

## Step 0 — Capture confidence/currency + extend the receipt schema (prerequisite)

Priority 1 leans on fields the pipeline doesn't produce yet. Do this first or the
`needs_review`, anomaly, and review-queue features ship as no-ops.

**1. `ocr_engine.parse_receipt` — emit `confidence` and `currency`:**

```python
# ocr_engine.py
_CURRENCY_BY_SYMBOL = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "₹": "INR"}

def extract_confidence(ocr_result) -> float | None:
    probs = [p for _, _, p in ocr_result if isinstance(p, (int, float))]
    return round(sum(probs) / len(probs), 4) if probs else None

def detect_currency(text: str) -> str:
    for sym, code in _CURRENCY_BY_SYMBOL.items():
        if sym in text:
            return code
    return "USD"

# inside parse_receipt(...), before return:
data["confidence"] = extract_confidence(ocr_result)
data["currency"] = detect_currency(full_text)
```

**2. `tasks.py` — stamp the receipt with `job_id`, `currency`, `confidence`, `needs_review`:**

```python
parsed["tenant_id"] = tenant_id
parsed["job_id"] = jid                       # ObjectId of the job
parsed.setdefault("currency", "USD")
conf = parsed.get("confidence")
parsed["needs_review"] = conf is not None and conf < 0.75
parsed["created_at"] = datetime.now(timezone.utc)
ins = receipts.insert_one(parsed)
parsed["_id"] = ins.inserted_id             # keep for the line_items writer
```

**3. `models.py` — extend `Receipt` (all optional, additive — existing tests stay green):**

```python
class Receipt(ReceiptBase):
    ...
    job_id: Optional[PyObjectId] = None
    currency: Optional[str] = "USD"
    confidence: Optional[float] = None
    needs_review: bool = False
```

**4. Stamp `model_used`/`pages` on the job at creation** (`routes/receipts.py` `job_doc`)
and write `confidence` onto the job at completion (`tasks.py`). See Quick Win #1.

---

## Priority 1 — `line_items` Collection

### Why this is first

Every meaningful analytics feature — vendor spend trends, category breakdowns,
anomaly detection, the human review queue — requires line-item granularity. Your
current dashboard aggregates at the receipt level (`total_amount` per receipt per
merchant). That answers "how much did I spend at Walmart." It cannot answer "how much
did I spend on eggs, produce, and dairy across all stores" — which is the actual
product value proposition.

Build this before anything else. Every dashboard feature you add after this point
will be built on the right foundation. Adding it later means rewriting every
analytics query you wrote before it.

### Collection schema

```python
# One document per line item, written after each successful job
{
    "_id": ObjectId,
    "tenant_id": "acme",                    # leading key on all indexes
    "job_id": ObjectId,                     # FK to jobs collection
    "receipt_id": ObjectId,                 # FK to receipts collection
    "vendor_raw": "WALMART SUPERCENTER",    # raw string from OCR
    "vendor_id": ObjectId | None,           # resolved after normalisation (Phase 5)
    "description": "Organic Whole Milk",
    "quantity": 1.0,
    "unit_price": 4.99,
    "amount": 4.99,
    "currency": "USD",
    "category": "Groceries",               # from keyword categorizer
    "period": ISODate("2026-05-01"),        # normalized billing date
    "confidence": 0.91,                    # OCR confidence for this item
    "needs_review": False,                 # True if confidence < 0.75
    "created_at": ISODate
}
```

### New file: `backend/line_items_writer.py`

> The Celery worker is **synchronous PyMongo**, so this writer is sync too. `db` is the
> PyMongo database handle from `tasks._db()`; `db["line_items"]` is a sync collection.
> `find_line_items` emits `{description, amount, qty}` (no `unit_price`/per-item
> `confidence`), so the writer falls back to the receipt-level confidence for each item.

```python
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)


def write_line_items(db, receipt_doc: dict, items: list[dict], tenant_id: str) -> int:
    """
    Write one MongoDB document per extracted line item (synchronous PyMongo).
    Called by the Celery worker after a successful OCR job.
    `receipt_doc` must already include `_id` (set it from insert_one().inserted_id).
    Returns the number of documents written.
    """
    if not items:
        return 0

    overall_conf = receipt_doc.get("confidence")
    period = receipt_doc.get("date")  # datetime or None; used as-is for $dateToString
    now = datetime.now(timezone.utc)

    docs = []
    for item in items:
        amount = float(item.get("amount") or 0.0)
        conf = item.get("confidence", overall_conf)
        docs.append({
            "tenant_id": tenant_id,
            "job_id": receipt_doc.get("job_id"),
            "receipt_id": receipt_doc.get("_id"),
            "vendor_raw": receipt_doc.get("merchant_name"),
            "vendor_id": None,                       # populated by Priority 5
            "description": item.get("description"),
            "quantity": float(item.get("qty") or 1),
            "unit_price": item.get("unit_price"),
            "amount": amount,
            "currency": receipt_doc.get("currency", "USD"),
            "category": receipt_doc.get("category", "Uncategorized"),
            "period": period,
            "confidence": conf,
            "needs_review": conf is not None and conf < 0.75,
            "created_at": now,
        })

    result = db["line_items"].insert_many(docs)
    logger.info("line_items written count=%d job_id=%s",
                len(result.inserted_ids), receipt_doc.get("job_id"))
    return len(result.inserted_ids)
```

### Add indexes in `database.py`

The repo already has an async `ensure_indexes()` using module-global collections.
Add a `collection_line_items` handle and extend it (don't introduce a `create_indexes(db)`):

```python
# database.py
collection_line_items = database.line_items

async def ensure_indexes() -> None:
    try:
        await collection_jobs.create_index([("tenant_id", 1), ("created_at", -1)])
        await collection_jobs.create_index([("tenant_id", 1), ("status", 1)])
        await collection_receipts.create_index([("tenant_id", 1), ("created_at", -1)])
        await collection_receipts.create_index([("tenant_id", 1), ("needs_review", 1)])
        # --- new: line_items (tenant_id leads every index) ---
        await collection_line_items.create_index([("tenant_id", 1), ("period", -1)])
        await collection_line_items.create_index([("tenant_id", 1), ("vendor_id", 1), ("period", -1)])
        await collection_line_items.create_index([("tenant_id", 1), ("category", 1), ("period", -1)])
        await collection_line_items.create_index([("tenant_id", 1), ("needs_review", 1)])
        await collection_line_items.create_index([("tenant_id", 1), ("receipt_id", 1)])
    except Exception:
        logger.exception("Failed to create MongoDB indexes")
```

### Wire into `tasks.py`

The worker is synchronous, so call the sync writer directly — no event loop juggling.
This goes right after the receipt is inserted (see Step 0, which sets `parsed["_id"]`):

```python
from line_items_writer import write_line_items

# ... ocr_result = ocr_engine.extract_text_and_coords(contents)
# ... parsed = ocr_engine.parse_receipt(ocr_result)  # includes parsed["items"]
parsed["tenant_id"] = tenant_id
parsed["job_id"] = jid
parsed.setdefault("currency", "USD")
conf = parsed.get("confidence")
parsed["needs_review"] = conf is not None and conf < 0.75
parsed["created_at"] = datetime.now(timezone.utc)

ins = receipts.insert_one(parsed)
parsed["_id"] = ins.inserted_id

# `parsed["items"]` is what find_line_items produced — NOT "line_items"
try:
    write_line_items(db, parsed, parsed.get("items", []), tenant_id)
except Exception:
    logger.exception("line_items write failed job_id=%s", job_id)  # non-fatal
```

> **Bug fixed vs the original draft:** the key is `items` (what `find_line_items`
> returns), not `line_items`; and the worker no longer re-queries by `job_id`
> (receipts didn't store one until Step 0) — it reuses the inserted `_id`.

### New analytics endpoints in `backend/routes/analytics.py`

Use the module-global `collection_line_items`, the existing `_parse_tenant_header`
and `_tenant_match_stage` helpers, and the `X-Tenant-ID` header — matching `/monthly`
and `/merchant`. The router is mounted at `/analytics`, so these become
`/analytics/vendors`, etc. (no `/line-items/` segment, no `Depends`).

```python
from datetime import datetime, timedelta, timezone
from database import collection_line_items   # add to imports

@router.get("/vendors")
async def vendor_spend(
    days: int = 90,
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-ID"),
):
    """Top vendors by total spend over the last N days."""
    tenant_id = _parse_tenant_header(x_tenant_id)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    pipeline = [
        _tenant_match_stage(tenant_id),
        {"$match": {"period": {"$gte": since}}},
        {"$group": {
            "_id": "$vendor_raw",
            "total": {"$sum": "$amount"},
            "count": {"$sum": 1},
            "avg_amount": {"$avg": "$amount"},
        }},
        {"$sort": {"total": -1}},
        {"$limit": 20},
    ]
    results = await collection_line_items.aggregate(pipeline).to_list(length=None)
    return [{"name": r["_id"] or "Unknown", "value": r["total"],
             "count": r["count"], "avg": round(r["avg_amount"] or 0, 2)}
            for r in results]


@router.get("/categories")
async def category_by_month(
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-ID"),
):
    """Monthly spend broken down by category, from line items."""
    tenant_id = _parse_tenant_header(x_tenant_id)
    pipeline = [
        _tenant_match_stage(tenant_id),
        {"$match": {"period": {"$ne": None}}},
        {"$group": {
            "_id": {
                "category": "$category",
                "month": {"$dateToString": {"format": "%Y-%m", "date": "$period"}},
            },
            "total": {"$sum": "$amount"},
            "count": {"$sum": 1},
        }},
        {"$sort": {"_id.month": -1, "total": -1}},
    ]
    results = await collection_line_items.aggregate(pipeline).to_list(length=None)
    return [{"category": r["_id"]["category"] or "Uncategorized",
             "month": r["_id"]["month"], "value": r["total"], "count": r["count"]}
            for r in results]
```

> The anomaly (`$stdDevPop` z-score) and review-queue pipelines from the
> ARCHITECTURE doc are valid Mongo but **not supported by `mongomock`**, so they
> can't be unit-tested with the current in-memory harness. Defer them until there's
> enough real `line_items` volume to make them meaningful, or test against a real
> MongoDB in CI. `/extraction-failures` (Quick Win #2) covers the immediate
> review-dashboard need at the receipt level.

---

## Priority 2 — PDF Support

### Why this matters now

Your three test fixtures are all JPGs. Real-world invoices, utility bills, and
vendor statements are PDFs. If you demo this to any business user, the first thing
they'll drag in is a PDF. The fix is additive — it doesn't change the API contract
or any existing tests.

### Install

```bash
# requirements.txt — add
pdf2image>=1.17.0
```

```dockerfile
# Dockerfile / docker-compose.yml backend service — add
RUN apt-get update && apt-get install -y poppler-utils libgl1 --no-install-recommends
```

### New file: `backend/pdf_converter.py`

```python
import io
import logging
from pathlib import Path
from PIL import Image

logger = logging.getLogger(__name__)


def pdf_to_images(file_path: str | Path, dpi: int = 300) -> list[Image.Image]:
    """
    Convert a PDF file to a list of PIL Images (one per page) at 300 DPI.
    Requires poppler-utils to be installed in the Docker image.
    """
    try:
        from pdf2image import convert_from_path
        pages = convert_from_path(str(file_path), dpi=dpi)
        logger.info(f"PDF converted: {len(pages)} pages from {file_path}")
        return pages
    except Exception as exc:
        logger.error(f"PDF conversion failed for {file_path}: {exc}")
        raise


def load_image(file_path: str | Path) -> tuple[Image.Image, int]:
    """
    Load an image or PDF into a PIL Image.
    Returns (PIL Image of first page, total page count).
    For multi-page PDFs, only the first page is returned for now.
    """
    path = Path(file_path)
    suffix = path.suffix.lower()

    if suffix == ".pdf":
        pages = pdf_to_images(path)
        return pages[0], len(pages)
    else:
        img = Image.open(path).convert("RGB")
        return img, 1
```

### Update upload validation in `routes/receipts.py`

```python
ALLOWED_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/tiff",
    "application/pdf",
}

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tiff", ".tif", ".pdf"}

@router.post("/upload")
async def upload_receipt(file: UploadFile, ...):
    # Validate content type
    if file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type '{file.content_type}'. "
                   f"Accepted: JPEG, PNG, WEBP, TIFF, PDF."
        )

    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file extension '{suffix}'."
        )
    # ... rest of upload logic unchanged
```

### Update `tasks.py` worker entry point

```python
from pdf_converter import load_image

# Replace direct Image.open() with:
pil_image, total_pages = load_image(file_path)

# Update job document with page count
db["jobs"].update_one(
    {"_id": job_id},
    {"$set": {"pages": total_pages}}
)

# Pass pil_image to existing EasyOCR pipeline unchanged
```

---

## Priority 3 — Real Authentication

### Why this is a security boundary, not a feature

`X-Tenant-ID: acme` supplied by a client means any caller can impersonate any
tenant and read their receipts, analytics, and jobs. This is acceptable in local
development. It is not acceptable once real user data is involved.

The minimum viable implementation: API keys stored hashed in MongoDB, validated
on every request via a FastAPI dependency, mapped server-side to `tenant_id`. The
`X-Tenant-ID` header becomes a dev-only convenience fallback.

### New file: `backend/auth.py`

```python
import hashlib
import secrets
import logging
from datetime import datetime, timezone
from fastapi import Header, HTTPException, Depends
from database import get_db

logger = logging.getLogger(__name__)

# ── Key generation ────────────────────────────────────────────────────────

def generate_api_key() -> tuple[str, str]:
    """
    Generate a new API key.
    Returns (raw_key, hashed_key).
    Store only the hashed_key in MongoDB.
    Show raw_key to the user exactly once.
    """
    raw = "ext_" + secrets.token_hex(32)     # "ext_" prefix for easy identification
    hashed = _hash_key(raw)
    return raw, hashed


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


# ── FastAPI dependency ────────────────────────────────────────────────────

async def get_tenant_id(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    db=Depends(get_db),
) -> str:
    """
    Resolve tenant_id from the request.

    Priority:
      1. X-API-Key header  → look up in tenants collection (production path)
      2. X-Tenant-ID header → dev/testing convenience only
      3. Default to "default" tenant

    Replace this function's fallback behaviour once auth is enforced.
    """
    if x_api_key:
        hashed = _hash_key(x_api_key)
        tenant = await db["tenants"].find_one(
            {"api_key_hash": hashed, "active": True}
        )
        if not tenant:
            raise HTTPException(status_code=401, detail="Invalid or revoked API key.")
        # Update last_seen without blocking the request
        await db["tenants"].update_one(
            {"_id": tenant["_id"]},
            {"$set": {"last_seen_at": datetime.now(timezone.utc)}}
        )
        return tenant["tenant_id"]

    # Dev fallback — remove this branch when enforcing auth in production
    if x_tenant_id:
        logger.warning(
            "Request authenticated via X-Tenant-ID header (dev mode only)",
            extra={"tenant_id": x_tenant_id}
        )
        return x_tenant_id

    return "default"
```

### `tenants` collection schema

```python
# One document per registered tenant / API key
{
    "_id": ObjectId,
    "tenant_id": "acme-corp",               # stable, human-readable identifier
    "name": "Acme Corporation",
    "email": "admin@acme.com",
    "api_key_hash": "sha256hexstring",      # NEVER store the raw key
    "active": True,
    "plan": "starter",                      # starter | growth | enterprise
    "created_at": ISODate,
    "last_seen_at": ISODate,
}
```

### Admin endpoint to issue keys (protect this route separately)

```python
# backend/routes/admin.py
@router.post("/admin/tenants", include_in_schema=False)
async def create_tenant(
    name: str,
    email: str,
    plan: str = "starter",
    db=Depends(get_db),
    admin_key: str = Header(alias="X-Admin-Key"),
):
    """Issue a new tenant and API key. Admin-only."""
    if admin_key != settings.ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Forbidden.")

    raw_key, hashed_key = generate_api_key()
    tenant_id = slugify(name)

    await db["tenants"].insert_one({
        "tenant_id": tenant_id,
        "name": name,
        "email": email,
        "api_key_hash": hashed_key,
        "active": True,
        "plan": plan,
        "created_at": datetime.now(timezone.utc),
        "last_seen_at": None,
    })

    # Return raw key exactly once — it cannot be recovered after this response
    return {
        "tenant_id": tenant_id,
        "api_key": raw_key,
        "warning": "Store this key securely. It will not be shown again."
    }
```

### Add index

```python
await db["tenants"].create_index([("api_key_hash", 1)], unique=True)
await db["tenants"].create_index([("tenant_id", 1)], unique=True)
```

### Wire into existing routes

Replace every instance of `tenant_id: str = Header(alias="X-Tenant-ID")` in your
route files with `tenant_id: str = Depends(get_tenant_id)`. The behaviour is
identical for existing clients using the header; the API key path is additive.

---

## Priority 4 — OpenCV Pre-processing

### Why this improves EasyOCR without changing anything else

Real receipt photos are skewed, low-contrast, and noisy. EasyOCR performance
degrades significantly on these. Pre-processing is CPU-only, adds 200–400ms in the
worker (not in the API path), and requires no API contract changes. Your
`receipt_blurry.jpg` test fixture exists precisely because this is a known gap.

### Install

```bash
# requirements.txt — add
opencv-python-headless>=4.9.0
```

### New file: `backend/preprocess.py`

```python
import cv2
import numpy as np
from PIL import Image
import logging

logger = logging.getLogger(__name__)


def preprocess_receipt(pil_image: Image.Image) -> Image.Image:
    """
    Apply deskew, denoise, and contrast normalisation to a receipt image.
    Input and output are PIL Images. Drop-in replacement for Image.open() result.
    """
    img = np.array(pil_image.convert("RGB"))
    original_shape = img.shape

    img = _deskew(img)
    img = _denoise(img)
    img = _normalise_contrast(img)

    logger.debug(
        "preprocess complete",
        extra={"original_shape": original_shape, "output_shape": img.shape}
    )
    return Image.fromarray(img)


def _deskew(img: np.ndarray) -> np.ndarray:
    """Detect and correct page rotation using Hough line transform."""
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    lines = cv2.HoughLinesP(
        edges, 1, np.pi / 180,
        threshold=100, minLineLength=100, maxLineGap=10
    )
    if lines is None:
        return img

    angles = []
    for x1, y1, x2, y2 in lines[:, 0]:
        angle = np.degrees(np.arctan2(y2 - y1, x2 - x1))
        if abs(angle) < 45:
            angles.append(angle)

    if not angles:
        return img

    median_angle = float(np.median(angles))
    if abs(median_angle) < 0.5:
        return img

    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w // 2, h // 2), median_angle, 1.0)
    return cv2.warpAffine(
        img, M, (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE
    )


def _denoise(img: np.ndarray) -> np.ndarray:
    """Remove noise while preserving text edges."""
    return cv2.fastNlMeansDenoisingColored(img, None, 10, 10, 7, 21)


def _normalise_contrast(img: np.ndarray) -> np.ndarray:
    """Apply CLAHE contrast enhancement in LAB color space."""
    lab = cv2.cvtColor(img, cv2.COLOR_RGB2LAB)
    l_channel, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l_channel = clahe.apply(l_channel)
    return cv2.cvtColor(cv2.merge([l_channel, a, b]), cv2.COLOR_LAB2RGB)
```

### Wire into `tasks.py` — one line change

```python
from preprocess import preprocess_receipt
from pdf_converter import load_image

# Replace:
pil_image = Image.open(file_path)

# With:
pil_image, total_pages = load_image(file_path)
pil_image = preprocess_receipt(pil_image)      # ← add this line

# Then pass pil_image to EasyOCR exactly as before
```

### Add pre-processing test

```python
# backend/tests/test_preprocess.py
from PIL import Image, ImageDraw
from preprocess import preprocess_receipt


def _make_skewed_image(angle_deg: float = 5.0) -> Image.Image:
    img = Image.new("RGB", (400, 200), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.text((50, 80), "TOTAL  $47.83", fill=(0, 0, 0))
    return img.rotate(angle_deg, expand=False, fillcolor=(255, 255, 255))


def test_preprocess_returns_pil_image():
    img = _make_skewed_image()
    result = preprocess_receipt(img)
    assert isinstance(result, Image.Image)


def test_preprocess_preserves_dimensions():
    img = _make_skewed_image()
    result = preprocess_receipt(img)
    # Allow small dimension variance from rotation crop
    assert abs(result.width - img.width) <= 10
    assert abs(result.height - img.height) <= 10


def test_preprocess_does_not_crash_on_clean_image():
    img = Image.new("RGB", (400, 200), color=(255, 255, 255))
    result = preprocess_receipt(img)
    assert result is not None
```

---

## Priority 5 — Vendor Normalisation

### Why raw merchant names break analytics

EasyOCR will give you "WALMART SUPERCENTER", "Walmart #4821", "WAL-MART",
and "walmart" for the same store. Without normalisation, your top-merchant chart
shows four entries for one merchant, and vendor-level spend totals are wrong.
This is a 100-line fix that transforms analytics accuracy.

### Install

```bash
# requirements.txt — add
rapidfuzz>=3.6.0
```

### New file: `backend/vendor_normaliser.py`

```python
import logging
from datetime import datetime, timezone
from bson import ObjectId
from rapidfuzz import fuzz, process as fuzz_process

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = 85      # 0–100; above this = same vendor
REDIS_TTL_SECONDS = 86400      # cache resolved mappings for 24 hours
REDIS_KEY_PREFIX = "vendor_norm:"


async def resolve_vendor(
    db,
    redis_client,
    raw_name: str,
    tenant_id: str,
) -> str | None:
    """
    Resolve a raw merchant name string to a canonical vendor_id.

    Strategy:
      1. Check Redis cache (raw_name → vendor_id)
      2. Fuzzy-match against vendors.aliases in MongoDB
      3. On match >= threshold: return existing vendor_id
      4. On no match: create new vendor, flag for review
    """
    if not raw_name or not raw_name.strip():
        return None

    normalised = raw_name.strip().upper()
    cache_key = f"{REDIS_KEY_PREFIX}{tenant_id}:{normalised}"

    # 1. Redis cache check
    cached = await redis_client.get(cache_key)
    if cached:
        return cached.decode()

    # 2. Load all vendor aliases for this tenant
    vendors = await db["vendors"].find(
        {"tenant_id": tenant_id},
        {"_id": 1, "canonical_name": 1, "aliases": 1}
    ).to_list(length=1000)

    # Build flat list: (alias_string, vendor_id_string)
    alias_pairs = []
    for v in vendors:
        for alias in v.get("aliases", []):
            alias_pairs.append((alias.upper(), str(v["_id"])))

    # 3. Fuzzy match
    if alias_pairs:
        alias_strings = [p[0] for p in alias_pairs]
        match_result = fuzz_process.extractOne(
            normalised,
            alias_strings,
            scorer=fuzz.token_sort_ratio,
        )
        if match_result and match_result[1] >= SIMILARITY_THRESHOLD:
            matched_alias, score, idx = match_result
            vendor_id = alias_pairs[idx][1]
            logger.debug(
                f"Vendor matched: '{raw_name}' → '{matched_alias}' "
                f"(score={score}, vendor_id={vendor_id})"
            )
            # Cache and return
            await redis_client.setex(cache_key, REDIS_TTL_SECONDS, vendor_id)
            return vendor_id

    # 4. No match — create new vendor, flag for human review
    result = await db["vendors"].insert_one({
        "tenant_id": tenant_id,
        "canonical_name": raw_name.strip(),
        "aliases": [normalised],
        "needs_review": True,
        "category_default": None,
        "created_at": datetime.now(timezone.utc),
    })
    vendor_id = str(result.inserted_id)
    logger.info(
        f"New vendor created: '{raw_name}' (tenant={tenant_id}, "
        f"vendor_id={vendor_id}) — flagged for review"
    )

    # Cache the new mapping
    await redis_client.setex(cache_key, REDIS_TTL_SECONDS, vendor_id)
    return vendor_id


async def confirm_vendor_alias(
    db,
    redis_client,
    vendor_id: str,
    new_alias: str,
    tenant_id: str,
) -> None:
    """
    Human confirms a vendor alias mapping via the review UI.
    Adds alias to vendor document and clears relevant Redis cache.
    """
    normalised = new_alias.strip().upper()
    await db["vendors"].update_one(
        {"_id": ObjectId(vendor_id)},
        {
            "$addToSet": {"aliases": normalised},
            "$set": {"needs_review": False},
        }
    )
    cache_key = f"{REDIS_KEY_PREFIX}{tenant_id}:{normalised}"
    await redis_client.delete(cache_key)
    logger.info(f"Vendor alias confirmed: '{new_alias}' → vendor_id={vendor_id}")
```

### `vendors` collection schema

```python
{
    "_id": ObjectId,
    "tenant_id": "acme",
    "canonical_name": "Walmart",
    "aliases": ["WALMART", "WALMART SUPERCENTER", "WAL-MART", "WALMART #4821"],
    "category_default": "Groceries",
    "needs_review": False,
    "created_at": ISODate,
}
```

### Add indexes

```python
await db["vendors"].create_index([("tenant_id", 1), ("aliases", 1)])
await db["vendors"].create_index([("tenant_id", 1), ("needs_review", 1)])
```

### Wire into `line_items_writer.py`

```python
from vendor_normaliser import resolve_vendor

async def write_line_items(db, redis_client, receipt_doc, line_items, job_id, tenant_id):
    vendor_id = await resolve_vendor(
        db, redis_client,
        raw_name=receipt_doc.get("merchant_name"),
        tenant_id=tenant_id,
    )
    # ... rest of writer, set vendor_id on each doc
```

---

## Priority 6 — API Versioning

### The 10-minute change that prevents future pain

Add a `/v1/` prefix now via a FastAPI `APIRouter`. This costs almost nothing today
and prevents breaking existing clients when you need to change response shapes in
the future. Keep the old routes as aliases temporarily.

### `backend/main.py`

```python
from fastapi import FastAPI
from routes import receipts, analytics

app = FastAPI(title="Extracta AI", version="1.0.0")

# Versioned routes (canonical going forward)
app.include_router(receipts.router, prefix="/v1")
app.include_router(analytics.router, prefix="/v1")

# Legacy routes (keep for backward compatibility, deprecate in README)
app.include_router(receipts.router, prefix="", include_in_schema=False)
app.include_router(analytics.router, prefix="", include_in_schema=False)
```

### Add deprecation headers on legacy routes

```python
from fastapi import Response

@router.get("/receipts/")
async def list_receipts_legacy(response: Response, ...):
    response.headers["Deprecation"] = "true"
    response.headers["Link"] = '</v1/receipts/>; rel="successor-version"'
    return await list_receipts(...)    # delegate to v1 handler
```

### Add `model_used` and `pages` to job documents now

Even before VLM or PDF support is complete, stamp these fields on every job
document for schema consistency:

```python
# tasks.py — in the initial job creation
await db["jobs"].insert_one({
    ...existing fields...,
    "model_used": "easyocr",
    "pages": 1,
    "processing_ms": None,
    "confidence": None,
})
```

---

## Priority 7 — S3 / MinIO File Storage

### Why local disk limits you

`UPLOAD_ROOT` on local disk means your API cannot run as multiple replicas — they
don't share a filesystem. MinIO is the right development path: it's S3-compatible,
runs as a Docker container, requires zero AWS credentials, and switching to
production S3 is one env var change.

### Add MinIO to `docker-compose.yml`

```yaml
services:
  minio:
    image: minio/minio:latest
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin
    ports:
      - "9000:9000"
      - "9001:9001"    # MinIO console
    volumes:
      - minio_data:/data
    healthcheck:
      test: ["CMD", "mc", "ready", "local"]
      interval: 10s
      timeout: 5s
      retries: 3

volumes:
  minio_data:
```

### Install

```bash
# requirements.txt — add
boto3>=1.34
```

### New file: `backend/object_storage.py`

```python
import os
import boto3
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Reads from environment — same vars work for MinIO and AWS S3
S3_ENDPOINT = os.getenv("S3_ENDPOINT_URL")       # None for AWS, set for MinIO
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY_ID", "minioadmin")
S3_SECRET_KEY = os.getenv("S3_SECRET_ACCESS_KEY", "minioadmin")
S3_BUCKET = os.getenv("S3_BUCKET", "extracta-receipts")
S3_REGION = os.getenv("S3_REGION", "us-east-1")


def _get_client():
    kwargs = dict(
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        region_name=S3_REGION,
    )
    if S3_ENDPOINT:
        kwargs["endpoint_url"] = S3_ENDPOINT
    return boto3.client("s3", **kwargs)


def upload_file(local_path: str | Path, s3_key: str) -> str:
    """Upload a file to S3/MinIO. Returns the s3_key."""
    client = _get_client()
    client.upload_file(str(local_path), S3_BUCKET, s3_key)
    logger.info(f"Uploaded to S3: {s3_key}")
    return s3_key


def download_file(s3_key: str, local_path: str | Path) -> None:
    """Download a file from S3/MinIO to a local path."""
    client = _get_client()
    client.download_file(S3_BUCKET, s3_key, str(local_path))


def get_presigned_url(s3_key: str, expires_in: int = 900) -> str:
    """Generate a presigned URL for client download (default 15 min expiry)."""
    client = _get_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": S3_BUCKET, "Key": s3_key},
        ExpiresIn=expires_in,
    )


def build_raw_key(tenant_id: str, job_id: str, filename: str) -> str:
    return f"raw/{tenant_id}/{job_id}/{filename}"


def build_result_key(tenant_id: str, job_id: str) -> str:
    return f"results/{tenant_id}/{job_id}/output.json"
```

### Updated environment variables

```bash
# .env.example — add these
S3_ENDPOINT_URL=http://minio:9000    # MinIO (Docker); remove for AWS S3
S3_ACCESS_KEY_ID=minioadmin
S3_SECRET_ACCESS_KEY=minioadmin
S3_BUCKET=extracta-receipts
S3_REGION=us-east-1
```

### Migration path

The transition from local disk to S3 is additive:

1. Add `s3_raw_key` and `s3_result_key` fields to job documents (nullable)
2. Write new uploads to both local disk and S3 during the transition
3. Update worker to read from S3 when `s3_raw_key` is set, fall back to local disk
4. Once all workers are updated, remove local disk writes
5. Remove `UPLOAD_ROOT` from the codebase

---

## Priority 8 — Rate Limiting

### Install

```bash
# requirements.txt — add
slowapi>=0.1.9
```

### Wire into `main.py`

```python
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(
    key_func=get_remote_address,    # replace with get_tenant_id after auth lands
    storage_uri=settings.REDIS_URL,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
```

### Apply limits per route

```python
# routes/receipts.py
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@router.post("/upload")
@limiter.limit("20/minute")          # adjust per plan tier
async def upload_receipt(request: Request, ...):
    ...

@router.get("/jobs/{job_id}")
@limiter.limit("120/minute")         # polling can be faster
async def get_job_status(request: Request, ...):
    ...
```

### Per-plan rate limits (after auth lands)

```python
PLAN_LIMITS = {
    "starter":    {"upload": "10/minute",  "poll": "60/minute"},
    "growth":     {"upload": "50/minute",  "poll": "300/minute"},
    "enterprise": {"upload": "200/minute", "poll": "1000/minute"},
}
```

---

## Priority 9 — Health Endpoints

### Add to `main.py`

```python
import time
from motor.motor_asyncio import AsyncIOMotorClient
import redis.asyncio as aioredis

@app.get("/health", tags=["ops"])
async def health_check():
    """Basic liveness — returns 200 if the process is running."""
    return {"status": "ok", "timestamp": time.time()}


@app.get("/health/ready", tags=["ops"])
async def readiness_check(db=Depends(get_db)):
    """
    Readiness — checks MongoDB and Redis connectivity.
    Returns 503 if either dependency is unreachable.
    Used by Docker healthchecks and load balancer health probes.
    """
    checks = {}

    # MongoDB
    try:
        await db.command("ping")
        checks["mongodb"] = "ok"
    except Exception as exc:
        checks["mongodb"] = f"error: {exc}"

    # Redis
    try:
        redis = aioredis.from_url(settings.REDIS_URL)
        await redis.ping()
        await redis.aclose()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {exc}"

    all_ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if all_ok else 503,
        content={"status": "ready" if all_ok else "degraded", "checks": checks}
    )
```

### Add to `docker-compose.yml`

```yaml
services:
  api:
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health/ready"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s
```

---

## Priority 10 — Error Handling Audit

### Consistent error response shape

```python
# backend/errors.py — add this module

from fastapi import Request
from fastapi.responses import JSONResponse
import logging

logger = logging.getLogger(__name__)


class ExtractaError(Exception):
    """Base exception for all application errors."""
    def __init__(self, message: str, status_code: int = 400, code: str = "ERROR"):
        self.message = message
        self.status_code = status_code
        self.code = code
        super().__init__(message)


class TenantNotFoundError(ExtractaError):
    def __init__(self):
        super().__init__("Tenant not found.", 404, "TENANT_NOT_FOUND")

class JobNotFoundError(ExtractaError):
    def __init__(self, job_id: str):
        super().__init__(f"Job '{job_id}' not found.", 404, "JOB_NOT_FOUND")

class ReceiptNotFoundError(ExtractaError):
    def __init__(self, receipt_id: str):
        super().__init__(f"Receipt '{receipt_id}' not found.", 404, "RECEIPT_NOT_FOUND")

class UnsupportedFileTypeError(ExtractaError):
    def __init__(self, content_type: str):
        super().__init__(
            f"Unsupported file type '{content_type}'. Accepted: JPEG, PNG, WEBP, TIFF, PDF.",
            415, "UNSUPPORTED_FILE_TYPE"
        )


def error_response(message: str, code: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}}
    )


async def extracta_error_handler(request: Request, exc: ExtractaError):
    return error_response(exc.message, exc.code, exc.status_code)


async def unhandled_error_handler(request: Request, exc: Exception):
    logger.exception(f"Unhandled error on {request.method} {request.url.path}")
    return error_response(
        "An unexpected error occurred. Please try again.",
        "INTERNAL_ERROR",
        500
    )
```

```python
# main.py — register handlers
from errors import ExtractaError, extracta_error_handler, unhandled_error_handler

app.add_exception_handler(ExtractaError, extracta_error_handler)
app.add_exception_handler(Exception, unhandled_error_handler)
```

---

## Quick Wins — Do These This Week

Each item below is under 2 hours and improves the project materially.

### 1. Stamp `model_used` and `pages` on all job documents

```python
# tasks.py — add to initial job creation and final update
{
    "model_used": "easyocr",
    "pages": 1,
    "processing_ms": None,   # set at completion
    "confidence": None,       # set at completion
}
```

### 2. Add `GET /analytics/extraction-failures` endpoint

Router is mounted at `/analytics`, so this is `/analytics/extraction-failures`.
Combine the `_tenant_match_stage` `$or` with the null-field `$or` via `$and`, and
stringify `_id` so it serialises cleanly:

```python
@router.get("/extraction-failures")
async def extraction_failures(
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-ID"),
):
    """Receipts where OCR failed to extract key fields — for the review dashboard."""
    tenant_id = _parse_tenant_header(x_tenant_id)
    tenant_filter = _tenant_match_stage(tenant_id)["$match"]
    query = {"$and": [
        tenant_filter,
        {"$or": [{"merchant_name": None}, {"total_amount": None}, {"date": None}]},
    ]}
    docs = await collection_receipts.find(query).sort("created_at", -1).limit(50).to_list(50)
    for d in docs:
        d["id"] = str(d.pop("_id"))
    return docs
```

### 3. Sanitise logging — never log full OCR text at INFO

```python
# tasks.py and ocr_engine.py — replace:
logger.info(f"OCR result: {full_text}")

# With:
logger.debug(f"OCR result: {full_text[:50]}...")   # truncate; debug level only
logger.info(f"OCR complete: {len(full_text)} chars, job_id={job_id}")
```

### 4. Add `GET /receipts/` pagination

> [!WARNING]
> **Breaking change.** The current endpoint returns a bare JSON array (`List[Receipt]`)
> and the React `ReceiptsList` plus several tests assume `res.data` is an array.
> Switching to `{items, total, ...}` breaks both. Either (a) keep returning a list and
> add optional `limit`/`skip` query params only, or (b) bump this behind the `/v1/`
> prefix (Priority 6) and migrate the frontend in the same change. Don't paste this
> as-is onto the legacy route.

```python
@router.get("/receipts/")
async def list_receipts(
    tenant_id: str = Depends(get_tenant_id),
    limit: int = Query(default=20, le=100),
    skip: int = Query(default=0, ge=0),
    db=Depends(get_db),
):
    receipts = await db["receipts"].find(
        {"tenant_id": tenant_id}
    ).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)

    total = await db["receipts"].count_documents({"tenant_id": tenant_id})

    return {
        "items": receipts,
        "total": total,
        "limit": limit,
        "skip": skip,
        "has_more": (skip + limit) < total,
    }
```

### 5. Add `.env.example` completeness check

```bash
# .env.example — ensure all new variables are documented
MONGODB_URL=mongodb://mongo:27017
REDIS_URL=redis://redis:6379/0
UPLOAD_ROOT=/data/uploads
ALLOWED_ORIGINS=http://localhost:3000,http://localhost:5173
VITE_API_URL=http://localhost:8000
ADMIN_KEY=change-me-in-production        # NEW: for admin endpoints
S3_ENDPOINT_URL=http://minio:9000        # NEW: MinIO (remove for AWS S3)
S3_ACCESS_KEY_ID=minioadmin              # NEW
S3_SECRET_ACCESS_KEY=minioadmin          # NEW
S3_BUCKET=extracta-receipts             # NEW
S3_REGION=us-east-1                     # NEW
```

---

## New Tests to Write

Your 53 existing tests must stay green throughout all changes. New tests:

```
backend/tests/
├── test_line_items_writer.py     # write_line_items() with mongomock
├── test_vendor_normaliser.py     # fuzzy match logic (mock Redis + mongomock)
├── test_auth.py                  # API key generation, hashing, lookup
├── test_preprocess.py            # OpenCV pipeline on PIL fixtures
├── test_pdf_converter.py         # PDF → PIL Image (requires poppler in CI)
├── test_health.py                # /health and /health/ready endpoints
└── test_pagination.py            # GET /receipts/ limit/skip/has_more
```

Run guard pattern for tests with heavy dependencies:

```python
import pytest, os

@pytest.mark.skipif(
    not os.getenv("RUN_PDF_TESTS"),
    reason="Set RUN_PDF_TESTS=1 to run (requires poppler)"
)
def test_pdf_to_image():
    ...
```

---

## Implementation Sequence

Work in this order to minimise rework. Each step is independent and deployable
without the next.

| Week | Work | Unlocks |
|------|------|---------|
| 1 | **Step 0** — confidence/currency + receipt schema + stamp `model_used`/`pages` | Prerequisite for line_items, needs_review, anomalies |
| 1 | `line_items_writer.py` + indexes + `/analytics/vendors`,`/categories` | The core analytics differentiator |
| 1 | `preprocess.py` + wire into worker | Better EasyOCR quality on real receipts |
| 2 | PDF support (`pdf_converter.py`) | Real invoice uploads work |
| 2 | Real authentication (`auth.py` + `tenants`) — **promote earlier than original plan** | Safe to show to real users |
| 3 | `vendor_normaliser.py` + `vendors` collection | Accurate vendor spend aggregations |
| 3 | API versioning (`/v1/` prefix) | Safe to evolve API without breaking clients |
| 4 | Real authentication (`auth.py` + `tenants` collection) | Safe to show to real users |
| 4 | Rate limiting (`slowapi`) | Safe to expose publicly |
| 5 | MinIO + `object_storage.py` | Multi-replica capable, production-ready storage |
| 5 | Health endpoints + Docker healthchecks | Operational visibility |
| 6 | Error handling audit (`errors.py`) | Consistent, debuggable error responses |

---

## What to Skip Until Later

These are all valid eventually but deliver less value than the above right now:

- **Webhooks** — useful for B2B integrations, but you have no B2B customers yet. Add after auth.
- **Celery priority lanes** — single queue is fine until you have multiple tenant tiers.
- **Full `tenants` CRUD UI** — admin endpoint to issue keys is sufficient. Self-serve signup is a product decision.
- **ClickHouse / PostgreSQL analytics store** — only needed when MongoDB `$lookup` chains become painful. Not there yet.
- **SOC 2 preparation** — correct for enterprise, premature for MVP.

---

*Last updated: May 2026 — based on OCR Expense Intelligence README + docs/ARCHITECTURE.md*
