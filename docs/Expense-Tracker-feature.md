# Expense Tracker — Manual Entry & Budgets

> **Status: ✅ Shipped.** This document is the *as-built* specification. Every
> endpoint, model, and component below exists in the codebase and is covered by
> tests (`backend/tests/test_expenses_api.py`, plus budget-progress cases in
> `test_analytics_api.py`).

Until now the app was purely **reactive**: it waited for an image/PDF, processed
it asynchronously through Celery + EasyOCR, and saved the result. The Expense
Tracker adds the missing **proactive** path — **manual expense entry** and
**per-category budgets** — without duplicating any of the existing analytics,
export, or edit/delete machinery.

The design principle: *manual expenses are just receipts with a different
origin.* They live in the **same `receipts` collection**, stamped
`source = "manual"`, so the monthly/merchant/category charts, CSV/Excel export,
and inline edit/delete all work on them for free.

---

## 1. Data model (`backend/models.py`)

A `source` discriminator was added to the `Receipt` model. OCR documents predate
this field, so it **defaults to `ocr`** and older rows serialize correctly.

```python
class ExpenseSource(str, Enum):
    OCR = "ocr"
    MANUAL = "manual"

class Receipt(ReceiptBase):
    ...
    source: ExpenseSource = ExpenseSource.OCR   # manual entries set "manual"
    notes: Optional[str] = None
```

Two request models validate the new write paths (Pydantic v2 — note `ge=0`
amount guards and a regex-constrained `month`):

```python
MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"   # e.g. "2026-06"

class ManualExpenseCreate(BaseModel):
    merchant_name: str = Field(min_length=1, max_length=200)
    total_amount: float = Field(ge=0)
    date: datetime
    category: str = Field(min_length=1, max_length=60)
    notes: Optional[str] = Field(default=None, max_length=1000)
    currency: Optional[str] = Field(default="USD", max_length=8)

class BudgetUpsert(BaseModel):
    category: str = Field(min_length=1, max_length=60)
    limit_amount: float = Field(ge=0)
    month: str = Field(pattern=MONTH_PATTERN)
```

### Collection & indexes (`backend/database.py`)

A dedicated `budgets` collection holds one document per
`(tenant_id, month, category)`. Indexes added:

| Collection | Index | Purpose |
| --- | --- | --- |
| `receipts` | `{tenant_id:1, date:1, category:1}` | Keeps budget-progress Stage 1 an index scan |
| `budgets`  | `{tenant_id:1, month:1, category:1}` **unique** | Enforces the upsert key; prevents duplicates |

---

## 2. Backend API (`backend/routes/expenses.py`)

Mounted at `/expenses` in `main.py`. All endpoints are tenant-scoped via the
existing `get_tenant_id` dependency (server-trusted identity — never a
client-supplied id), and manual expenses **bypass Redis/Celery entirely**.

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/expenses/manual` | Create a manual expense (`201`). Server stamps `tenant_id`, `source="manual"`, `created_at`. |
| `POST` | `/expenses/budgets` | Idempotent upsert of a category limit for a month. |
| `GET`  | `/expenses/budgets/{month}` | List the tenant's budgets for `YYYY-MM`. |

```python
@router.post("/manual", status_code=201)
async def create_manual_expense(payload: ManualExpenseCreate,
                                tenant_id: str = Depends(get_tenant_id)):
    now = datetime.now(timezone.utc)              # tz-aware, not utcnow()
    doc = payload.model_dump()                    # Pydantic v2
    doc.update({"tenant_id": tenant_id, "source": ExpenseSource.MANUAL.value,
                "items": [], "created_at": now, "updated_at": now})
    result = await collection_receipts.insert_one(doc)   # Motor module-global
    return {"id": str(result.inserted_id), "status": "created", "source": "manual"}
