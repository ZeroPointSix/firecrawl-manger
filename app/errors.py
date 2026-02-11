from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.observability.logging import get_request_id

logger = logging.getLogger(__name__)


class ErrorInfo(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    request_id: str
    error: ErrorInfo


class FcamError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.retry_after = retry_after


def _json_error(
    *,
    request_id: str,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    retry_after: int | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        request_id=request_id,
        error=ErrorInfo(code=code, message=message, details=details),
    )
    headers: dict[str, str] = {"X-Request-Id": request_id}
    if retry_after is not None:
        headers["Retry-After"] = str(int(retry_after))
    return JSONResponse(status_code=status_code, content=body.model_dump(), headers=headers)


def build_error_response(request_id: str, exc: FcamError) -> JSONResponse:
    return _json_error(
        request_id=request_id,
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        details=exc.details,
        retry_after=exc.retry_after,
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(FcamError)
    async def _handle_fcam_error(request: Request, exc: FcamError) -> JSONResponse:
        request_id = get_request_id() or getattr(request.state, "request_id", None) or "-"
        request.state.error_code = exc.code
        logger.warning(
            "request.rejected",
            extra={
                "fields": {
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": exc.status_code,
                    "error_code": exc.code,
                }
            },
        )
        return build_error_response(request_id, exc)

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        request_id = get_request_id() or getattr(request.state, "request_id", None) or "-"
        request.state.error_code = "VALIDATION_ERROR"
        safe_errors = [{k: v for k, v in e.items() if k != "input"} for e in exc.errors()]
        logger.warning(
            "request.validation_failed",
            extra={
                "fields": {
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                }
            },
        )
        return _json_error(
            request_id=request_id,
            status_code=400,
            code="VALIDATION_ERROR",
            message="Validation error",
            details={"errors": safe_errors},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        request_id = get_request_id() or getattr(request.state, "request_id", None) or "-"
        code = "NOT_FOUND" if exc.status_code == 404 else "INTERNAL_ERROR"
        message = "Not found" if exc.status_code == 404 else "HTTP error"
        request.state.error_code = code
        logger.warning(
            "request.http_error",
            extra={
                "fields": {
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": exc.status_code,
                }
            },
        )
        return _json_error(request_id=request_id, status_code=exc.status_code, code=code, message=message)

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        request_id = get_request_id() or getattr(request.state, "request_id", None) or "-"
        request.state.error_code = "INTERNAL_ERROR"
        logger.exception(
            "request.unhandled_exception",
            extra={
                "fields": {
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                }
            },
        )
        return _json_error(
            request_id=request_id,
            status_code=500,
            code="INTERNAL_ERROR",
            message="Internal error",
        )
