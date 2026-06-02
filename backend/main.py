import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

import health
from database import ensure_indexes
from errors import ExtractaError, extracta_error_handler, unhandled_error_handler
from rate_limit import limiter, rate_limit_exceeded_handler
from routes import admin, analytics, expenses, receipts, vendors

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await ensure_indexes()
    yield


app = FastAPI(title="OCR Expense Intelligence", lifespan=lifespan)

# Rate limiting: the limiter instance must be on app.state for the route decorators.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

# Error handling: typed app errors render to {detail, code}; the catch-all keeps
# internal details out of client responses (registered last so it's the fallback).
app.add_exception_handler(ExtractaError, extracta_error_handler)
app.add_exception_handler(Exception, unhandled_error_handler)

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
app.include_router(expenses.router, prefix="/expenses", tags=["expense-tracker"])
app.include_router(admin.router, prefix="/admin", tags=["admin"])
app.include_router(vendors.router, prefix="/vendors", tags=["vendors"])


@app.get("/")
async def root():
    return {"message": "OCR Expense Intelligence API is running"}


@app.get("/health", tags=["ops"])
async def health_check():
    """Liveness — 200 as long as the process is up. No dependency checks, so
    container/orchestrator restarts aren't triggered by transient Mongo/Redis blips."""
    return {"status": "ok", "timestamp": time.time()}


@app.get("/health/ready", tags=["ops"])
async def readiness_check():
    """Readiness — verifies MongoDB and Redis are reachable. Returns 503 if either
    is down, so load balancers / probes stop routing until dependencies recover."""
    checks = {}
    for name, probe in (("mongodb", health.check_mongodb), ("redis", health.check_redis)):
        try:
            await probe()
            checks[name] = "ok"
        except Exception:
            # Don't leak connection internals (hosts/creds) to clients; the detailed
            # error is captured server-side via the probe's own logging if needed.
            checks[name] = "error"
    all_ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if all_ok else 503,
        content={"status": "ready" if all_ok else "degraded", "checks": checks},
    )
