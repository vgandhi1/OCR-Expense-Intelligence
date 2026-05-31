# Extracta AI — Architecture Reference (No VLM)

> High-level architecture for the OCR Expense Intelligence platform  
> Stack: FastAPI · React · MongoDB · Celery · Redis · EasyOCR · OpenCV · Docker  
> Scope: Image pre-processing → OCR extraction → itemized bill parsing → analytics  
> VLM / GPU inference intentionally excluded from this version

> [!IMPORTANT]
> **This document describes the _target_ architecture, not the current code.**
> It is the north-star design we are building toward. Sections marked 🔭 **(planned)**
> are not yet implemented. The phased path from the current MVP to this target — with an
> honest snapshot of what exists today versus what is aspirational — is in
> [`CODEBASE_IMPROVEMENTS.md`](CODEBASE_IMPROVEMENTS.md).
>
> **Implemented today:** async upload→`job_id`→poll flow, Celery+Redis worker, EasyOCR
> extraction with confidence/currency, **OpenCV pre-processing** (Layer 4), **PDF ingestion**
> (poppler/`pdf2image`), geometric total detection, keyword categorization, rule-based
> line-item extraction (`receipt_parsing.py`) fanned into a **`line_items` collection**,
> receipt + item + vendor + category analytics, CSV/Excel export, **RapidFuzz vendor
> normalisation** (Layer 9) with the `vendors` collection, **API-key authentication**
> (Layer 10) with the `tenants` collection, **per-tenant rate limiting** (slowapi),
> **health/readiness endpoints**, consistent error handling, and per-tenant local-disk
> storage. Tenancy resolves from `X-API-Key` → `X-Tenant-ID` → `default`.
>
> **Planned (🔭):** S3/MinIO object storage (Layer 8), the `/v1/` API prefix, webhooks,
> Celery priority lanes, and VLM/GPU inference. The current API mounts routes at
> `/receipts`, `/analytics`, `/vendors`, and `/admin` (no `/v1/` yet).

---

## System Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                         CLIENT LAYER                                │
│         Browser / Mobile App / Developer API Client                 │
└─────────────────────┬──────────────────────────────────────────────┘
                       │ HTTPS
                       ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       API GATEWAY (FastAPI)                         │
│   Auth · Validation · File Ingest · Job Dispatch · Status Polling   │
└──────────┬──────────────────────────────────────┬───────────────────┘
           │ enqueue job                           │ read/write
           ▼                                       ▼
┌──────────────────────┐               ┌───────────────────────────────┐
│   MESSAGE BROKER     │               │         DATA LAYER            │
│   Redis + Celery     │               │  MongoDB · MinIO/S3 · Redis   │
└──────────┬───────────┘               └───────────────────────────────┘
           │ dequeue
           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    PROCESSING WORKER (Celery)                       │
│                                                                     │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────────┐  │
│  │ Pre-process  │→ │   EasyOCR    │→ │   Post-process & Parse    │  │
│  │  (OpenCV)    │  │  Extraction  │  │  (Pydantic · receipt_     │  │
│  │              │  │              │  │   parsing · categorizer)  │  │
│  └──────────────┘  └──────────────┘  └───────────────────────────┘  │
│                                                ↓                    │
│                                   ┌────────────────────────┐        │
│                                   │  line_items writer     │        │
│                                   │  vendor normaliser     │        │
│                                   └────────────────────────┘        │
└─────────────────────────────────────────────────────────────────────┘
           │ writes results
           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     ANALYTICS & DASHBOARD                           │
│      React · Recharts · MongoDB Aggregation Pipelines               │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Layer 1 — API Gateway (FastAPI)

The API gateway is intentionally thin. Its only responsibilities are:
authenticate, validate, store the raw file, enqueue the job, and return
`job_id`. It never runs OCR or touches image data.

### Responsibilities

| Responsibility | Implementation |
|---|---|
| Authentication | API key lookup → `tenant_id` resolution (SHA-256 hash in `tenants` collection) |
| File validation | MIME type check (JPEG · PNG · WEBP · TIFF · PDF), max size enforcement |
| File storage | Write to MinIO/S3 under `raw/{tenant_id}/{job_id}/filename` |
| Job creation | Insert job document into MongoDB (`status: "queued"`) |
| Queue dispatch | Push `job_id` onto Celery Redis queue |
| Response | Return `{ job_id, status: "queued" }` immediately — no waiting |
| Status polling | `GET /v1/jobs/{job_id}` reads job document and returns current status |
| Webhook delivery | On job completion, `POST` signed payload to tenant-configured URL |

