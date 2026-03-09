from __future__ import annotations

import json
import logging
import re
import uuid
from time import perf_counter
from typing import Any

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.core.redact import redact_data
from app.db.models import RequestLog
from app.errors import FcamError, build_error_response, build_proxy_error_response, is_proxy_path
from app.observability.logging import request_id_ctx
from app.observability.metrics import RequestMetrics

logger = logging.getLogger(__name__)

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def _is_valid_request_id(value: str) -> bool:
    return bool(_REQUEST_ID_RE.fullmatch(value))


def _new_request_id() -> str:
    return uuid.uuid4().hex


def _infer_api_endpoint(path: str) -> str | None:
    prefix: str | None = None
    if path.startswith("/api/"):
        prefix = "/api/"
    elif path.startswith("/v1/"):
        prefix = "/v1/"
    elif path.startswith("/v2/"):
        prefix = "/v2/"
    elif path.startswith("/exa/"):
        prefix = "/exa/"
    else:
        return None

    suffix = path[len(prefix) :].strip("/")
    if not suffix:
        return "unknown"

    if prefix == "/exa/":
        first = suffix.split("/", 1)[0]
        endpoint = f"exa_{first[:32]}" if first else "unknown"
        return endpoint or "unknown"

    first, *rest = suffix.split("/", 1)
    if first == "crawl":
        return "crawl_status" if rest else "crawl"

    endpoint = first[:32] if first else "unknown"
    return endpoint or "unknown"


_DEFAULT_SENSITIVE_KEYS = {
    "authorization",
    "api_key",
    "token",
    "password",
    "secret",
    "master_key",
}

_MAX_ERROR_DETAILS_CHARS = 2000
_MAX_ERROR_BODY_PREVIEW_BYTES = 8192


def _dump_error_details(value: Any) -> str | None:
    if value is None:
        return None

    try:
        safe = redact_data(value, _DEFAULT_SENSITIVE_KEYS)
        text = json.dumps(safe, ensure_ascii=False)
        if len(text) <= _MAX_ERROR_DETAILS_CHARS:
            return text

        base: dict[str, Any] = {}
        if isinstance(safe, dict):
            base = {k: safe.get(k) for k in ("code", "message") if k in safe}
        base["truncated"] = True
        # `message` 可能非常大（例如上游返回超长 body preview），必须在这里也做裁剪，
        # 否则即使 preview 缩短，最终 `json.dumps(base)` 仍可能超过上限。
        msg = base.get("message")
        if msg is not None:
            try:
                msg_text = msg if isinstance(msg, str) else json.dumps(msg, ensure_ascii=False)
            except Exception:
                msg_text = str(msg)
            if len(msg_text) > 400:
                base["message"] = msg_text[:400]
                base["message_truncated"] = True
            else:
                base["message"] = msg_text

        preview_len = min(len(text), _MAX_ERROR_DETAILS_CHARS)
        for _ in range(5):
            candidate = dict(base)
            candidate["preview"] = text[:preview_len]
            dumped = json.dumps(candidate, ensure_ascii=False)
            if len(dumped) <= _MAX_ERROR_DETAILS_CHARS:
                return dumped
            preview_len = max(int(preview_len * 0.6), 0)

        dumped_base = json.dumps(base, ensure_ascii=False)
        if len(dumped_base) <= _MAX_ERROR_DETAILS_CHARS:
            return dumped_base
        return json.dumps({"truncated": True}, ensure_ascii=False)
    except Exception:
        return None


async def _maybe_capture_error_from_response(request: Request, response: Response) -> Response:
    if getattr(request.state, "error_code", None) is not None:
        return response

    status_code = getattr(response, "status_code", None)
    if status_code is None or int(status_code) < 400:
        return response

    body: bytes = getattr(response, "body", b"") or b""
    if not body and hasattr(response, "body_iterator"):
        chunks: list[bytes] = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)
        body = b"".join(chunks)

        headers = dict(response.headers)
        headers.pop("content-length", None)
        response = Response(
            content=body,
            status_code=int(status_code),
            headers=headers,
            media_type=getattr(response, "media_type", None),
            background=getattr(response, "background", None),
        )

    content_type = (response.headers.get("content-type") or "").strip()

    details: dict[str, Any] = {
        "status_code": int(status_code),
        "content_type": content_type,
        "body_bytes": len(body),
    }

    if body:
        preview_bytes = body[:_MAX_ERROR_BODY_PREVIEW_BYTES]
        details["body_preview"] = preview_bytes.decode("utf-8", errors="replace")
        details["body_truncated"] = len(body) > _MAX_ERROR_BODY_PREVIEW_BYTES
        if "application/json" in content_type.lower() and not details["body_truncated"]:
            try:
                details["body_json"] = json.loads(body)
            except Exception:
                pass

    request.state.error_code = "UPSTREAM_HTTP_ERROR"
    request.state.error_details = {
        "code": "UPSTREAM_HTTP_ERROR",
        "message": "Upstream returned error response",
        "details": details,
    }
    return response


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
    error_details = _dump_error_details(getattr(request.state, "error_details", None))

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
                        error_details=error_details,
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
            response = await _maybe_capture_error_from_response(request, response)
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
            if is_proxy_path(request.url.path):
                return build_proxy_error_response(request_id, exc)
            return build_error_response(request_id, exc)


class RequestLimitsMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, max_body_bytes: int, allowed_api_paths: set[str], allowed_exa_paths: set[str] | None = None):
        super().__init__(app)
        self._max_body_bytes = max_body_bytes
        self._allowed_api_paths = {p.strip("/") for p in allowed_api_paths}
        self._allowed_exa_paths = {p.strip("/") for p in (allowed_exa_paths or set())}

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path

        api_prefix: str | None = None
        if path.startswith("/api/"):
            api_prefix = "/api/"
        elif path.startswith("/v1/"):
            api_prefix = "/v1/"
        elif path.startswith("/v2/"):
            api_prefix = "/v2/"

        if api_prefix is not None:
            seg = path[len(api_prefix) :].split("/", 1)[0]
            if seg not in self._allowed_api_paths:
                raise FcamError(status_code=404, code="PATH_NOT_ALLOWED", message="Path not allowed")

        if path.startswith("/exa/") and self._allowed_exa_paths:
            seg = path[len("/exa/") :].split("/", 1)[0]
            if seg not in self._allowed_exa_paths:
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
