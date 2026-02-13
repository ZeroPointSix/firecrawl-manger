from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Any

import httpx
from cryptography.exceptions import InvalidTag
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.config import AppConfig, Secrets
from app.core.concurrency import ConcurrencyManager
from app.core.key_pool import KeyPool
from app.core.cooldown import NoopCooldownStore
from app.core.rate_limit import TokenBucketRateLimiter
from app.core.security import decrypt_api_key, derive_master_key_bytes
from app.db.models import ApiKey, Client
from app.errors import FcamError
from app.observability.metrics import Metrics

logger = logging.getLogger(__name__)

_DROP_REQUEST_HEADERS = {
    "authorization",
    "host",
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "x-forwarded-for",
    "x-forwarded-proto",
    "x-forwarded-host",
    "x-real-ip",
}

_DROP_RESPONSE_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "set-cookie",
}


@dataclass
class ForwardResult:
    response: Response
    upstream_status_code: int | None
    api_key_id: int | None
    retry_count: int


@dataclass
class KeyTestResult:
    ok: bool
    upstream_status_code: int | None
    latency_ms: int
    observed_status: str
    observed_cooldown_until: datetime | None


def _parse_retry_after(headers: httpx.Headers) -> int | None:
    raw = headers.get("retry-after")
    if not raw:
        return None
    try:
        seconds = int(raw.strip())
    except ValueError:
        return None
    return max(seconds, 0)


def _sanitized_request_headers(in_headers: httpx.Headers, request_id: str) -> dict[str, str]:
    headers: dict[str, str] = {"x-request-id": request_id}
    accept = in_headers.get("accept")
    if accept:
        headers["accept"] = accept
    content_type = in_headers.get("content-type")
    if content_type:
        headers["content-type"] = content_type
    user_agent = in_headers.get("user-agent")
    if user_agent:
        headers["user-agent"] = user_agent
    return headers


def _to_fastapi_response(upstream: httpx.Response) -> Response:
    headers: dict[str, str] = {}
    for k, v in upstream.headers.items():
        lk = k.lower()
        if lk in _DROP_RESPONSE_HEADERS:
            continue
        headers[k] = v
    return Response(content=upstream.content, status_code=upstream.status_code, headers=headers)