### API surface

> Target surface. Today routes are mounted **without** the `/v1/` prefix
> (`/receipts/...`, `/analytics/...`), uploads accept images only (no PDF), and the
> `vendors`/`categories`/`anomalies`/`review-queue`/`failures` analytics endpoints
> plus `/health*` are not yet implemented. Priority 1 adds the line-item analytics
> endpoints; Priority 9 adds health checks.

```
POST   /v1/receipts/upload          Upload image or PDF → { job_id, status }
GET    /v1/jobs/{job_id}            Poll job status and result
GET    /v1/receipts/                List receipts for tenant (paginated)
GET    /v1/receipts/{id}            Get single receipt with line items
PATCH  /v1/receipts/{id}            Edit merchant, total, date, category
DELETE /v1/receipts/{id}            Remove receipt and its line items
POST   /v1/receipts/{id}/itemize    Re-run itemized bill extraction on demand
GET    /v1/analytics/vendors        Top vendor spend (last N days)
GET    /v1/analytics/categories     Monthly spend by category
GET    /v1/analytics/anomalies      Line items flagged as statistically unusual
GET    /v1/analytics/review-queue   Fields with confidence < 0.75
GET    /v1/analytics/failures       Receipts with null merchant or total
GET    /health                      Liveness check
GET    /health/ready                Readiness check (MongoDB + Redis ping)
```

### Key design rules

- FastAPI process is **stateless** — no file handles, no model state, no shared memory
- All file I/O goes through MinIO/S3, never local disk
- All database access is async via Motor
- Rate limits enforced per API key via Redis sliding window
- CORS allow-list from environment variable (`ALLOWED_ORIGINS`)

---

## Layer 2 — Message Broker (Redis + Celery)

Celery decouples the API from the processing pipeline. The API enqueues a job
reference (not the file — the file is already in S3) and returns immediately.
The worker picks it up asynchronously.

### Queue structure

```
Redis Queues:
  celery.high      ← enterprise tenants, SLA-bound jobs
  celery.default   ← growth tier, standard jobs
  celery.bulk      ← starter tier, batch processing, best-effort

Redis Keys (other):
  vendor_norm:{tenant_id}:{raw_name}  ← vendor normalisation cache (TTL 24h)
  ratelimit:{api_key}                 ← sliding window counter (TTL 60s)
  job_result:{job_id}                 ← short-lived result cache (TTL 1h)
```

### Job document shape (MongoDB `jobs` collection)

```json
{
  "_id": "ObjectId",
  "job_id": "uuid-string",
  "tenant_id": "acme-corp",
  "status": "queued | processing | complete | failed",
  "s3_raw_key": "raw/acme-corp/job_xyz/receipt.jpg",
  "s3_result_key": "results/acme-corp/job_xyz/output.json",
  "model_used": "easyocr",
  "pages": 1,
  "processing_ms": 1840,
  "confidence": 0.88,
  "error": null,
  "created_at": "ISODate",
  "completed_at": "ISODate"
}
```

---

## Layer 3 — Processing Worker (Celery)

The worker is where all computation happens. It is the only component that
reads from S3, runs OpenCV, runs EasyOCR, and writes results back to MongoDB.
The worker runs in a separate Docker container from the API.

### Full worker pipeline

```
1. Dequeue job_id from Redis
        │
        ▼
2. Download raw file from S3
        │
        ▼
3. FILE TYPE ROUTER
   ├── PDF  → pdf2image (poppler) → PIL Image (first page + page count)
   └── Image → PIL Image directly
        │
        ▼
4. PRE-PROCESSING (OpenCV)
   ├── Deskew      (Hough line transform → rotation correction)
   ├── Denoise     (fastNlMeansDenoisingColored)
   └── Contrast    (CLAHE in LAB color space)
        │
        ▼
5. OCR EXTRACTION (EasyOCR)
   └── Returns: list of (text, bounding_box, confidence) tuples
        │
        ▼
6. FIELD EXTRACTION HEURISTICS (ocr_engine.py)
   ├── Merchant name  (first meaningful text block, top of receipt)
   ├── Date           (regex: MM/DD/YYYY, DD-MM-YYYY, "Jan 15 2026", etc.)
   ├── Total amount   (align "TOTAL" label with rightmost price on same row)
   └── Currency       (symbol detection: $, €, £, ¥ → ISO 4217)
        │
        ▼
7. ITEMIZED BILL EXTRACTION (receipt_parsing.py)
   ├── Line detection  (group OCR tokens into rows by Y-coordinate proximity)
   ├── Row classifier  (item row vs subtotal vs tax vs total vs header)
   ├── Price alignment (rightmost numeric token per row = price)
   ├── Qty extraction  (numeric token before description = quantity)
   └── Output: [{ description, qty, unit_price, amount }, ...]
        │
        ▼
8. CATEGORIZATION (keyword classifier)
   └── Map merchant_name → category using keyword lookup table
        │
        ▼
9. POST-PROCESSING (Pydantic v2 validation)
   ├── Type coercion   ("$47.83" → 47.83, "Jan 15" → date object)
   ├── Confidence flag  (overall confidence from EasyOCR scores)
   ├── Null detection   (merchant/total/date null → needs_review = True)
   └── Schema validation (ExtractionResult model)
        │
        ▼
10. PERSISTENCE
    ├── Write receipt document to MongoDB (receipts collection)
    ├── Write line item documents to MongoDB (line_items collection)
    ├── Resolve vendor via RapidFuzz normaliser → write to vendors collection
    ├── Write result JSON to S3 (results/{tenant_id}/{job_id}/output.json)
    └── Update job status → "complete" (or "failed" on exception)
```

