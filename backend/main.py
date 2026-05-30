import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import ensure_indexes
from routes import analytics, receipts

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await ensure_indexes()
    yield


app = FastAPI(title="OCR Expense Intelligence", lifespan=lifespan)

# CORS Configuration. Origins are configurable via ALLOWED_ORIGINS (comma
# separated) so deployments / demos on non-default ports can be allow-listed
# without code changes; falls back to the standard local dev ports.
_default_origins = "http://localhost:3000,http://localhost:5173"
origins = [
    o.strip()
    for o in os.getenv("ALLOWED_ORIGINS", _default_origins).split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(receipts.router, prefix="/receipts", tags=["receipts"])
app.include_router(analytics.router, prefix="/analytics", tags=["analytics"])


@app.get("/")
async def root():
    return {"message": "OCR Expense Intelligence API is running"}
