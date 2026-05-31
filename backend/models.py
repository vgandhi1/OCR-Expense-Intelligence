from pydantic import BaseModel, Field, BeforeValidator
from typing import List, Optional, Annotated
from datetime import datetime

# Helper for ObjectId handling in Pydantic v2
PyObjectId = Annotated[str, BeforeValidator(str)]


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

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True


class ReceiptUpdate(BaseModel):
    """Editable fields for a receipt. All optional; only provided fields change."""

    merchant_name: Optional[str] = Field(default=None, max_length=200)
    total_amount: Optional[float] = Field(default=None, ge=0)
    date: Optional[datetime] = None
    category: Optional[str] = Field(default=None, max_length=60)


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