---

## Layer 4 — Pre-processing Pipeline (OpenCV) 🔭 (planned)

Pre-processing runs between file load and EasyOCR. It is CPU-only, adds
200–400ms, and requires no changes to the EasyOCR call or API contract.
Its purpose is to maximise OCR accuracy on real-world receipt photos that
are skewed, low-contrast, or noisy.

### Pipeline stages

```
PIL Image (RGB)
      │
      ▼
┌─────────────────────────────────────────────────────┐
│  Stage 1: DESKEW                                    │
│  Convert to grayscale → Canny edge detection →      │
│  Hough line transform → detect median line angle →  │
│  Rotate image by inverse angle                      │
│  Threshold: skip rotation if angle < 0.5°           │
└─────────────────────┬───────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────┐
│  Stage 2: DENOISE                                   │
│  cv2.fastNlMeansDenoisingColored                    │
│  h=10, hColor=10, templateWindowSize=7,             │
│  searchWindowSize=21                                │
│  Preserves text edges while removing background     │
│  noise from photo grain and scanning artifacts      │
└─────────────────────┬───────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────┐
│  Stage 3: CONTRAST NORMALISATION                    │
│  Convert RGB → LAB color space →                   │
│  Apply CLAHE to L channel only                      │
│  (clipLimit=2.0, tileGridSize=8×8) →               │
│  Convert back to RGB                               │
│  Improves legibility on faded thermal receipts      │
└─────────────────────┬───────────────────────────────┘
                      │
                      ▼
              PIL Image (cleaned)
              → passed to EasyOCR
```

### Alternative pre-processing libraries

OpenCV is the recommended choice. Alternatives if OpenCV is too heavy:

| Library | Tradeoff vs OpenCV |
|---|---|
| **Pillow (PIL)** | Built-in, zero new dependency. Has `ImageFilter.SHARPEN`, `ImageEnhance.Contrast`, basic rotation. No Hough transform — deskew requires manual implementation or skip. Good enough for clean receipts. |
| **scikit-image** | Pure Python, easier API than OpenCV. Has `skimage.transform.rotate`, `skimage.restoration.denoise_nl_means`. Slower than OpenCV on large images. Good choice if OpenCV install is problematic in Docker. |
| **Wand (ImageMagick)** | Excellent for PDF rasterisation and rotation. Requires ImageMagick system dependency. More complex Docker setup than OpenCV. |
| **No pre-processing** | Acceptable only for clean, well-lit, straight-on photos. Will degrade EasyOCR accuracy on real-world inputs. Not recommended for production. |

### Pillow-only fallback (if OpenCV excluded)

```python
from PIL import Image, ImageFilter, ImageEnhance, ImageOps

def preprocess_pillow_only(img: Image.Image) -> Image.Image:
    """
    Lightweight pre-processing using only Pillow.
    No deskew (Hough transform not available).
    Suitable for clean receipt photos; less effective on skewed scans.
    """
    # Convert to RGB
    img = img.convert("RGB")

    # Sharpen to improve text edges
    img = img.filter(ImageFilter.SHARPEN)

    # Contrast enhancement
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(1.4)

    # Brightness normalisation
    enhancer = ImageEnhance.Brightness(img)
    img = enhancer.enhance(1.1)

    return img
```

---

## Layer 5 — Itemized Bill Extraction (receipt_parsing.py)

Itemized extraction is the core differentiator over basic OCR. It transforms
a flat list of OCR tokens into a structured list of line items: description,
quantity, unit price, and amount. No ML model is required — this is entirely
rule-based.

