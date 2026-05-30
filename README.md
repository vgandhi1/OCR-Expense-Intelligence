# OCR Expense Intelligence

Upload a receipt image, get structured expense data back. This is a **FARM-stack**
(FastAPI · React · MongoDB) application that uses AI-powered OCR to extract the
merchant, date, total, and individual line items from receipt photos, classify
the spend into categories, and visualize it on a dashboard.

It is the working MVP of a larger B2B document-intelligence vision (codename
**Extracta AI**) — see [`docs/plan.md`](docs/plan.md) for the product north star
and [`docs/IMPLEMENTATION_GAP.md`](docs/IMPLEMENTATION_GAP.md) for how the current
code maps to it.

---

## Features

- **AI OCR extraction** — merchant, date, and total are pulled from JPG/PNG
  receipts using [EasyOCR](https://github.com/JaidedAI/EasyOCR), with a geometric
  heuristic that finds the total by aligning the "TOTAL" label with the price on
  the same line.
- **Itemized bills** — line items (product + price) are extracted per receipt and
  shown most-expensive-first, so you can see exactly which products cost the most.
- **Automatic categorization** — keyword rules sort spend into Groceries, Dining,
  Transport, Shopping, Utilities, Entertainment, Health, or Uncategorized.
- **Edit & delete** — correct any field (merchant, total, date, category) inline,
  and delete duplicate receipts.
- **Analytics dashboard** — monthly spend and top-merchant charts (Recharts).
- **Async by design** — upload returns a `job_id` immediately; a Celery worker
  runs OCR off the request path while the UI polls job status.
- **Multi-tenant** — an optional `X-Tenant-ID` header scopes every job, receipt,
  and analytics query.
- **Fully containerized** — one `docker compose up` brings up the whole stack.

---

## Tech stack

| Layer | Technology |
|-------|-----------|
| Backend API | FastAPI, Uvicorn, Motor (async MongoDB) |
| Worker | Celery + Redis broker, PyMongo, EasyOCR, PyTorch, Pillow |
| Database | MongoDB (`receipts`, `jobs` collections) + Mongo-Express admin UI |
| Frontend | React, Vite, TailwindCSS, Recharts, Axios |
| Infra | Docker & Docker Compose |

### Architecture

```
                        ┌─────────────┐
  Browser ──HTTP──▶ FastAPI (API) ──enqueue──▶ Redis ──▶ Celery worker
                        │                                      │
                        │                                  EasyOCR + parse
                        ▼                                      ▼
                     MongoDB  ◀──────────── writes receipt ────┘
                     (jobs, receipts)
```

The API only authenticates, validates, stores, and enqueues — it never runs OCR.
The worker does the heavy lifting and writes results back to MongoDB.

---

## Quick start (Docker)

Prerequisites: Docker Desktop (or Docker Engine + Compose v2). On Windows, use WSL2.

```bash
docker compose up --build
```

Then open:

| Service | URL |
|---------|-----|
| Frontend dashboard | http://localhost:3000 |
| API docs (Swagger) | http://localhost:8000/docs |
| MongoDB admin (Mongo-Express) | http://localhost:8081 |

> **Port conflicts?** If `8000` or `3000` are already in use, start with the
> included override to remap to `18000` / `13000` / `18081`:
>
> ```bash
> docker compose -f docker-compose.yml -f docker-compose.demo.yml up -d
> ```
>
> The override also sets `VITE_API_URL` and `ALLOWED_ORIGINS` so the UI and CORS
> point at the remapped ports.

Try it: drop a receipt image (or one from `test_fixtures/`) onto the dashboard and
watch it appear with extracted details. Click the list icon on a row to generate
its itemized bill, the pencil to edit, or the trash to delete.

---

## API reference

Base URL: `http://localhost:8000`. All endpoints accept an optional
`X-Tenant-ID` header (alphanumeric, `_`, `-`, max 64 chars; defaults to `default`).

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Health check |
| `POST` | `/receipts/upload` | Upload an image (`file` form field) → `{ job_id, status }` |
| `GET` | `/receipts/jobs/{job_id}` | Poll job status (`queued` → `processing` → `complete`/`failed`) |
| `GET` | `/receipts/` | List receipts for the tenant |
| `PATCH` | `/receipts/{id}` | Update `merchant_name`, `total_amount`, `date`, `category` |
| `DELETE` | `/receipts/{id}` | Delete a receipt (`204`) |
| `POST` | `/receipts/{id}/itemize` | Derive line items from the receipt's OCR text |
| `GET` | `/analytics/monthly` | Spend grouped by month |
| `GET` | `/analytics/merchant` | Top 5 merchants by spend |

### Example flow

```bash
# 1. Upload a receipt
curl -X POST http://localhost:8000/receipts/upload \
  -F "file=@test_fixtures/receipt_walmart.jpg" \
  -H "X-Tenant-ID: acme"
# → {"job_id":"...","status":"queued"}

# 2. Poll until complete
curl http://localhost:8000/receipts/jobs/JOB_ID -H "X-Tenant-ID: acme"
# → {"status":"complete","receipt_id":"..."}

# 3. Generate the itemized bill
curl -X POST http://localhost:8000/receipts/RECEIPT_ID/itemize -H "X-Tenant-ID: acme"
# → { ..., "items":[{"description":"Eggs","amount":3.49,"qty":1}, ...] }

# 4. Correct a field
curl -X PATCH http://localhost:8000/receipts/RECEIPT_ID \
  -H "Content-Type: application/json" \
  -d '{"merchant_name":"Walmart","total_amount":47.83}'

# 5. View analytics
curl http://localhost:8000/analytics/monthly  -H "X-Tenant-ID: acme"
curl http://localhost:8000/analytics/merchant -H "X-Tenant-ID: acme"
```

---

## Local development & testing

The backend deps (including a Python `.venv`) install with pip. Use the virtualenv
so the global interpreter (often PEP 668 "externally managed") doesn't get in the way.

```bash
python3 -m venv .venv          # if one doesn't exist
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
```

### Generate sample receipts

The repo ships a generator that renders deterministic, OCR-legible fixtures —
no need to source your own images:

```bash
python test_fixtures/generate_fixtures.py
# → test_fixtures/receipt_{walmart,starbucks,shell,blurry}.jpg
```

### Run the test suite

The suite runs without Docker, MongoDB, or Redis — it uses `mongomock-motor` for
an in-memory database and stubs the Celery enqueue.

```bash
pytest                                                   # fast unit + API tests
RUN_OCR=1 pytest backend/tests/test_ocr_end_to_end.py    # real EasyOCR (downloads weights once)
```

> The end-to-end OCR test writes model weights to `~/.EasyOCR`. If your home
> directory isn't writable, point it elsewhere:
> `EASYOCR_MODULE_PATH="$(pwd)/.easyocr_models" RUN_OCR=1 pytest ...`

Tests cover tenant/path validation, OCR parsing & categorization, line-item
extraction, the upload→job flow, receipt edit/delete/itemize, tenant isolation,
and analytics aggregation.

### Frontend (outside Docker)

```bash
cd frontend
npm install
npm run dev        # Vite dev server on :5173
```

Set `VITE_API_URL` to point the UI at a non-default API URL (defaults to
`http://localhost:8000`).

---

## Configuration

All configuration is via environment variables (Docker Compose supplies sensible
defaults, so a `.env` is optional). See [`.env.example`](.env.example).

| Variable | Used by | Default | Purpose |
|----------|---------|---------|---------|
| `MONGODB_URL` | API, worker | `mongodb://mongo:27017` | MongoDB connection |
| `REDIS_URL` | API, worker | `redis://redis:6379/0` | Celery broker + result backend |
| `UPLOAD_ROOT` | API, worker | `/data/uploads` | Where raw uploads are stored (tenant/job namespaced) |
| `ALLOWED_ORIGINS` | API | `http://localhost:3000,http://localhost:5173` | CORS allow-list (comma-separated) |
| `VITE_API_URL` | frontend | `http://localhost:8000` | API base URL baked into the UI |

---

## Project structure

```
.
├── backend/
│   ├── routes/             # API endpoints (receipts, analytics)
│   ├── tests/              # pytest suite (mongomock-backed, no Docker needed)
│   ├── main.py             # FastAPI app + CORS + lifespan
│   ├── database.py         # MongoDB connection + index setup
│   ├── celery_app.py       # Celery application
│   ├── tasks.py            # Background OCR job
│   ├── ocr_engine.py       # EasyOCR + field parsing & categorization
│   ├── receipt_parsing.py  # Lightweight line-item text parser (no ML deps)
│   ├── storage_paths.py    # Tenant-prefixed, traversal-safe upload paths
│   └── models.py           # Pydantic models
├── frontend/
│   └── src/
│       ├── components/     # Upload, ReceiptsList (edit/delete/itemize), Dashboard
│       └── api/client.js   # Axios client (reads VITE_API_URL)
├── test_fixtures/
│   └── generate_fixtures.py
├── docs/
│   ├── plan.md             # Product & architecture vision (Extracta AI)
│   ├── IMPLEMENTATION_GAP.md  # Current code vs. the plan
│   └── PRODUCTION_DEPLOY.md   # Full production deployment guide
├── docker-compose.yml
├── docker-compose.demo.yml # Host-port remap override (avoids 8000/3000 conflicts)
├── requirements.txt        # Runtime deps
├── requirements-dev.txt    # Test/dev deps
└── pytest.ini
```

---

## Documentation

- **[`docs/plan.md`](docs/plan.md)** — the product and architecture north star.
- **[`docs/IMPLEMENTATION_GAP.md`](docs/IMPLEMENTATION_GAP.md)** — what's built vs. planned, with a suggested completion order.
- **[`docs/PRODUCTION_DEPLOY.md`](docs/PRODUCTION_DEPLOY.md)** — end-to-end production deployment (hosting, CI/CD, domain, SSL, monitoring, cost).

---

## Roadmap (high level)

The current MVP covers async OCR, itemized extraction, categorization, receipt
management, and basic analytics. Larger planned work — API keys & rate limiting,
object storage (S3/MinIO), GPU VLM inference, custom schemas, vendor normalization,
webhooks, and a `line_items` analytics collection — is tracked in
[`docs/IMPLEMENTATION_GAP.md`](docs/IMPLEMENTATION_GAP.md).

---

## License

MIT.
