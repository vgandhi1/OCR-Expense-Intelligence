# Implementation gap vs `plan.md`

This document compares the **OCR Expense Intelligence** repository to the architecture and roadmap in [`plan.md`](plan.md) (Extracta AI — B2B multimodal document API). Use it for prioritization and handoffs.

---

## Current implementation (snapshot)

| Area | Status |
|------|--------|
| Stack | FastAPI, React (Vite), MongoDB (Motor), Docker Compose |
| Async jobs | `POST /receipts/upload` → `{ job_id, status: "queued" }`; `GET /receipts/jobs/{job_id}` for status |
| Queue | Redis + Celery worker; OCR runs in worker (not in the HTTP process) |
| Raw files | Local disk under `UPLOAD_ROOT`, paths namespaced by `tenant_id` / `job_id` (not S3) |
| Data | MongoDB collections: `receipts`, `jobs`; compound indexes on `tenant_id` + `created_at` / `status` |
| Tenancy | Optional `X-Tenant-ID` on API; `tenant_id` on jobs and receipts; analytics filtered by tenant |
| OCR / ML | EasyOCR + heuristics on **images** only; keyword categorization |
| Product surface | Single-app dashboard: upload, receipt list, monthly + merchant charts |

---

## Gap analysis by theme

### 1. Architecture (`plan.md` §1)

| Recommendation | Gap | Severity |
|----------------|-----|----------|
| Versioned public API (`/v1/...`) | Routes are under `/receipts`, `/analytics` | Low |
| Webhook on job completion | Not implemented; UI uses polling only | Medium |
| S3 per-tenant prefixes (`raw/{tenant_id}/...`) | Local volume only | Medium |
| Redis key namespacing per tenant | Global broker DB; no tenant prefix in keys | Low |
| Rate limiting (API key + Redis sliding window) | No API keys, no limits | High for B2B |
| Celery priority lanes (high / standard / bulk) | Single default queue | Medium |
| Horizontally stateless API | File writes use shared volume (OK for one node; not for multi-replica without shared FS or S3) | Medium |

### 2. ML pipeline (`plan.md` §2)

| Recommendation | Gap | Severity |
|----------------|-----|----------|
| OpenCV preprocess (deskew, binarize, denoise) | Not in pipeline | Medium |
| Layout pre-pass (Docling / LayoutLMv3) | None | High for invoice tables |
| VLM inference (e.g. Qwen2.5-VL) on GPU | EasyOCR CPU path only | High vs plan |
| Serverless GPU (Modal / RunPod) | Not integrated | High vs plan |
| Model routing (Qwen / DeepSeek / Gemma) | Single path | High for unit economics |
| Per-field confidence in API | Not modeled in JSON schema | Medium |
| Bounding boxes exposed per field | OCR produces boxes internally; not returned as stable API contract | Medium |
| Scan quality warning (`POOR_QUALITY`) | Not implemented | Low |

### 3. Data layer (`plan.md` §3)

| Recommendation | Gap | Severity |
|----------------|-----|----------|
| `line_items` collection + dashboard indexes | Analytics aggregate `receipts` totals, not line-level rows | High for “real” spend analytics |
| `vendors` + RapidFuzz normalization + Redis cache | Raw `merchant_name` only | Medium |
| `schemas` / `schema_id` on jobs | Hardcoded receipt shape | High for B2B |
| `s3_raw_key` / `s3_result_key` on jobs | Local `raw_storage_path`; no result blob in object storage | Medium |
| `tenants` collection | Only string `tenant_id`; no tenant CRUD or plans | Medium |
| Multi-currency + `amount_usd` | Single implied currency | Medium |
| PostgreSQL / ClickHouse for heavy analytics | Not started (optional until `$lookup` pain) | Low (later) |

### 4. Product & phases (`plan.md` §5)

| Phase | Exit criterion (abbrev.) | Gap |
|-------|--------------------------|-----|
| **1** | S3, Modal Qwen worker, tenants, E2E &lt; 30s | Partial: jobs + worker + tenant field; **no S3, no Modal/VLM** |
| **2** | Docling, custom schema, router, line_items, vendors, webhooks | **Not started** |
| **3** | Category classifier, aggregations on line items, anomalies, HITL queue | **Minimal**: simple charts + keyword category |
| **4** | Enterprise models, RBAC, Stripe, SOC2, API versioning | **Not started** |