> **Current vs target:** the implemented `find_line_items` in `receipt_parsing.py`
> is a simpler, text-line-based heuristic — it pairs a description line with the
> price line that immediately follows and emits `{description, amount, qty}` (qty
> defaults to 1; no `unit_price` or per-item `confidence` yet). The bounding-box /
> X-Y column-role algorithm described below is the target. It was deliberately kept
> text-based so the API process can itemize without importing torch/EasyOCR.

### How it works

EasyOCR returns each text token with a bounding box: `[[x1,y1],[x2,y1],[x2,y2],[x1,y2]]`
and a confidence score. Receipt parsing uses these coordinates to reconstruct
the 2D structure of the receipt.

```
EasyOCR output (flat list):
  [("Organic Milk",    [[10,100],[180,100],[180,118],[10,118]],  0.94),
   ("1",               [[190,100],[210,100],[210,118],[190,118]], 0.91),
   ("4.99",            [[320,100],[380,100],[380,118],[320,118]], 0.97),
   ("Eggs Large 12pk", [[10,122],[180,122],[180,140],[10,140]],  0.89),
   ("2",               [[190,122],[210,122],[210,140],[190,122]], 0.93),
   ("6.49",            [[320,122],[380,122],[380,140],[320,122]], 0.96),
   ("SUBTOTAL",        [[10,200],[120,200],[120,218],[10,218]],  0.99),
   ("11.48",           [[320,200],[380,200],[380,218],[320,200]], 0.98)]

Step 1 — Row grouping (Y-coordinate clustering):
  Group tokens whose Y-centroid falls within ±8px of each other → one row

Step 2 — Row classification:
  ├── "SUBTOTAL", "TAX", "GST", "HST", "TOTAL", "CHANGE" → skip (summary rows)
  ├── Row has no numeric token → skip (header or decorative row)
  └── Row has description + at least one numeric → candidate line item

Step 3 — Column role assignment (X-coordinate heuristics):
  ├── Leftmost non-numeric tokens → description
  ├── Rightmost numeric token → amount (price)
  ├── Second-rightmost numeric (if present) → unit_price
  └── Small integer (1–99) left of description → quantity

Step 4 — Amount validation:
  └── amount > 0 and amount < total_amount → keep; else discard

Step 5 — Sort by amount descending:
  └── Most expensive items first in output
```

### Output schema

```json
{
  "items": [
    {
      "description": "Eggs Large 12pk",
      "qty": 2,
      "unit_price": 3.25,
      "amount": 6.49,
      "confidence": 0.93
    },
    {
      "description": "Organic Milk",
      "qty": 1,
      "unit_price": null,
      "amount": 4.99,
      "confidence": 0.94
    }
  ]
}
```

### Edge cases handled

| Scenario | Handling |
|---|---|
| Qty × unit price printed as "2 × $3.25" | Regex split on `×`, `x`, `@` separators |
| Price with comma decimal ("4,99") | Normalise comma → period before float parse |
| Description split across two lines | Merge rows within 4px Y-gap with no price token |
| Discount rows ("SAVE $1.00") | Negative amounts preserved as negative `amount` |
| Tax rows ("TAX 8.5%") | Classified as summary row, excluded from line items |
| Barcodes / SKU numbers in description | Strip numeric-only tokens from description text |

---

## Layer 6 — Categorization Engine

Maps extracted `merchant_name` and individual line item descriptions to
a spending category. Runs entirely on CPU, no model required.

### Two-level categorization

```
Level 1 — Merchant-level (fast path):
  Match merchant_name against keyword dictionary
  e.g. "WALMART" → "Groceries", "SHELL" → "Transport", "NETFLIX" → "Entertainment"

Level 2 — Item-level (slow path, only if merchant is ambiguous):
  Match item description against item keyword dictionary
  e.g. "Organic Milk" → "Groceries", "Uber" → "Transport", "Advil" → "Health"
```

### Category taxonomy

