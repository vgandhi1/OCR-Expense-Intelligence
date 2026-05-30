<div align="center">

# 🧾 OCR Expense Intelligence

### Snap a receipt → get structured expenses, itemized bills, and spending analytics.

A **FARM-stack** (FastAPI · React · MongoDB) app that uses AI-powered OCR to read
receipt photos, extract the merchant, date, total, and line items, categorize the
spend, and visualize it on a dashboard.

<br/>

![Python](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.128-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=black)
![MongoDB](https://img.shields.io/badge/MongoDB-Motor-47A248?logo=mongodb&logoColor=white)
![Celery](https://img.shields.io/badge/Celery-Redis-37814A?logo=celery&logoColor=white)
![EasyOCR](https://img.shields.io/badge/OCR-EasyOCR%20%2B%20PyTorch-EE4C2C?logo=pytorch&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![Tests](https://img.shields.io/badge/tests-53%20passing-brightgreen)
![License](https://img.shields.io/badge/License-MIT-blue)

</div>

> 💡 This is the working MVP of a larger B2B document-intelligence vision
> (codename **Extracta AI**). See [`docs/plan.md`](docs/plan.md) for the product
> north star and [`docs/IMPLEMENTATION_GAP.md`](docs/IMPLEMENTATION_GAP.md) for how
> today's code maps to it.

---

## 📑 Table of contents

- [Features](#-features)
- [Tech stack](#️-tech-stack)
- [Architecture](#-architecture)
- [Quick start](#-quick-start-docker)
- [API reference](#-api-reference)
- [Local development & testing](#-local-development--testing)
- [Configuration](#️-configuration)
- [Project structure](#-project-structure)
- [Documentation](#-documentation)
- [Roadmap](#️-roadmap)
- [License](#-license)

---

## ✨ Features

| | Feature | What it does |
|---|---------|--------------|
| 🔍 | **AI OCR extraction** | Pulls merchant, date, and total from JPG/PNG receipts via [EasyOCR](https://github.com/JaidedAI/EasyOCR), aligning the "TOTAL" label with the price on the same line. |
| 🧾 | **Itemized bills** | Extracts line items (product + price) and shows them **most-expensive-first**, so you see which products cost the most. |
| 🏷️ | **Auto-categorization** | Sorts spend into Groceries, Dining, Transport, Shopping, Utilities, Entertainment, Health, or Uncategorized. |
| ✏️ | **Edit & delete** | Fix any field (merchant, total, date, category) inline, and remove duplicate receipts. |
| 📊 | **Analytics dashboard** | Monthly spend and top-merchant charts (Recharts). |
| ⚡ | **Async by design** | Upload returns a `job_id` instantly; a Celery worker runs OCR off the request path while the UI polls. |
| 🏢 | **Multi-tenant** | An optional `X-Tenant-ID` header scopes every job, receipt, and analytics query. |
| 🐳 | **Containerized** | One `docker compose up` brings up the entire stack. |

---

## 🛠️ Tech stack

| Layer | Technology |
|-------|-----------|
| **Backend API** | FastAPI · Uvicorn · Motor (async MongoDB) |
| **Worker** | Celery + Redis broker · PyMongo · EasyOCR · PyTorch · Pillow |
| **Database** | MongoDB (`receipts`, `jobs`) + Mongo-Express admin UI |
| **Frontend** | React · Vite · TailwindCSS · Recharts · Axios |
| **Infra** | Docker & Docker Compose |

---

## 🏗 Architecture

```
                        ┌─────────────┐
  Browser ──HTTP──▶  FastAPI (API) ──enqueue──▶  Redis  ──▶  Celery worker
                        │                                          │
                        │                                    EasyOCR + parse
                        ▼                                          ▼
                     MongoDB  ◀────────────── writes receipt ──────┘
                  (jobs, receipts)
```

The API only **authenticates, validates, stores, and enqueues** — it never runs OCR.
The worker does the heavy lifting and writes results back to MongoDB.

---

## 🚀 Quick start (Docker)

**Prerequisites:** Docker Desktop (or Docker Engine + Compose v2). On Windows, use WSL2.

```bash
docker compose up --build
```

Then open:

| Service | URL |
|---------|-----|
| 🖥️ Frontend dashboard | http://localhost:3000 |
| 📚 API docs (Swagger) | http://localhost:8000/docs |
| 🗄️ MongoDB admin (Mongo-Express) | http://localhost:8081 |

> ⚠️ **Port conflicts?** If `8000` or `3000` are taken, use the included override to
> remap to `18000` / `13000` / `18081` (it also sets `VITE_API_URL` and `ALLOWED_ORIGINS`):
>
> ```bash
> docker compose -f docker-compose.yml -f docker-compose.demo.yml up -d
> ```

**Try it:** drop a receipt image (or one from `test_fixtures/`) onto the dashboard.
Use the 🔽 list icon on a row to generate its itemized bill, ✏️ to edit, or 🗑️ to delete.

---

## 🔌 API reference

Base URL: `http://localhost:8000`. Every endpoint accepts an optional
**`X-Tenant-ID`** header (alphanumeric, `_`, `-`, max 64 chars; defaults to `default`).

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

<details>
<summary><b>📋 Example flow (click to expand)</b></summary>

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

</details>

---

## 🧪 Local development & testing

Use the project virtualenv so the global interpreter (often PEP 668
"externally managed") doesn't get in the way.

```bash
python3 -m venv .venv          # if one doesn't exist
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
```

**Generate sample receipts** — deterministic, OCR-legible fixtures, no need to
source your own images:

```bash
python test_fixtures/generate_fixtures.py
# → test_fixtures/receipt_{walmart,starbucks,shell,blurry}.jpg
```

**Run the test suite** — no Docker, MongoDB, or Redis needed (`mongomock-motor`
provides an in-memory DB and the Celery enqueue is stubbed):

```bash
pytest                                                   # fast unit + API tests
RUN_OCR=1 pytest backend/tests/test_ocr_end_to_end.py    # real EasyOCR (downloads weights once)
```

> 🧠 The end-to-end OCR test writes model weights to `~/.EasyOCR`. If your home
> directory isn't writable:
> `EASYOCR_MODULE_PATH="$(pwd)/.easyocr_models" RUN_OCR=1 pytest ...`

Tests cover tenant/path validation, OCR parsing & categorization, line-item
extraction, the upload→job flow, edit/delete/itemize, tenant isolation, and
analytics aggregation.

**Frontend (outside Docker):**

```bash
cd frontend
npm install
npm run dev        # Vite dev server on :5173
```

---

## ⚙️ Configuration

All config is via environment variables (Docker Compose supplies sensible defaults,
so a `.env` is optional). See [`.env.example`](.env.example).

| Variable | Used by | Default | Purpose |
|----------|---------|---------|---------|
| `MONGODB_URL` | API, worker | `mongodb://mongo:27017` | MongoDB connection |
| `REDIS_URL` | API, worker | `redis://redis:6379/0` | Celery broker + result backend |
| `UPLOAD_ROOT` | API, worker | `/data/uploads` | Raw upload storage (tenant/job namespaced) |
| `ALLOWED_ORIGINS` | API | `http://localhost:3000,http://localhost:5173` | CORS allow-list (comma-separated) |
| `VITE_API_URL` | frontend | `http://localhost:8000` | API base URL baked into the UI |

---

## 📂 Project structure

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
│   ├── plan.md                # Product & architecture vision (Extracta AI)
│   ├── IMPLEMENTATION_GAP.md   # Current code vs. the plan
│   └── PRODUCTION_DEPLOY.md    # Full production deployment guide
├── docker-compose.yml
├── docker-compose.demo.yml # Host-port remap override (avoids 8000/3000 conflicts)
├── requirements.txt        # Runtime deps
├── requirements-dev.txt    # Test/dev deps
└── pytest.ini
```

---

## 📚 Documentation

| Doc | What's inside |
|-----|---------------|
| 📘 [`docs/plan.md`](docs/plan.md) | The product and architecture north star |
| 📙 [`docs/IMPLEMENTATION_GAP.md`](docs/IMPLEMENTATION_GAP.md) | What's built vs. planned, with a suggested completion order |
| 📗 [`docs/PRODUCTION_DEPLOY.md`](docs/PRODUCTION_DEPLOY.md) | End-to-end production deploy (hosting, CI/CD, domain, SSL, monitoring, cost) |

---

## 🗺️ Roadmap

The current MVP covers async OCR, itemized extraction, categorization, receipt
management, and basic analytics. Larger planned work — API keys & rate limiting,
object storage (S3/MinIO), GPU VLM inference, custom schemas, vendor normalization,
webhooks, and a `line_items` analytics collection — is tracked in
[`docs/IMPLEMENTATION_GAP.md`](docs/IMPLEMENTATION_GAP.md).

---

## 📄 License

Released under the **MIT License**.

<div align="center">
<sub>Built with FastAPI, React, MongoDB, and EasyOCR.</sub>
</div>
