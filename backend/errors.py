"""Consistent error handling.

We keep the FastAPI-native ``{"detail": ...}`` body that the frontend and existing
tests already depend on, and additionally surface a stable machine-readable ``code``
for programmatic clients. The catch-all handler logs full context server-side (behind
access control) and returns a generic message — it never echoes exception text, stack
traces, hostnames, or other internals to the client.
"""

import logging
import uuid
from typing import Any, Dict, Optional

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class ExtractaError(Exception):
    """Base class for typed, client-safe application errors."""

    status_code: int = 400
    code: str = "ERROR"

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        code: Optional[str] = None,
    ):
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code
        super().__init__(message)


class NotFoundError(ExtractaError):
    def __init__(self, message: str = "Resource not found."):
        super().__init__(message, 404, "NOT_FOUND")


class ValidationError(ExtractaError):
    def __init__(self, message: str = "Invalid request."):
        super().__init__(message, 400, "VALIDATION_ERROR")


def _body(message: str, code: str, **extra: Any) -> Dict[str, Any]:
    body: Dict[str, Any] = {"detail": message, "code": code}
    body.update(extra)
    return body


async def extracta_error_handler(request: Request, exc: ExtractaError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code, content=_body(exc.message, exc.code)
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last line of defence. Logs the real error with a correlation id and returns a
    generic 500 so internals never reach the client (see logging/error-message rules)."""
    error_id = uuid.uuid4().hex[:12]
    logger.exception(
        "unhandled error id=%s method=%s path=%s",
        error_id,
        request.method,
        request.url.path,
    )
    return JSONResponse(
        status_code=500,
        content=_body(
            "An unexpected error occurred.", "INTERNAL_ERROR", error_id=error_id
        ),
    )