```
Groceries       → WALMART, KROGER, SAFEWAY, WHOLE FOODS, TRADER JOE'S
                  item keywords: milk, eggs, bread, produce, organic, fresh
Dining          → MCDONALD'S, STARBUCKS, SUBWAY, CHIPOTLE, RESTAURANT, CAFE
                  item keywords: burger, latte, sandwich, coffee, meal
Transport       → SHELL, BP, EXXON, UBER, LYFT, PARKING, TRANSIT
                  item keywords: fuel, gas, petrol, fare, ride
Shopping        → AMAZON, TARGET, BEST BUY, HOME DEPOT, COSTCO
                  item keywords: electronics, clothing, appliance, tool
Utilities       → AT&T, VERIZON, COMCAST, ELECTRIC, WATER, INTERNET
                  item keywords: bill, service charge, usage, monthly plan
Entertainment   → NETFLIX, SPOTIFY, AMC, CINEMA, STEAM, APPLE
                  item keywords: subscription, ticket, download, streaming
Health          → WALGREENS, CVS, PHARMACY, HOSPITAL, CLINIC
                  item keywords: prescription, vitamin, supplement, medical
Uncategorized   → fallback if no match at either level
```

### Extensibility

The keyword dictionary is stored as a Python dict in `ocr_engine.py`. For the
B2B product, this should migrate to a MongoDB `categories` collection so tenants
can add custom categories and mappings without a code deployment.

---

## Layer 7 — Data Layer (MongoDB)

### Collections

**`jobs`** — one document per upload request

```json
{
  "_id": "ObjectId",
  "tenant_id": "acme-corp",
  "status": "queued | processing | complete | failed",
  "s3_raw_key": "raw/acme-corp/job_xyz/receipt.jpg",
  "s3_result_key": "results/acme-corp/job_xyz/output.json",
  "model_used": "easyocr",
  "pages": 1,
  "processing_ms": 1840,
  "confidence": 0.88,
  "error": null,
  "created_at": "ISODate",
  "completed_at": "ISODate"
}
```

**`receipts`** — one document per successfully processed receipt

```json
{
  "_id": "ObjectId",
  "tenant_id": "acme-corp",
  "job_id": "ObjectId",
  "merchant_name": "Walmart Supercenter",
  "date": "ISODate",
  "total_amount": 47.83,
  "currency": "USD",
  "category": "Groceries",
  "raw_text": "...",
  "confidence": 0.88,
  "needs_review": false,
  "created_at": "ISODate"
}
```

**`line_items`** — one document per extracted line item (powers analytics)

```json
{
  "_id": "ObjectId",
  "tenant_id": "acme-corp",
  "job_id": "ObjectId",
  "receipt_id": "ObjectId",
  "vendor_raw": "Walmart Supercenter",
  "vendor_id": "ObjectId",
  "description": "Organic Whole Milk",
  "quantity": 1.0,
  "unit_price": null,
  "amount": 4.99,
  "currency": "USD",
  "category": "Groceries",
  "period": "ISODate",
  "confidence": 0.94,
  "needs_review": false,
  "created_at": "ISODate"
}
```

**`vendors`** — canonical vendor records for normalisation

```json
{
  "_id": "ObjectId",
  "tenant_id": "acme-corp",
  "canonical_name": "Walmart",
  "aliases": ["WALMART", "WALMART SUPERCENTER", "WAL-MART", "WALMART #4821"],
  "category_default": "Groceries",
  "needs_review": false,
  "created_at": "ISODate"
}
```

**`tenants`** — one document per API key / registered tenant

```json
{
  "_id": "ObjectId",
  "tenant_id": "acme-corp",
  "name": "Acme Corporation",
  "email": "admin@acme.com",
  "api_key_hash": "sha256-hex-string",
  "active": true,
  "plan": "starter | growth | enterprise",
  "created_at": "ISODate",
  "last_seen_at": "ISODate"
}
```

### Index strategy

```javascript
// jobs — status polling and tenant history
db.jobs.createIndex({ tenant_id: 1, created_at: -1 })
db.jobs.createIndex({ tenant_id: 1, status: 1 })

// receipts — list view and review queue
db.receipts.createIndex({ tenant_id: 1, created_at: -1 })
db.receipts.createIndex({ tenant_id: 1, needs_review: 1 })

// line_items — all analytics aggregations
// tenant_id MUST be the leading key on every index
db.line_items.createIndex({ tenant_id: 1, period: -1 })
db.line_items.createIndex({ tenant_id: 1, vendor_id: 1, period: -1 })
db.line_items.createIndex({ tenant_id: 1, category: 1, period: -1 })
db.line_items.createIndex({ tenant_id: 1, needs_review: 1 })
db.line_items.createIndex({ tenant_id: 1, receipt_id: 1 })

// vendors — normalisation lookup
db.vendors.createIndex({ tenant_id: 1, aliases: 1 })
db.vendors.createIndex({ tenant_id: 1, needs_review: 1 })

// tenants — auth lookup
db.tenants.createIndex({ api_key_hash: 1 }, { unique: true })
db.tenants.createIndex({ tenant_id: 1 }, { unique: true })
```

