# Extracta AI — Project Recommendations

> B2B SaaS Multimodal Document Reasoning API  
> Target: Financial documents (invoices, bills, receipts) → structured JSON → spending analytics dashboard  
> Database: MongoDB (current stack — keep it)

---

## 1. Architecture Recommendations

### Use an Async-First Design from Day One

Do not build a synchronous "upload → wait → get JSON" API. The moment GPU inference is in the critical path, synchronous responses will cause timeouts at scale. The recommended pattern:

- `POST /v1/extract` returns `{ job_id, status: "queued" }` immediately
- Client polls `GET /v1/jobs/{job_id}` or receives a webhook callback on completion
- FastAPI handles the HTTP layer; Celery + Redis handles the work queue

This mirrors the pattern used by AWS Textract, Google Document AI, and every production document AI API — follow it from the start, not after your first scaling incident.

### Keep the API Gateway Thin

FastAPI's job is: authenticate, validate, enqueue, respond. It should never touch a GPU or run a model. Any business logic that creeps into the gateway will become a bottleneck. The gateway should be horizontally scalable with zero state.

### Serverless GPU Over Reserved Instances

This is the most important cost decision in the architecture. A reserved A100 on AWS costs ~$3–4/hour whether it processes documents or sits idle. Modal and RunPod bill per second of actual GPU use and scale to zero. For a SaaS with bursty, unpredictable traffic, serverless GPU is the correct default until you have enough sustained load to justify reserved capacity (rough threshold: >$5,000/month GPU spend).

### Multi-Tenancy Isolation

Enforce tenant isolation at every layer from the start — it is much harder to retrofit:

- S3: per-tenant key prefix (`/raw/{tenant_id}/`)
- MongoDB: `tenant_id` field on every document, compound indexes with `tenant_id` as the leading key
- Redis: per-tenant key namespacing
- Rate limiting: per API key in Redis sliding window
- Celery queues: separate high/standard/bulk queues; enterprise tenants get priority lanes

---

## 2. ML Pipeline Recommendations

### Do Not Skip the Pre-processing Step

VLMs are powerful but not magic. Feeding a skewed, low-contrast scan directly into Qwen2.5-VL will produce hallucinations on table structures. The OpenCV pre-processing step (deskew → binarize → denoise) is cheap and dramatically improves extraction accuracy. Budget 200–400ms for this step on CPU — it pays for itself in reduced re-processing.

### Use a Layout Model as a Pre-pass

IBM Docling or LayoutLMv3 between pre-processing and VLM inference is worth the added complexity. It gives the VLM structured context about where tables, headers, and line items are located, rather than forcing the VLM to reason about layout from raw pixels. This is the difference between 85% and 95% field-level accuracy on complex invoices.

### Model Routing is Your Margin Lever

The three-model routing strategy is not optional — it is core to the unit economics:

| Condition | Model | Approx. cost/page |
|---|---|---|
| Standard invoice, <20 pages | Qwen 2.5-VL 7B | ~$0.008 |
| Bulk text-heavy, cost-sensitive | DeepSeek-OCR (first pass) | ~$0.002 |
| Long-form contracts, 50+ pages | Gemma 3 Vision | ~$0.015 |

A cascade pattern for bulk processing (DeepSeek first, Qwen re-check on confidence <0.75) reduces cost per page by approximately 60% on high-volume tenants without sacrificing accuracy on difficult documents.

### Confidence Scoring is a Feature, Not an Internal Detail

Surface confidence scores per field in the API response (`"_meta": { "confidence": 0.94 }`). Customers will use this to build their own review queues for low-confidence extractions. It also gives you a natural upsell path — higher-tier plans get the 72B model with higher accuracy guarantees.

### Bounding Box Grounding is a Differentiator

Qwen2.5-VL returns pixel-level bounding boxes for each extracted field. Expose this in the API response. It enables:

- UI overlays showing exactly where each field was found in the source document
- Audit trails for financial compliance use cases
- Human-in-the-loop review workflows where reviewers can see what the model saw

Most competitor APIs do not surface this. It is a meaningful differentiator for enterprise buyers.

---

## 3. Data Layer Recommendations (MongoDB)

### Why MongoDB Works for Your Current Stage

MongoDB's document model is a natural fit for the extraction layer: every invoice produces a unique JSON shape, customer schemas vary widely, and you are mostly storing and retrieving whole job documents rather than aggregating across fields. At this stage, staying on MongoDB is the right call — the switching cost exceeds the benefit.

### Recommended Collection Structure

Design three primary collections. Keep the structure flat where possible — deeply nested documents make aggregation pipelines harder to maintain.

**`jobs` collection** — one document per extraction request:

```json
{
  "_id": "ObjectId",
  "tenant_id": "uuid-string",
  "status": "queued | processing | complete | failed",
  "s3_raw_key": "raw/tenant_abc/job_xyz/invoice.pdf",
  "s3_result_key": "results/tenant_abc/job_xyz/output.json",
  "schema_id": "ObjectId",
  "model_used": "qwen2.5-vl-7b",
  "pages": 3,
  "processing_ms": 2100,
  "confidence": 0.94,
  "created_at": "ISODate",
  "completed_at": "ISODate"
}
```

**`line_items` collection** — one document per extracted line item, written by the post-processor after each job completes. This is the collection that powers the analytics dashboard:

```json
{
  "_id": "ObjectId",
  "tenant_id": "uuid-string",
  "job_id": "ObjectId",
  "vendor_id": "ObjectId",
  "vendor_raw": "Amazon Web Services",
  "description": "EC2 On-Demand Instances",
  "amount": 1245.60,
  "currency": "USD",
  "category": "Cloud Infrastructure",
  "period": "ISODate",
  "confidence": 0.97,
  "bbox": { "x": 142, "y": 380, "w": 310, "h": 18 },
  "created_at": "ISODate"
}
```

**`vendors` collection** — canonical vendor records for normalisation:

```json
{
  "_id": "ObjectId",
  "tenant_id": "uuid-string",
  "canonical_name": "Amazon Web Services",
  "aliases": ["AWS", "Amazon AWS", "Amazon Web Svc"],
  "category_default": "Cloud Infrastructure",
  "created_at": "ISODate"
}
```

### Index Strategy — Critical for Dashboard Performance

MongoDB without the right indexes will become unusably slow once the `line_items` collection grows. Create these compound indexes immediately, always with `tenant_id` as the leading key:

```javascript
// line_items — powers all dashboard aggregations
db.line_items.createIndex({ tenant_id: 1, period: -1 })
db.line_items.createIndex({ tenant_id: 1, vendor_id: 1, period: -1 })
db.line_items.createIndex({ tenant_id: 1, category: 1, period: -1 })

// jobs — powers status polling and history views
db.jobs.createIndex({ tenant_id: 1, created_at: -1 })
db.jobs.createIndex({ tenant_id: 1, status: 1 })

// vendors — powers normalisation lookup
db.vendors.createIndex({ tenant_id: 1, aliases: 1 })
```

### Aggregation Pipeline Patterns for the Dashboard

MongoDB's `$group` and `$facet` stages handle the core dashboard queries. The key is keeping these as server-side aggregations rather than pulling data into Python and computing in memory.

**Spend by vendor, last 90 days:**

```javascript
db.line_items.aggregate([
  { $match: {
      tenant_id: "uuid-string",
      period: { $gte: ISODate("2026-01-14") }
  }},
  { $group: {
      _id: "$vendor_id",
      total_spend: { $sum: "$amount" },
      transaction_count: { $sum: 1 }
  }},
  { $sort: { total_spend: -1 } },
  { $limit: 20 }
])
```

**Monthly spend by category:**

```javascript
db.line_items.aggregate([
  { $match: { tenant_id: "uuid-string" } },
  { $group: {
      _id: {
        category: "$category",
        month: { $dateToString: { format: "%Y-%m", date: "$period" } }
      },
      total: { $sum: "$amount" }
  }},
  { $sort: { "_id.month": -1 } }
])
```

### Vendor Normalisation — Solve This Early

Raw vendor strings from invoices are messy. "Amazon Web Services", "AWS", "Amazon AWS", and "AMZN Web Svcs" are all the same vendor. Without normalisation, vendor-level spend aggregations are useless.

Recommended approach: on each new `vendor_raw` string, check `vendors.aliases` for a fuzzy match using RapidFuzz (Python). On a match above 0.85 similarity, assign the existing `vendor_id`. On no match, create a new vendor document and queue it for human review in the dashboard. Cache resolved mappings in Redis by raw string to avoid re-running fuzzy matching on every document.

### When to Consider Adding PostgreSQL

MongoDB will handle the extraction and basic analytics workload well through Phase 3. The point where it starts working against you is when you need complex multi-collection JOINs with filtering and aggregation in a single query — for example, "show me all line items where the vendor's default category is Cloud Infrastructure, grouped by month, for tenants on the Growth plan." MongoDB's `$lookup` can do this but the pipeline becomes hard to maintain. If you find yourself writing `$lookup` + `$unwind` + `$group` chains regularly, that is the signal to introduce a dedicated analytics store. At that point, consider writing enriched line items to a read-only PostgreSQL instance (or ClickHouse for higher volume) specifically for dashboard queries, while keeping MongoDB as the primary operational store.

---

## 4. Product & Feature Recommendations

### The Spending Analytics Dashboard is the Moat

Raw JSON extraction is a commodity — AWS Textract, Google Document AI, and Azure Form Recognizer all do it. The spending dashboard (which items is this customer overspending on, how does their vendor spend compare month-over-month, which line items are anomalous) is where Extracta AI becomes sticky. Prioritise this layer early, not as a Phase 2 afterthought.

