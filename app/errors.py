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


def _firecrawl_json_error(
    *,
    request_id: str,
    status_code: int,
    code: str | None = None,
    message: str,
    retry_after: int | None = None,
) -> JSONResponse:
    headers: dict[str, str] = {"X-Request-Id": request_id}
    if retry_after is not None:
        headers["Retry-After"] = str(int(retry_after))
    content: dict[str, object] = {"success": False, "error": str(message)}
    if code:
        content["code"] = str(code)
    return JSONResponse(
        status_code=int(status_code),
        content=content,
        headers=headers,
    )


def _is_proxy_path(path: str) -> bool:
    return path.startswith("/api/") or path.startswith("/v1/") or path.startswith("/v2/") or path.startswith("/exa/")


def is_proxy_path(path: str) -> bool:
    return _is_proxy_path(path)


def build_proxy_error_response(request_id: str, exc: FcamError) -> JSONResponse:
    return _firecrawl_json_error(
        request_id=request_id,
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        retry_after=exc.retry_after,
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(FcamError)
    async def _handle_fcam_error(request: Request, exc: FcamError) -> JSONResponse:
        request_id = get_request_id() or getattr(request.state, "request_id", None) or "-"
        request.state.error_code = exc.code
        request.state.error_details = {
            "code": exc.code,
            "message": exc.message,
            "details": exc.details,
            "retry_after": exc.retry_after,
        }
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
        if _is_proxy_path(request.url.path):
            return _firecrawl_json_error(
                request_id=request_id,
                status_code=exc.status_code,
                code=exc.code,
                message=exc.message,
                retry_after=exc.retry_after,
            )
        return build_error_response(request_id, exc)

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        request_id = get_request_id() or getattr(request.state, "request_id", None) or "-"
        request.state.error_code = "VALIDATION_ERROR"
        safe_errors = [{k: v for k, v in e.items() if k != "input"} for e in exc.errors()]
        request.state.error_details = {
            "code": "VALIDATION_ERROR",
            "message": "Validation error",
            "details": {"errors": safe_errors[:20]},
        }
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
        if _is_proxy_path(request.url.path):
            return _firecrawl_json_error(
                request_id=request_id,
                status_code=400,
                code="VALIDATION_ERROR",
                message="Invalid request parameters",
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
        request.state.error_details = {
            "code": code,
            "message": message,
            "details": {"status_code": exc.status_code},
        }
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
        if _is_proxy_path(request.url.path):
            return _firecrawl_json_error(
                request_id=request_id,
                status_code=exc.status_code,
                code=code,
                message=message,
            )
        return _json_error(request_id=request_id, status_code=exc.status_code, code=code, message=message)

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        request_id = get_request_id() or getattr(request.state, "request_id", None) or "-"
        request.state.error_code = "INTERNAL_ERROR"
        request.state.error_details = {
            "code": "INTERNAL_ERROR",
            "message": "Internal error",
            "details": {"exception_type": exc.__class__.__name__},
        }
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
        if _is_proxy_path(request.url.path):
            return _firecrawl_json_error(
                request_id=request_id,
                status_code=500,
                code="UNKNOWN_ERROR",
                message="An unexpected error occurred on the server.",
            )
        return _json_error(
            request_id=request_id,
            status_code=500,
            code="INTERNAL_ERROR",
            message="Internal error",
        )