### Analytics aggregation patterns

**Top vendor spend (last 90 days):**

```javascript
db.line_items.aggregate([
  { $match: { tenant_id: "acme-corp",
              period: { $gte: ISODate("2026-02-28") } }},
  { $group: { _id: "$vendor_raw",
              total_spend: { $sum: "$amount" },
              transaction_count: { $sum: 1 },
              avg_amount: { $avg: "$amount" } }},
  { $sort: { total_spend: -1 } },
  { $limit: 20 }
])
```

**Monthly spend by category:**

```javascript
db.line_items.aggregate([
  { $match: { tenant_id: "acme-corp" } },
  { $group: {
      _id: {
        category: "$category",
        month: { $dateToString: { format: "%Y-%m", date: "$period" } }
      },
      total: { $sum: "$amount" },
      count: { $sum: 1 }
  }},
  { $sort: { "_id.month": -1, total: -1 } }
])
```

**Anomaly detection (2σ above vendor historical average):**

```javascript
db.line_items.aggregate([
  { $match: { tenant_id: "acme-corp" } },
  { $group: { _id: "$vendor_raw",
              avg: { $avg: "$amount" },
              std: { $stdDevPop: "$amount" },
              items: { $push: { id: "$_id", amount: "$amount",
                                description: "$description", period: "$period" }} }},
  { $unwind: "$items" },
  { $addFields: { z_score: {
      $cond: [ { $gt: ["$std", 0] },
               { $divide: [{ $abs: { $subtract: ["$items.amount", "$avg"] }}, "$std"] },
               0 ]
  }}},
  { $match: { z_score: { $gt: 2.0 } } },
  { $sort: { z_score: -1 } },
  { $limit: 20 }
])
```

---

## Layer 8 — Object Storage (MinIO / S3) 🔭 (planned)

> Not implemented. The MVP persists uploads to per-tenant local disk via
> `storage_paths.py` (`UPLOAD_ROOT/{tenant_id}/{job_id}/...`). The job document
> stores `raw_storage_path` (a local path), not `s3_raw_key`.

### Key structure

```
Bucket: extracta-receipts
│
├── raw/
│   └── {tenant_id}/
│       └── {job_id}/
│           └── receipt.jpg          ← original upload, written by API
│
└── results/
    └── {tenant_id}/
        └── {job_id}/
            └── output.json          ← structured extraction result, written by worker
```

### Local development: MinIO (Docker)

MinIO is S3-compatible and runs as a Docker container. No AWS credentials required.
Switch from MinIO to production S3 by changing two environment variables:

```bash
# MinIO (local dev / staging)
S3_ENDPOINT_URL=http://minio:9000
S3_ACCESS_KEY_ID=minioadmin
S3_SECRET_ACCESS_KEY=minioadmin

# AWS S3 (production) — remove S3_ENDPOINT_URL entirely
S3_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE
S3_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
S3_BUCKET=extracta-prod-receipts
S3_REGION=us-east-1
```

### File lifecycle

```
Upload arrives at API
    → validate MIME type and size
    → write to S3: raw/{tenant_id}/{job_id}/filename
    → set s3_raw_key on job document
    → enqueue job_id (not the file bytes)

Worker picks up job
    → download from S3 using s3_raw_key
    → process (preprocess → OCR → parse)
    → write output.json to S3: results/{tenant_id}/{job_id}/output.json
    → set s3_result_key on job document

Client retrieves result
    → GET /v1/jobs/{job_id} returns presigned S3 URL (15-min expiry)
    → or GET /v1/receipts/{id} returns structured data from MongoDB

S3 lifecycle rules (set in bucket policy)
    → raw/: delete after 30 days
    → results/: delete after 90 days
```

---

## Layer 9 — Vendor Normalisation (RapidFuzz) 🔭 (planned)

### Problem

EasyOCR produces inconsistent merchant name strings from the same store:
"WALMART SUPERCENTER", "Walmart #4821", "WAL-MART", "walmart". Without
normalisation, every analytics aggregation groups these as four separate
vendors — spend totals are wrong and charts are meaningless.

### Solution

RapidFuzz fuzzy string matching with Redis caching. Runs in the worker
after OCR extraction, before writing to `line_items`.

```
Input: raw merchant string from EasyOCR
    │
    ▼
Redis cache check: vendor_norm:{tenant_id}:{raw_name}
    ├── HIT  → return cached vendor_id immediately
    └── MISS → proceed to fuzzy match
                    │
                    ▼
            Load all vendor aliases for tenant from MongoDB
                    │
                    ▼
            RapidFuzz token_sort_ratio match
                    ├── score >= 85 → use existing vendor_id
                    │                 cache result in Redis (TTL 24h)
                    └── score < 85  → create new vendor document
                                      flag needs_review = True
                                      cache new vendor_id in Redis
```

