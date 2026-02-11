from __future__ import annotations

import logging
import re
import uuid
from time import perf_counter

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.db.models import RequestLog
from app.errors import FcamError, build_error_response
from app.observability.logging import request_id_ctx
from app.observability.metrics import RequestMetrics

logger = logging.getLogger(__name__)

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def _is_valid_request_id(value: str) -> bool:
    return bool(_REQUEST_ID_RE.fullmatch(value))


def _new_request_id() -> str:
    return uuid.uuid4().hex


def _infer_api_endpoint(path: str) -> str | None:
    if not path.startswith("/api/"):
        return None

    suffix = path[len("/api/") :].strip("/")
    if not suffix:
        return "unknown"

    first, *rest = suffix.split("/", 1)
    if first == "crawl":
        return "crawl_status" if rest else "crawl"

    endpoint = first[:32] if first else "unknown"
    return endpoint or "unknown"


def _persist_request_log(request: Request, *, status_code: int | None, response_time_ms: int) -> str | None:
    endpoint = getattr(request.state, "endpoint", None) or _infer_api_endpoint(request.url.path)
    if endpoint is None:
        return None

    SessionLocal = getattr(request.app.state, "db_session_factory", None)
    if SessionLocal is None:
        return endpoint

    request_id = getattr(request.state, "request_id", None) or "-"
    client_id = getattr(request.state, "client_id", None)
    api_key_id = getattr(request.state, "api_key_id", None)
    retry_count = int(getattr(request.state, "retry_count", 0) or 0)
    idempotency_key = request.headers.get("x-idempotency-key")
    error_code = getattr(request.state, "error_code", None)

    success = None
    if status_code is not None:
        success = 200 <= status_code < 300

    try:
        with SessionLocal() as db:
            try:
                db.add(
                    RequestLog(
                        request_id=request_id,
                        client_id=client_id,
                        api_key_id=api_key_id,
                        endpoint=endpoint,
                        method=request.method,
                        status_code=status_code,
                        response_time_ms=response_time_ms,
                        success=success,
                        retry_count=retry_count,
                        error_message=error_code,
                        idempotency_key=idempotency_key,
                    )
                )
                db.commit()
            except Exception:
                db.rollback()
                raise
    except Exception:
        logger.exception(
            "db.request_log_write_failed",
            extra={"fields": {"request_id": request_id, "endpoint": endpoint}},
        )
    return endpoint


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get("X-Request-Id")
        request_id = incoming if (incoming and _is_valid_request_id(incoming)) else _new_request_id()

        token = request_id_ctx.set(request_id)
        request.state.request_id = request_id

        start = perf_counter()
        try:
            response = await call_next(request)

            response.headers["X-Request-Id"] = request_id
            latency_ms = int((perf_counter() - start) * 1000)
            endpoint = _persist_request_log(
                request,
                status_code=getattr(response, "status_code", None),
                response_time_ms=latency_ms,
            )
            metrics = getattr(request.app.state, "metrics", None)
            status_code = getattr(response, "status_code", None)
            if metrics is not None and endpoint is not None and status_code is not None:
                metrics.record_request(
                    RequestMetrics(
                        endpoint=endpoint,
                        method=request.method,
                        status_code=int(status_code),
                        latency_ms=latency_ms,
                        client_id=getattr(request.state, "client_id", None),
                    )
                )
            logger.info(
                "request.completed",
                extra={
                    "fields": {
                        "request_id": request_id,
                        "client_id": getattr(request.state, "client_id", None),
                        "endpoint": endpoint,
                        "method": request.method,
                        "path": request.url.path,
                        "status_code": status_code,
                        "latency_ms": latency_ms,
                    }
                },
            )
            return response
        finally:
            request_id_ctx.reset(token)


class FcamErrorMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        try:
            return await call_next(request)
        except FcamError as exc:
            request_id = getattr(request.state, "request_id", None) or request_id_ctx.get() or _new_request_id()
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


class RequestLimitsMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, max_body_bytes: int, allowed_api_paths: set[str]):
        super().__init__(app)
        self._max_body_bytes = max_body_bytes
        self._allowed_api_paths = {p.strip("/") for p in allowed_api_paths}

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path

        if path.startswith("/api/"):
            seg = path[len("/api/") :].split("/", 1)[0]
            if seg not in self._allowed_api_paths:
                raise FcamError(status_code=404, code="PATH_NOT_ALLOWED", message="Path not allowed")

        if request.method not in {"GET", "HEAD"}:
            content_length = request.headers.get("content-length")
            if content_length:
                try:
                    length = int(content_length)
                except ValueError as exc:
                    raise FcamError(
                        status_code=400,
                        code="VALIDATION_ERROR",
                        message="Invalid Content-Length",
                    ) from exc
                if length > self._max_body_bytes:
                    raise FcamError(
                        status_code=413,
                        code="REQUEST_TOO_LARGE",
                        message="Request body too large",
                        details={"max_body_bytes": self._max_body_bytes},
                    )

            body = await request.body()
            if not body:
                return await call_next(request)

            content_type = request.headers.get("content-type", "")
            if "application/json" not in content_type:
                raise FcamError(
                    status_code=415,
                    code="UNSUPPORTED_MEDIA_TYPE",
                    message="Only application/json is supported",
                )
            if len(body) > self._max_body_bytes:
                raise FcamError(
                    status_code=413,
                    code="REQUEST_TOO_LARGE",
                    message="Request body too large",
                    details={"max_body_bytes": self._max_body_bytes},
                )

        return await call_next(request)