```

The budget upsert uses `$set` for the limit and `$setOnInsert` for the
immutable key, so re-submitting the same `(month, category)` simply overwrites
the limit (matching the unique index).

---

## 3. Budget vs. actual (`backend/routes/analytics.py`)

`GET /analytics/budget-progress/{month}` returns one row per category, **unioning
both sides** so the UI surfaces:

1. categories with a budget and tracked spend,
2. budgets with **no spend yet** (actual `0`), and
3. spend in categories with **no budget** (limit `0` — "unbudgeted leaks").

> **Implementation note (deviation from the original blueprint):** the first
> draft proposed a single `$lookup` sub-pipeline joining `receipts → budgets`.
> That is elegant but (a) the in-memory test database (`mongomock-motor`) can't
> execute `$lookup` with `let`/`pipeline`, and (b) a lookup-from-receipts can't
> show budgets that have zero spend. We instead do a small `$match → $group`
> aggregation for actual spend and a plain `find` for limits, then merge in
> Python. The `{tenant_id, date, category}` compound index keeps the aggregation
> index-backed.

```python
@router.get("/budget-progress/{month}")
async def get_budget_progress(month: str, tenant_id: str = Depends(get_tenant_id)):
    start, end = _month_bounds(month)             # raises 400 on bad YYYY-MM
    match = dict(_tenant_match_stage(tenant_id)["$match"])
    match["date"] = {"$gte": start, "$lte": end}
    spend = await collection_receipts.aggregate([
        {"$match": match},
        {"$group": {"_id": {"$ifNull": ["$category", "Uncategorized"]},
                    "actual_amount": {"$sum": "$total_amount"}}},
    ]).to_list(length=500)
    actual = {r["_id"] or "Uncategorized": round(r["actual_amount"] or 0, 2) for r in spend}

    budgets = await collection_budgets.find(
        {"tenant_id": tenant_id, "month": month}).to_list(length=500)
    limits = {b["category"]: round(b.get("limit_amount", 0) or 0, 2) for b in budgets}

    rows = [{"category": c, "actual": actual.get(c, 0.0), "limit": limits.get(c, 0.0)}
            for c in (set(actual) | set(limits))]
    rows.sort(key=lambda r: r["actual"], reverse=True)
    return rows
```

**Response shape** (drives the Recharts/Tailwind progress bars):

```json
[
  { "category": "Groceries", "actual": 420.50, "limit": 500.00 },
  { "category": "Dining",    "actual": 185.00, "limit": 150.00 },
  { "category": "Shopping",  "actual": 65.23,  "limit": 0.00 }
]
```

---

## 4. Frontend (`frontend/src/`)

| Component | Change |
| --- | --- |
| `constants.js` *(new)* | Shared `CATEGORIES` list + `currentMonth()` helper (de-duplicated from `ReceiptsList`). |
| `ManualExpenseModal.jsx` *(new)* | Modal form (merchant, amount, date, category, notes) → `POST /expenses/manual`, then refreshes the dashboard + table. |
| `BudgetPanel.jsx` *(new)* | Reads `/analytics/budget-progress/{month}`, renders color-coded progress bars (green < 85%, amber ≤ limit, red over), and has an inline "set budget" form → `POST /expenses/budgets`. |
| `App.jsx` | Header "Log manual expense" button that opens the modal; shared `refreshKey` re-fetches dashboard + table on create. |
| `Dashboard.jsx` | Renders `<BudgetPanel>` below the charts (same `refreshTrigger`). |
| `ReceiptsList.jsx` | New **Source** column with a `Manual` / `OCR` badge; imports the shared `CATEGORIES`. |

### Progress-bar color logic

```js
const overBudget = item.limit > 0 && item.actual > item.limit;
const pct = item.limit > 0 ? Math.min((item.actual / item.limit) * 100, 100) : 100;
// bar: over → rose, >85% → amber, else → emerald (no limit → neutral blue)
```

---

## 5. Architecture flow

```
[ UI: Manual modal ] ──POST /expenses/manual──► [ FastAPI ] ──► [ MongoDB: receipts (source=manual) ]
                                                                          ▲
[ UI: Receipt drop ] ──► [ FastAPI ] ──► [ Redis/Celery ] ──► [ EasyOCR ]─┘   (source=ocr)

[ UI: Budget form ] ──POST /expenses/budgets──► [ FastAPI ] ──► [ MongoDB: budgets ]
[ Dashboard ] ──GET /analytics/budget-progress/{month}──► [ FastAPI: aggregate spend + merge limits ]
```

Because manual expenses share the `receipts` schema, the existing
`/analytics/monthly`, `/analytics/merchant`, and `/analytics/category` endpoints,
the CSV/Excel exporters, and the edit/delete flows all include them with **zero
extra code**.

---

## 6. Tests

| File | Covers |
| --- | --- |
| `test_expenses_api.py` | Manual expense stored with `source` + tenant; appears in `/receipts/`; negative amount → 422; budget upsert idempotency; bad month → 422; budget tenant isolation. |
| `test_analytics_api.py` | `budget-progress` merges actual + limits, excludes other months/tenants, surfaces budgets with no spend; bad month → 400. |

Run: `pytest backend/tests/test_expenses_api.py backend/tests/test_analytics_api.py -q`