### Why `token_sort_ratio`

`token_sort_ratio` sorts tokens alphabetically before comparing, so
"WALMART SUPERCENTER" and "SUPERCENTER WALMART" score 100. It handles
word-order variations common in OCR output without requiring exact matches.

---

## Layer 10 — Authentication & Multi-tenancy 🔭 (planned auth)

> The API-key flow below is the target. Today there is **no API-key auth**: the
> `X-Tenant-ID` request header is the only tenant signal (validated for format by
> `storage_paths.validate_tenant_id`). Treat the header as a dev convenience until
> Priority 3 lands.

### API key flow

```
Client sends:  X-API-Key: ext_a3f...hex...

FastAPI dependency (get_tenant_id):
    → SHA-256 hash the key
    → db["tenants"].find_one({ api_key_hash: hash, active: true })
    → on match: return tenant["tenant_id"], update last_seen_at
    → on miss:  raise HTTP 401

All downstream queries use tenant_id as a filter.
tenant_id is NEVER taken from a client-supplied header in production.
```

### Tenant isolation boundaries

| Layer | Isolation mechanism |
|---|---|
| MongoDB | `tenant_id` filter on every query; compound indexes with `tenant_id` as leading key |
| S3 / MinIO | Per-tenant key prefix (`raw/{tenant_id}/`) |
| Redis | Per-tenant key prefix (`vendor_norm:{tenant_id}:`, `ratelimit:{api_key}:`) |
| Celery | Per-plan priority queue (enterprise → high, growth → default, starter → bulk) |
| Analytics | All aggregation pipelines include `$match: { tenant_id: ... }` as first stage |

### Dev convenience

During local development, `X-Tenant-ID` header is accepted as a fallback
when no `X-API-Key` is present. This preserves the existing test behaviour
and keeps `mongomock`-backed tests working without key generation. Remove
this fallback before exposing the API publicly.

---

## Infrastructure (Docker Compose)

### Services

```yaml
services:

  api:
    build: ./backend
    command: uvicorn main:app --host 0.0.0.0 --port 8000 --reload
    ports: ["8000:8000"]
    depends_on: [mongo, redis, minio]
    environment:
      MONGODB_URL: mongodb://mongo:27017
      REDIS_URL: redis://redis:6379/0
      S3_ENDPOINT_URL: http://minio:9000
      S3_ACCESS_KEY_ID: minioadmin
      S3_SECRET_ACCESS_KEY: minioadmin
      S3_BUCKET: extracta-receipts
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health/ready"]
      interval: 30s

  worker:
    build: ./backend
    command: celery -A celery_app worker --loglevel=info -Q high,default,bulk
    depends_on: [mongo, redis, minio]
    environment:
      MONGODB_URL: mongodb://mongo:27017
      REDIS_URL: redis://redis:6379/0
      S3_ENDPOINT_URL: http://minio:9000
      S3_ACCESS_KEY_ID: minioadmin
      S3_SECRET_ACCESS_KEY: minioadmin
      S3_BUCKET: extracta-receipts

  frontend:
    build: ./frontend
    ports: ["3000:3000"]
    environment:
      VITE_API_URL: http://localhost:8000

  mongo:
    image: mongo:7
    ports: ["27017:27017"]
    volumes: [mongo_data:/data/db]

  mongo-express:
    image: mongo-express:latest
    ports: ["8081:8081"]
    depends_on: [mongo]

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
    volumes: [redis_data:/data]

  minio:
    image: minio/minio:latest
    command: server /data --console-address ":9001"
    ports: ["9000:9000", "9001:9001"]
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin
    volumes: [minio_data:/data]

volumes:
  mongo_data:
  redis_data:
  minio_data:
```

### Port reference

| Port | Service |
|---|---|
| 3000 | React frontend |
| 8000 | FastAPI (Swagger at /docs) |
| 8081 | Mongo-Express admin UI |
| 9000 | MinIO S3 API |
| 9001 | MinIO web console |
| 27017 | MongoDB |
| 6379 | Redis |

---

## Key Design Decisions

### Why EasyOCR over Tesseract