### Build the Schema Builder as a Visual Tool

The difference between a developer tool and a B2B product is the schema builder UI. Allow non-technical users to upload a sample invoice, click on fields they want to extract, and generate the JSON schema automatically. This dramatically widens the addressable buyer — operations teams, finance teams, and AP departments can self-serve without engineering involvement.

### Human-in-the-Loop Review Queue

For fields below a confidence threshold (recommended: <0.75), surface a review queue in the dashboard where a human can confirm or correct the extracted value. Feed confirmed corrections back as fine-tuning data over time. This is also a strong enterprise sales argument — the system improves with use, and customers feel in control of accuracy.

### Pricing Model Recommendation

Align pricing to the unit economics of GPU inference:

| Tier | Model | Volume | Price |
|---|---|---|---|
| Starter | DeepSeek-OCR | Up to 500 pages/month | $49/month |
| Growth | Qwen 2.5-VL 7B | Up to 5,000 pages/month | $299/month |
| Enterprise | Qwen 72B + Gemma 3 | Custom volume, SLA, dedicated queue | Custom |

Overage pricing per page beyond tier limit creates natural expansion revenue without forcing upgrades.

---

## 5. Implementation Sequencing

### Phase 1 — Core Extraction API (Weeks 1–6)

- FastAPI skeleton: auth, file upload, `job_id` return
- S3 integration for raw file storage
- Redis + Celery queue
- Single Modal worker running Qwen 2.5-VL 7B
- Basic OpenCV pre-processing (deskew, binarize)
- JSON output matching a hardcoded invoice schema
- MongoDB: `jobs` and `tenants` collections, compound indexes on `tenant_id`

**Exit criterion:** End-to-end extraction of a real invoice to JSON in under 30 seconds.

### Phase 2 — Schema Flexibility + Accuracy (Weeks 7–10)

- Docling layout parsing integrated into worker pipeline
- User-defined JSON schema support via schema mapper
- Pydantic v2 validation and confidence scoring in post-processor
- Model router (Qwen 7B / DeepSeek-OCR)
- `line_items` collection with dashboard indexes
- Vendor normalisation with RapidFuzz + Redis cache
- Basic webhook delivery

**Exit criterion:** A customer-defined schema produces correctly typed, validated JSON with per-field confidence scores.

### Phase 3 — Analytics Dashboard (Weeks 11–16)

- Line item category classifier (zero-shot LLM or fine-tuned)
- MongoDB aggregation pipelines for vendor spend, category breakdown, period trends
- Next.js dashboard powered by aggregation API endpoints
- Anomaly detection on line item amounts (2σ above vendor historical average)
- Human review queue for low-confidence fields

**Exit criterion:** A user can upload 20 invoices and immediately see a meaningful spend breakdown by vendor and category.

### Phase 4 — Enterprise Readiness (Weeks 17–22)

- Gemma 3 Vision route for long documents
- Qwen 72B route for enterprise accuracy tier
- Priority queue lanes per tenant tier
- RBAC for dashboard (admin, reviewer, read-only roles)
- Usage metering, billing integration (Stripe)
- SOC 2 Type I audit preparation
- API versioning and deprecation policy
- Evaluate ClickHouse or PostgreSQL read replica if aggregation query latency exceeds 2s on large tenants

---

## 6. Key Technical Risks

**MongoDB aggregation performance at scale** — Aggregation pipelines on `line_items` will degrade as the collection grows beyond ~10M documents if indexes are not compound and tenant-prefixed. Profile with `explain("executionStats")` regularly. If `COLLSCAN` appears anywhere in the plan, an index is missing.

**GPU cold start latency** — Modal's cold start is ~4 seconds for a loaded model. For synchronous-feeling UX, maintain at least one warm worker instance during business hours using Modal's `keep_warm` option. Cost: ~$0.30–0.50/hour.

**VLM hallucination on degraded scans** — The pre-processing and layout parsing steps exist specifically to mitigate this, but very low quality scans (<150 DPI, heavy noise) will still produce poor results. Implement a scan quality classifier at the ingest stage and return a `POOR_QUALITY` warning to the client before processing.

**Vendor name normalisation at scale** — Fuzzy matching is accurate but slow at scale. Cache normalisation results by raw string in Redis. Consider building a small embedding-based matcher after 10,000+ unique vendor strings accumulate.

**Schema drift** — Customer schemas will change over time. Version schemas in the `schemas` collection and make the schema mapper stateless so historical jobs can always be re-processed against the original schema version.

**Multi-currency handling** — Financial documents will contain mixed currencies. Implement currency detection (symbol + context) and store all amounts in both original currency and a normalised USD equivalent using a daily exchange rate feed. Store both on the `line_items` document: `"amount": 1245.60, "currency": "USD", "amount_usd": 1245.60`.

---

*Last updated: April 2026*