### 5. Security & operations (implied by plan + production readiness)

| Topic | Gap |
|-------|-----|
| Authentication / authorization | No login; `X-Tenant-ID` is not a security boundary |
| Secrets | No AWS/Modal keys in repo; add via env / secret manager when integrating |
| User-facing errors | Improved vs raw stack traces in places; audit all routes for generic errors |

---

## Suggested completion order

Work below is ordered to **reduce rework**: tenancy and job shape first, then storage and ML, then analytics depth, then enterprise.

### Phase A — Harden the platform shell (1–2 weeks)

1. **Public API surface** — Introduce `/v1/extract` (alias or migrate from `/receipts/upload`) and `/v1/jobs/{id}`; document deprecation of old paths.
2. **Real authentication** — Issue API keys or JWT; map principal → `tenant_id` server-side; stop trusting client-supplied tenant as the only isolation (keep header only for dev if needed).
3. **Rate limits** — Redis sliding window per key; return `429` with `Retry-After`.
4. **Webhooks** — On job `complete` / `failed`, `POST` signed payload to tenant-configured URL (retry with backoff); keep polling for the UI.
5. **S3 (or MinIO locally)** — Replace shared-disk raw storage with object keys on the job document (`s3_raw_key`); worker reads from S3. Enables multi-replica API without NFS.

### Phase B — ML path toward `plan.md` (2–4+ weeks)

1. **CPU preprocess** — OpenCV deskew / denoise / optional binarize before EasyOCR or VLM; add timing fields on `jobs`.
2. **Layout pass** — Integrate Docling (or similar) for PDFs and images; output regions JSON for downstream model.
3. **Modal (or RunPod) worker** — Run Qwen2.5-VL (or agreed default) in GPU task; keep Celery as orchestrator or call Modal from task.
4. **Schema-driven extraction** — `schemas` collection + `schema_id` on job; validate output with Pydantic v2; store `model_used` and `pages` on job.
5. **Confidence + bboxes** — Extend response JSON with `_meta.confidence` per field and optional bbox map; surface in API and dashboard overlays.

### Phase C — Data model for analytics (`plan.md` §3)

1. **`line_items` writer** — After each successful job, normalize parser output into line-item documents (`tenant_id`, `job_id`, `vendor_raw`, `amount`, `currency`, `period`, `category`, `confidence`, `bbox`).
2. **Indexes** — Create compound indexes from `plan.md` on `line_items` (leading `tenant_id`).
3. **Aggregation APIs** — Move dashboard queries to `line_items` (vendor spend, category by month); keep `receipts` as optional summary or retire gradually.
4. **Vendors** — `vendors` collection; RapidFuzz match ≥ 0.85; Redis cache `raw_string → vendor_id`; new vendors flagged for review.

### Phase D — Product moat (`plan.md` §4)

1. **Review queue** — UI for fields with confidence &lt; 0.75; persist corrections; feed export for future fine-tuning.
2. **Anomalies** — Statistical flags (e.g. 2σ vs vendor history) on `line_items`.
3. **Schema builder UX** — Visual tool for non-developers (larger product effort).

### Phase E — Enterprise (`plan.md` §5 Phase 4)

1. Model routes (Gemma long-doc, Qwen 72B tier), priority Celery queues, usage metering, Stripe, RBAC on dashboard, API versioning policy, SOC2 preparation.

---

## Quick wins (days)

- Add **OpenCV preprocess** step in worker only (no API contract change).
- Return **partial OCR debug** only in admin/debug mode (never log full receipt text at `INFO`).
- Add **`model_used`: `"easyocr"`** and **`pages`: 1** on job documents for consistency with `plan.md` job shape.
- **PDF upload** — reject with clear `400` until pipeline supports PDFs, or convert first page to image in worker.
- **Health endpoints** — `GET /health` (API), `GET /health/ready` (Mongo + Redis reachable).

---

## How to use this file

- **`plan.md`** remains the north-star product/architecture spec.
- **`../README.md`** describes how to run this repo today.
- **`IMPLEMENTATION_GAP.md`** (this file) should be updated when major milestones land (e.g. “S3 enabled”, “line_items live”) so stakeholders see drift shrink over time.

---

*Last updated to match repository state as of document creation; align dates with your release process when editing.*