Tesseract is a character recogniser trained on clean, typeset text. EasyOCR
uses a deep learning model (CRAFT + CRNN) that handles: curved text, blurry
images, mixed fonts, non-standard layouts, and multiple languages out of the
box. For real-world receipt photos (not scanned documents), EasyOCR accuracy
is meaningfully higher. The tradeoff is ~100MB of model weights downloaded once
on first run, and slightly higher CPU time per image.

### Why rule-based itemized extraction over an ML parser

An ML approach to line-item extraction (sequence labelling, named entity
recognition, or a fine-tuned layout model) would require a labelled training
dataset of hundreds of receipts. The rule-based spatial approach using EasyOCR
bounding boxes works without training data and is fully explainable. It handles
the common cases well. The failure modes (highly irregular layouts, multi-column
tables, handwritten amounts) are edge cases for the current user base. Revisit
when you have labelled data from real user corrections.

### Why MongoDB over PostgreSQL at this stage

The extraction layer produces flexible JSON — every receipt has a different
shape depending on the merchant and the user's configured schema. MongoDB's
document model handles schema variability without migrations. The `line_items`
collection is where analytical queries run, and these are scoped to a single
tenant at a time — MongoDB aggregation pipelines handle this well with the right
compound indexes. The `$lookup` + `$unwind` + `$group` complexity that makes
PostgreSQL attractive does not materialise until cross-tenant analytical queries
are needed, which is a future enterprise feature.

### Why MinIO over AWS S3 for local development

MinIO is S3-compatible, runs in Docker, and requires zero cloud credentials.
The application code (`boto3`) is identical for both. Switching from MinIO to
S3 in production is one environment variable change (`S3_ENDPOINT_URL` removed).
This eliminates the AWS credential management problem during development while
keeping the production path clean.

### Why keep the EasyOCR path and not plan for VLM now

VLMs (Qwen2.5-VL, Gemma 3 Vision, DeepSeek-OCR) deliver meaningful accuracy
gains for complex multi-column invoice tables, handwriting, and scanned PDFs.
For consumer receipts (the current target document type), EasyOCR with OpenCV
pre-processing delivers acceptable accuracy without GPU cost, model weight
management, or serverless GPU infrastructure complexity. The architecture is
designed so the EasyOCR call in `tasks.py` can be swapped for a VLM call when
extraction quality becomes a documented user complaint.

---

## Data Flow Summary (end to end)

```
1.  Client uploads receipt.jpg
        POST /v1/receipts/upload
        Header: X-API-Key: ext_abc123

2.  API validates key → resolves tenant_id: "acme-corp"
        Validates MIME type (image/jpeg ✓)
        Writes file to MinIO: raw/acme-corp/job_xyz/receipt.jpg
        Creates job document: { status: "queued", s3_raw_key: "raw/acme-corp/..." }
        Enqueues job_id onto celery.default queue
        Returns: { job_id: "job_xyz", status: "queued" }

3.  Client polls job status
        GET /v1/jobs/job_xyz
        Returns: { status: "processing" }

4.  Celery worker dequeues job_xyz
        Downloads raw/acme-corp/job_xyz/receipt.jpg from MinIO
        Detects MIME: image/jpeg → PIL Image (1 page)
        Runs OpenCV pre-processing (deskew → denoise → contrast)
        Runs EasyOCR → list of (text, bbox, confidence) tuples
        Extracts: merchant="WALMART", date="2026-05-28", total=47.83, currency="USD"
        Parses line items: 12 items sorted by amount descending
        Categorizes: "Groceries"
        Validates with Pydantic v2: all fields typed, confidence=0.91, needs_review=False
        Writes receipt document to MongoDB (receipts collection)
        Resolves vendor via RapidFuzz: "WALMART SUPERCENTER" → vendor_id: "abc"
        Writes 12 line_item documents to MongoDB (line_items collection)
        Writes output.json to MinIO: results/acme-corp/job_xyz/output.json
        Updates job: { status: "complete", processing_ms: 2140, confidence: 0.91 }
        Triggers webhook (if configured) → POST to tenant endpoint

5.  Client polls job status
        GET /v1/jobs/job_xyz
        Returns: { status: "complete", receipt_id: "rec_xyz",
                   confidence: 0.91, processing_ms: 2140 }

6.  Client retrieves receipt
        GET /v1/receipts/rec_xyz
        Returns: full receipt document with line_items array

7.  Dashboard aggregates spend
        GET /v1/analytics/vendors?days=90
        MongoDB aggregation on line_items → top 20 vendors by spend
        GET /v1/analytics/categories
        MongoDB aggregation → monthly spend by category
```

---

*Last updated: May 2026 — OCR Expense Intelligence (EasyOCR + OpenCV, no VLM)*