class Forwarder:
    def __init__(
        self,
        *,
        config: AppConfig,
        secrets: Secrets,
        key_pool: KeyPool,
        key_concurrency: ConcurrencyManager,
        key_rate_limiter: TokenBucketRateLimiter | None = None,
        metrics: Metrics | None = None,
        cooldown_store: object | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._config = config
        self._master_key = derive_master_key_bytes(secrets.master_key) if secrets.master_key else None
        self._key_pool = key_pool
        self._key_concurrency = key_concurrency
        self._key_rate_limiter = key_rate_limiter or TokenBucketRateLimiter()
        self._metrics = metrics
        self._cooldown_store = cooldown_store or NoopCooldownStore()
        self._transport = transport

        self._failure_lock = threading.Lock()
        self._failures: dict[int, tuple[int, datetime]] = {}

    def _disable_key_decrypt_failed(self, db: Session, key: ApiKey) -> None:
        try:
            key.status = "decrypt_failed"
            key.is_active = False
            db.commit()
            logger.warning(
                "key.decrypt_failed",
                extra={"fields": {"api_key_id": key.id}},
            )
        except Exception:
            db.rollback()
            logger.exception(
                "db.key_disable_failed",
                extra={"fields": {"api_key_id": key.id}},
            )

    def forward(
        self,
        *,
        db: Session,
        request_id: str,
        client: Client,
        method: str,
        upstream_path: str,
        json_body: Any | None,
        inbound_headers: dict[str, str],
    ) -> ForwardResult:
        if self._master_key is None:
            raise FcamError(status_code=503, code="NOT_READY", message="Master key not configured")
        timeout = httpx.Timeout(self._config.firecrawl.timeout)

        last_upstream_status: int | None = None
        last_api_key_id: int | None = None

        headers = httpx.Headers(inbound_headers)
        safe_headers = _sanitized_request_headers(headers, request_id)

        total_attempts = max(self._config.firecrawl.max_retries, 0) + 1
        retry_count = 0

        with httpx.Client(
            timeout=timeout,
            base_url=self._config.firecrawl.base_url,
            transport=self._transport,
            follow_redirects=False,
        ) as client_http:
            max_selection_tries = max(total_attempts * 20, 50)
            upstream_attempts = 0
            selection_tries = 0
            decrypt_failed_seen = False

            while upstream_attempts < total_attempts and selection_tries < max_selection_tries:
                selection_tries += 1
                try:
                    selected = self._key_pool.select(db, self._config, client_id=client.id)
                except FcamError as exc:
                    if decrypt_failed_seen and exc.code in {
                        "NO_KEY_CONFIGURED",
                        "ALL_KEYS_DISABLED",
                        "NO_KEY_AVAILABLE",
                    }:
                        raise FcamError(
                            status_code=503,
                            code="KEY_DECRYPT_FAILED",
                            message="Failed to decrypt API key; check FCAM_MASTER_KEY",
                        ) from exc
                    raise
                key: ApiKey = selected.api_key
                last_api_key_id = key.id
                if self._metrics is not None:
                    self._metrics.record_key_selected(key.id)

                allowed, _retry_after = self._key_rate_limiter.allow(str(key.id), key.rate_limit_per_min)
                if not allowed:
                    retry_count += 1
                    continue

                lease = self._key_concurrency.try_acquire(str(key.id), key.max_concurrent)
                if lease is None:
                    retry_count += 1
                    continue

                try:
                    try:
                        plaintext_api_key = decrypt_api_key(self._master_key, key.api_key_ciphertext)
                    except (InvalidTag, ValueError):
                        decrypt_failed_seen = True
                        self._disable_key_decrypt_failed(db, key)
                        retry_count += 1
                        continue

                    upstream_headers = dict(safe_headers)
                    upstream_headers["authorization"] = f"Bearer {plaintext_api_key}"

                    for hk in list(upstream_headers.keys()):
                        if hk.lower() in _DROP_REQUEST_HEADERS and hk.lower() != "authorization":
                            upstream_headers.pop(hk, None)

                    resp = client_http.request(
                        method=method,
                        url=upstream_path,
                        headers=upstream_headers,
                        json=json_body,
                    )
                    last_upstream_status = resp.status_code
                    upstream_attempts += 1

                except httpx.TimeoutException as exc:
                    self._record_failure(db, key, reason="timeout")
                    retry_count += 1
                    upstream_attempts += 1
                    if upstream_attempts >= total_attempts:
                        logger.info(
                            "upstream.timeout",
                            extra={"fields": {"request_id": request_id, "api_key_id": key.id}},
                        )
                        raise FcamError(
                            status_code=504,
                            code="UPSTREAM_TIMEOUT",
                            message="Upstream timeout",
                        ) from exc
                    continue

                except httpx.HTTPError as exc:
                    self._record_failure(db, key, reason="http_error")
                    retry_count += 1
                    upstream_attempts += 1
                    if upstream_attempts >= total_attempts:
                        logger.info(
                            "upstream.unavailable",
                            extra={"fields": {"request_id": request_id, "api_key_id": key.id}},
                        )
                        raise FcamError(
                            status_code=503,
                            code="UPSTREAM_UNAVAILABLE",
                            message="Upstream unavailable",
                        ) from exc
                    continue

                finally:
                    lease.release()

                if resp.status_code == 429:
                    cooldown = _parse_retry_after(resp.headers) or self._config.rate_limit.cooldown_seconds
                    self._mark_cooling(db, key, cooldown)
                    retry_count += 1
                    if upstream_attempts >= total_attempts:
                        return ForwardResult(
                            response=_to_fastapi_response(resp),
                            upstream_status_code=last_upstream_status,
                            api_key_id=last_api_key_id,
                            retry_count=retry_count - 1,
                        )
                    continue

                if resp.status_code in {401, 403}:
                    self._disable_key(db, key, resp.status_code)
                    retry_count += 1
                    if upstream_attempts >= total_attempts:
                        return ForwardResult(
                            response=_to_fastapi_response(resp),
                            upstream_status_code=last_upstream_status,
                            api_key_id=last_api_key_id,
                            retry_count=retry_count - 1,
                        )
                    continue

                if resp.status_code >= 500:
                    self._record_failure(db, key, reason="upstream_5xx")
                    retry_count += 1
                    if upstream_attempts >= total_attempts:
                        return ForwardResult(
                            response=_to_fastapi_response(resp),
                            upstream_status_code=last_upstream_status,
                            api_key_id=last_api_key_id,
                            retry_count=retry_count - 1,
                        )
                    continue

                if 200 <= resp.status_code < 300 and self._config.quota.count_mode == "success":
                    self._consume_quota_on_success(db, client, key)

                return ForwardResult(
                    response=_to_fastapi_response(resp),
                    upstream_status_code=last_upstream_status,
                    api_key_id=last_api_key_id,
                    retry_count=retry_count,
                )

        if upstream_attempts == 0:
            raise FcamError(
                status_code=503,
                code="ALL_KEYS_BUSY",
                message="All keys busy or rate-limited",
            )
        raise FcamError(status_code=503, code="UPSTREAM_UNAVAILABLE", message="Upstream unavailable")

    def test_key(
        self,
        *,
        db: Session,
        request_id: str,
        key: ApiKey,
        mode: str = "scrape",
        test_url: str = "https://example.com",
    ) -> KeyTestResult:
        if self._master_key is None:
            raise FcamError(status_code=503, code="NOT_READY", message="Master key not configured")

        if mode != "scrape":
            raise FcamError(status_code=400, code="VALIDATION_ERROR", message="Unsupported test mode")

        timeout = httpx.Timeout(self._config.firecrawl.timeout)
        headers = httpx.Headers({"x-request-id": request_id, "content-type": "application/json"})

        start = perf_counter()
        try:
            try:
                plaintext_api_key = decrypt_api_key(self._master_key, key.api_key_ciphertext)
            except (InvalidTag, ValueError):
                self._disable_key_decrypt_failed(db, key)
                latency_ms = int((perf_counter() - start) * 1000)
                return KeyTestResult(
                    ok=False,
                    upstream_status_code=None,
                    latency_ms=latency_ms,
                    observed_status=key.status,
                    observed_cooldown_until=key.cooldown_until,
                )

            upstream_headers = dict(headers)
            upstream_headers["authorization"] = f"Bearer {plaintext_api_key}"

            with httpx.Client(
                timeout=timeout,
                base_url=self._config.firecrawl.base_url,
                transport=self._transport,
                follow_redirects=False,
            ) as client_http:
                resp = client_http.request(
                    method="POST",
                    url="/scrape",
                    headers=upstream_headers,
                    json={"url": test_url},
                )

            latency_ms = int((perf_counter() - start) * 1000)

        except httpx.TimeoutException as exc:
            self._record_failure(db, key, reason="timeout")
            latency_ms = int((perf_counter() - start) * 1000)
            logger.info(
                "upstream.key_test_timeout",
                extra={
                    "fields": {
                        "request_id": request_id,
                        "api_key_id": key.id,
                        "base_url": self._config.firecrawl.base_url,
                        "timeout_s": self._config.firecrawl.timeout,
                        "error": str(exc),
                    }
                },
            )
            return KeyTestResult(
                ok=False,
                upstream_status_code=None,
                latency_ms=latency_ms,
                observed_status=key.status,
                observed_cooldown_until=key.cooldown_until,
            )

        except httpx.HTTPError as exc:
            self._record_failure(db, key, reason="http_error")
            latency_ms = int((perf_counter() - start) * 1000)
            logger.info(
                "upstream.key_test_http_error",
                extra={
                    "fields": {
                        "request_id": request_id,
                        "api_key_id": key.id,
                        "base_url": self._config.firecrawl.base_url,
                        "error": str(exc),
                    }
                },
            )
            return KeyTestResult(
                ok=False,
                upstream_status_code=None,
                latency_ms=latency_ms,
                observed_status=key.status,
                observed_cooldown_until=key.cooldown_until,
            )

        if 200 <= resp.status_code < 300:
            self._mark_active(db, key)
            return KeyTestResult(
                ok=True,
                upstream_status_code=resp.status_code,
                latency_ms=latency_ms,
                observed_status=key.status,
                observed_cooldown_until=key.cooldown_until,
            )

        if resp.status_code == 429:
            cooldown = _parse_retry_after(resp.headers) or self._config.rate_limit.cooldown_seconds
            self._mark_cooling(db, key, cooldown)
            logger.info(
                "upstream.key_test_rate_limited",
                extra={
                    "fields": {
                        "request_id": request_id,
                        "api_key_id": key.id,
                        "base_url": self._config.firecrawl.base_url,
                        "upstream_status_code": resp.status_code,
                        "cooldown_seconds": int(cooldown),
                    }
                },
            )
            return KeyTestResult(
                ok=False,
                upstream_status_code=resp.status_code,
                latency_ms=latency_ms,
                observed_status=key.status,
                observed_cooldown_until=key.cooldown_until,
            )

        if resp.status_code in {401, 403}:
            self._disable_key(db, key, resp.status_code)
            logger.info(
                "upstream.key_test_unauthorized",
                extra={
                    "fields": {
                        "request_id": request_id,
                        "api_key_id": key.id,
                        "base_url": self._config.firecrawl.base_url,
                        "upstream_status_code": resp.status_code,
                    }
                },
            )
            return KeyTestResult(
                ok=False,
                upstream_status_code=resp.status_code,
                latency_ms=latency_ms,
                observed_status=key.status,
                observed_cooldown_until=key.cooldown_until,
            )

        if resp.status_code >= 500:
            self._record_failure(db, key, reason="upstream_5xx")
            logger.info(
                "upstream.key_test_5xx",
                extra={
                    "fields": {
                        "request_id": request_id,
                        "api_key_id": key.id,
                        "base_url": self._config.firecrawl.base_url,
                        "upstream_status_code": resp.status_code,
                    }
                },
            )
            return KeyTestResult(
                ok=False,
                upstream_status_code=resp.status_code,
                latency_ms=latency_ms,
                observed_status=key.status,
                observed_cooldown_until=key.cooldown_until,
            )

        return KeyTestResult(
            ok=False,
            upstream_status_code=resp.status_code,
            latency_ms=latency_ms,
            observed_status=key.status,
            observed_cooldown_until=key.cooldown_until,
        )

    def _consume_quota_on_success(self, db: Session, client: Client, key: ApiKey) -> None:
        try:
            if client.daily_quota is not None:
                client.daily_usage += 1
            client.last_used_at = datetime.now(timezone.utc)

            key.daily_usage += 1
            key.total_requests += 1
            key.last_used_at = datetime.now(timezone.utc)
            if key.daily_quota is not None and key.daily_usage >= key.daily_quota:
                key.status = "quota_exceeded"

            db.commit()
            if self._metrics is not None:
                if client.daily_quota is not None:
                    self._metrics.set_quota_remaining(
                        scope="client",
                        id=client.id,
                        remaining=max(int(client.daily_quota) - int(client.daily_usage), 0),
                    )
                if key.daily_quota is not None:
                    self._metrics.set_quota_remaining(
                        scope="key",
                        id=key.id,
                        remaining=max(int(key.daily_quota) - int(key.daily_usage), 0),
                    )
        except Exception as exc:
            db.rollback()
            logger.exception(
                "db.quota_consume_failed",
                extra={"fields": {"client_id": client.id, "api_key_id": key.id}},
            )
            raise FcamError(status_code=503, code="DB_UNAVAILABLE", message="Database unavailable") from exc

    def _mark_active(self, db: Session, key: ApiKey) -> None:
        if not key.is_active or key.status == "disabled":
            return
        try:
            key.status = "active"
            key.cooldown_until = None
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("db.key_active_update_failed", extra={"fields": {"api_key_id": key.id}})

    def _mark_cooling(self, db: Session, key: ApiKey, cooldown_seconds: int) -> None:
        try:
            key.status = "cooling"
            key.cooldown_until = datetime.now(timezone.utc) + timedelta(seconds=cooldown_seconds)
            db.commit()
            if self._metrics is not None:
                self._metrics.record_key_cooldown(key.id)
            if hasattr(self._cooldown_store, "set_cooldown"):
                try:
                    self._cooldown_store.set_cooldown(  # type: ignore[attr-defined]
                        key_id=key.id,
                        cooldown_seconds=int(cooldown_seconds),
                    )
                except Exception:
                    logger.exception(
                        "state.cooldown_set_failed",
                        extra={"fields": {"api_key_id": key.id}},
                    )
        except Exception:
            db.rollback()
            logger.exception("db.key_cooling_update_failed", extra={"fields": {"api_key_id": key.id}})

    def _disable_key(self, db: Session, key: ApiKey, status_code: int) -> None:
        try:
            key.status = "disabled"
            key.is_active = False
            db.commit()
            logger.info(
                "key.disabled",
                extra={"fields": {"api_key_id": key.id, "upstream_status_code": status_code}},
            )
        except Exception:
            db.rollback()
            logger.exception("db.key_disable_failed", extra={"fields": {"api_key_id": key.id}})

    def _record_failure(self, db: Session, key: ApiKey, reason: str) -> None:
        now = datetime.now(timezone.utc)
        with self._failure_lock:
            count, first_at = self._failures.get(key.id, (0, now))
            if (now - first_at).total_seconds() > self._config.firecrawl.failure_window_seconds:
                count, first_at = 0, now
            count += 1
            self._failures[key.id] = (count, first_at)

        if count < self._config.firecrawl.failure_threshold:
            return

        cooldown = max(self._config.firecrawl.failed_cooldown_seconds, 1)
        try:
            key.status = "failed"
            key.cooldown_until = now + timedelta(seconds=cooldown)
            db.commit()
            logger.info(
                "key.failed",
                extra={"fields": {"api_key_id": key.id, "reason": reason, "cooldown_seconds": cooldown}},
            )
        except Exception:
            db.rollback()
            logger.exception("db.key_failed_update_failed", extra={"fields": {"api_key_id": key.id}})
