from enum import Enum
from pydantic import BaseModel, Field, BeforeValidator
from typing import List, Optional, Annotated
from datetime import datetime

# Helper for ObjectId handling in Pydantic v2
PyObjectId = Annotated[str, BeforeValidator(str)]

# Format for a budget period / month bucket, e.g. "2026-06".
MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"


class ExpenseSource(str, Enum):
    """Where an expense record originated."""

    OCR = "ocr"
    MANUAL = "manual"


class Item(BaseModel):
    description: str
    amount: float
    qty: Optional[int] = 1
    unit_price: Optional[float] = None
    confidence: Optional[float] = None


class ReceiptBase(BaseModel):
    merchant_name: Optional[str] = None
    total_amount: Optional[float] = None
    date: Optional[datetime] = None
    items: List[Item] = []
    tenant_id: Optional[str] = "default"


class ReceiptCreate(ReceiptBase):
    raw_text: Optional[str] = None


class Receipt(ReceiptBase):
    id: Optional[PyObjectId] = Field(alias="_id", default=None)
    job_id: Optional[PyObjectId] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None
    raw_text: Optional[str] = None
    category: Optional[str] = "Uncategorized"
    currency: Optional[str] = "USD"
    confidence: Optional[float] = None
    needs_review: bool = False
    # OCR-extracted receipts have no `source` field; default to OCR so older
    # documents serialize correctly. Manual entries set this to "manual".
    source: ExpenseSource = ExpenseSource.OCR
    notes: Optional[str] = None

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True


class ReceiptUpdate(BaseModel):
    """Editable fields for a receipt. All optional; only provided fields change."""

    merchant_name: Optional[str] = Field(default=None, max_length=200)
    total_amount: Optional[float] = Field(default=None, ge=0)
    date: Optional[datetime] = None
    category: Optional[str] = Field(default=None, max_length=60)


class ManualExpenseCreate(BaseModel):
    """A user-entered expense. Bypasses the OCR/Celery pipeline and is written
    straight into the receipts collection with ``source = manual``."""

    merchant_name: str = Field(min_length=1, max_length=200)
    total_amount: float = Field(ge=0)
    date: datetime
    category: str = Field(min_length=1, max_length=60)
    notes: Optional[str] = Field(default=None, max_length=1000)
    currency: Optional[str] = Field(default="USD", max_length=8)


class BudgetUpsert(BaseModel):
    """A monthly spending target for a single category (upserted per tenant)."""

    category: str = Field(min_length=1, max_length=60)
    limit_amount: float = Field(ge=0)
    month: str = Field(pattern=MONTH_PATTERN)


class JobEnqueueResponse(BaseModel):
    job_id: str
    status: str = "queued"


class JobStatusResponse(BaseModel):
    job_id: str
    tenant_id: str
    status: str
    receipt_id: Optional[str] = None
    error_message: Optional[str] = None
    processing_ms: Optional[int] = None
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
