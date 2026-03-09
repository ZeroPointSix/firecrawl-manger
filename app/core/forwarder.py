from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from cryptography.exceptions import InvalidTag
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.config import AppConfig, Secrets
from app.core.concurrency import ConcurrencyManager
from app.core.cooldown import NoopCooldownStore
from app.core.key_pool import KeyPool
from app.core.rate_limit import TokenBucketRateLimiter
from app.core.security import decrypt_api_key, derive_master_key_bytes
from app.db.models import ApiKey, Client
from app.errors import FcamError
from app.observability.metrics import Metrics

logger = logging.getLogger(__name__)

_DROP_REQUEST_HEADERS = {
    "authorization",
    "x-api-key",
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
    "content-encoding",
    "content-length",
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


def _strip_firecrawl_version_suffix(base_url: str) -> tuple[str, str | None]:
    normalized = base_url.rstrip("/")
    parts = urlsplit(normalized)
    if not parts.scheme or not parts.netloc:
        return normalized, None

    path = parts.path.rstrip("/")
    version: str | None = None
    if path.endswith("/v1"):
        version = "v1"
        path = path[:-3]
    elif path.endswith("/v2"):
        version = "v2"
        path = path[:-3]

    stripped = urlunsplit((parts.scheme, parts.netloc, path, "", ""))
    return stripped.rstrip("/"), version


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
        self._firecrawl_upstream_base_url, self._firecrawl_upstream_version = _strip_firecrawl_version_suffix(
            self._config.firecrawl.base_url
        )
        self._master_key = derive_master_key_bytes(secrets.master_key) if secrets.master_key else None
        self._key_pool = key_pool
        self._key_concurrency = key_concurrency
        self._key_rate_limiter = key_rate_limiter or TokenBucketRateLimiter()
        self._metrics = metrics
        self._cooldown_store = cooldown_store or NoopCooldownStore()
        self._transport = transport

        self._failure_lock = threading.Lock()
        self._failures: dict[int, tuple[int, datetime]] = {}

    def _provider_base_url(self, provider: str) -> str:
        """Return the upstream base URL for the given provider."""
        if provider == "firecrawl":
            return self._firecrawl_upstream_base_url
        prov_cfg = getattr(self._config.providers, provider, None)
        if prov_cfg is None or not prov_cfg.enabled:
            raise FcamError(
                status_code=400,
                code="PROVIDER_NOT_CONFIGURED",
                message=f"Provider '{provider}' is not configured or enabled",
            )
        return prov_cfg.base_url.rstrip("/")

    def _provider_timeout(self, provider: str) -> httpx.Timeout:
        """Return the timeout for the given provider."""
        if provider == "firecrawl":
            return httpx.Timeout(self._config.firecrawl.timeout)
        prov_cfg = getattr(self._config.providers, provider, None)
        if prov_cfg is not None:
            return httpx.Timeout(prov_cfg.timeout)
        return httpx.Timeout(self._config.firecrawl.timeout)

    def _provider_max_retries(self, provider: str) -> int:
        """Return the max retries for the given provider."""
        if provider == "firecrawl":
            return self._config.firecrawl.max_retries
        prov_cfg = getattr(self._config.providers, provider, None)
        if prov_cfg is not None:
            return prov_cfg.max_retries
        return self._config.firecrawl.max_retries

    def _provider_auth_header(self, provider: str, plaintext_key: str) -> dict[str, str]:
        """Return the auth header dict for the given provider."""
        if provider == "firecrawl":
            return {"authorization": f"Bearer {plaintext_key}"}
        prov_cfg = getattr(self._config.providers, provider, None)
        if prov_cfg is not None and prov_cfg.auth_mode == "x-api-key":
            return {"x-api-key": plaintext_key}
        return {"authorization": f"Bearer {plaintext_key}"}

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
        pinned_api_key_id: int | None = None,
        provider: str = "firecrawl",
    ) -> ForwardResult:
        if self._master_key is None:
            raise FcamError(status_code=503, code="NOT_READY", message="Master key not configured")
        timeout = self._provider_timeout(provider)
        base_url = self._provider_base_url(provider)

        last_upstream_status: int | None = None
        last_api_key_id: int | None = None

        headers = httpx.Headers(inbound_headers)
        safe_headers = _sanitized_request_headers(headers, request_id)

        total_attempts = max(self._provider_max_retries(provider), 0) + 1
        retry_count = 0

        with httpx.Client(
            timeout=timeout,
            base_url=base_url,
            transport=self._transport,
            follow_redirects=False,
        ) as client_http:
            if pinned_api_key_id is not None:
                # Pinned key mode: used by "sticky" resource bindings (e.g. GET status by job_id).
                # We must not switch keys here, otherwise upstream may return 404 for resources created by
                # another key. Still enforce per-key rate-limit and concurrency gates to avoid bypassing
                # governance when callers provide pinned_api_key_id.
                try:
                    pinned_key = (
                        db.query(ApiKey)
                        .filter(
                            ApiKey.id == int(pinned_api_key_id),
                            ApiKey.client_id == client.id,
                        )
                        .one_or_none()
                    )
                except Exception as exc:
                    logger.exception(
                        "db.pinned_key_lookup_failed",
                        extra={
                            "fields": {
                                "request_id": request_id,
                                "client_id": client.id,
                                "api_key_id": pinned_api_key_id,
                            }
                        },
                    )
                    raise FcamError(status_code=503, code="DB_UNAVAILABLE", message="Database unavailable") from exc

                if pinned_key is None or (not pinned_key.is_active) or pinned_key.status == "disabled":
                    pinned_api_key_id = None
                elif pinned_key.provider != provider:
                    # Provider mismatch: pinned key belongs to a different provider
                    pinned_api_key_id = None
                else:
                    last_api_key_id = pinned_key.id
                    if self._metrics is not None:
                        self._metrics.record_key_selected(pinned_key.id)

                    for attempt in range(total_attempts):
                        allowed, retry_after = self._key_rate_limiter.allow(
                            str(pinned_key.id), pinned_key.rate_limit_per_min
                        )
                        if not allowed:
                            raise FcamError(
                                status_code=503,
                                code="ALL_KEYS_BUSY",
                                message="All keys busy or rate-limited",
                                retry_after=retry_after,
                                details={
                                    "client_id": client.id,
                                    "api_key_id": pinned_key.id,
                                    "base_url": self._config.firecrawl.base_url,
                                    "attempts": total_attempts,
                                    "method": method,
                                    "upstream_path": upstream_path,
                                },
                            )

                        lease = self._key_concurrency.try_acquire(str(pinned_key.id), pinned_key.max_concurrent)
                        if lease is None:
                            raise FcamError(
                                status_code=503,
                                code="ALL_KEYS_BUSY",
                                message="All keys busy or rate-limited",
                                details={
                                    "client_id": client.id,
                                    "api_key_id": pinned_key.id,
                                    "base_url": self._config.firecrawl.base_url,
                                    "attempts": total_attempts,
                                    "method": method,
                                    "upstream_path": upstream_path,
                                },
                            )

                        try:
                            try:
                                plaintext_api_key = decrypt_api_key(
                                    self._master_key, pinned_key.api_key_ciphertext
                                )
                            except (InvalidTag, ValueError):
                                self._disable_key_decrypt_failed(db, pinned_key)
                                raise FcamError(
                                    status_code=503,
                                    code="KEY_DECRYPT_FAILED",
                                    message="Failed to decrypt API key; check FCAM_MASTER_KEY",
                                ) from None

                            upstream_headers = dict(safe_headers)
                            upstream_headers.update(self._provider_auth_header(provider, plaintext_api_key))
                            auth_header_keys = {"authorization", "x-api-key"}
                            for hk in list(upstream_headers.keys()):
                                if hk.lower() in _DROP_REQUEST_HEADERS and hk.lower() not in auth_header_keys:
                                    upstream_headers.pop(hk, None)

                            resp = client_http.request(
                                method=method,
                                url=upstream_path,
                                headers=upstream_headers,
                                json=json_body,
                            )
                            last_upstream_status = resp.status_code
                            if attempt > 0:
                                retry_count = attempt

                        except httpx.TimeoutException as exc:
                            self._record_failure(db, pinned_key, reason="timeout")
                            if attempt >= total_attempts - 1:
                                logger.info(
                                    "upstream.timeout",
                                    extra={
                                        "fields": {
                                            "request_id": request_id,
                                            "api_key_id": pinned_key.id,
                                        }
                                    },
                                )
                                raise FcamError(
                                    status_code=504,
                                    code="UPSTREAM_TIMEOUT",
                                    message="Upstream timeout",
                                    details={
                                        "base_url": self._config.firecrawl.base_url,
                                        "timeout_s": self._config.firecrawl.timeout,
                                        "attempts": total_attempts,
                                        "method": method,
                                        "upstream_path": upstream_path,
                                    },
                                ) from exc
                            continue

                        except httpx.HTTPError as exc:
                            self._record_failure(db, pinned_key, reason="http_error")
                            if attempt >= total_attempts - 1:
                                logger.info(
                                    "upstream.unavailable",
                                    extra={
                                        "fields": {
                                            "request_id": request_id,
                                            "api_key_id": pinned_key.id,
                                        }
                                    },
                                )
                                raise FcamError(
                                    status_code=503,
                                    code="UPSTREAM_UNAVAILABLE",
                                    message="Upstream unavailable",
                                    details={
                                        "base_url": self._config.firecrawl.base_url,
                                        "attempts": total_attempts,
                                        "method": method,
                                        "upstream_path": upstream_path,
                                        "error": str(exc),
                                    },
                                ) from exc
                            continue
                        finally:
                            lease.release()

                        if resp.status_code == 429:
                            cooldown = _parse_retry_after(resp.headers) or self._config.rate_limit.cooldown_seconds
                            self._mark_cooling(db, pinned_key, cooldown)
                            return ForwardResult(
                                response=_to_fastapi_response(resp),
                                upstream_status_code=last_upstream_status,
                                api_key_id=last_api_key_id,
                                retry_count=retry_count,
                            )

                        if resp.status_code in {401, 403}:
                            self._disable_key(db, pinned_key, resp.status_code)
                            return ForwardResult(
                                response=_to_fastapi_response(resp),
                                upstream_status_code=last_upstream_status,
                                api_key_id=last_api_key_id,
                                retry_count=retry_count,
                            )

                        if resp.status_code >= 500:
                            self._record_failure(db, pinned_key, reason="upstream_5xx")
                            if attempt >= total_attempts - 1:
                                return ForwardResult(
                                    response=_to_fastapi_response(resp),
                                    upstream_status_code=last_upstream_status,
                                    api_key_id=last_api_key_id,
                                    retry_count=retry_count,
                                )
                            continue

                        credit_changed = False
                        if 200 <= resp.status_code < 300:
                            if (
                                self._config.credit_monitoring.enabled
                                and self._config.credit_monitoring.local_estimation.enabled
                                and self._config.credit_monitoring.local_estimation.sync_on_request
                                and method.upper() != "GET"
                            ):
                                try:
                                    from app.core.credit_estimator import (
                                        CREDIT_COST_MAP,
                                        estimate_credit_cost,
                                        normalize_endpoint,
                                    )

                                    endpoint = normalize_endpoint(upstream_path)
                                    if endpoint in CREDIT_COST_MAP and pinned_key.cached_remaining_credits is not None:
                                        response_data = None
                                        content_type = (resp.headers.get("content-type") or "").lower()
                                        if content_type.startswith("application/json"):
                                            try:
                                                response_data = resp.json()
                                            except Exception:
                                                response_data = None

                                        cost = estimate_credit_cost(endpoint, response_data)
                                        old_remaining = int(pinned_key.cached_remaining_credits)
                                        new_remaining = max(0, old_remaining - int(cost))
                                        if new_remaining != old_remaining:
                                            pinned_key.cached_remaining_credits = new_remaining
                                            credit_changed = True
                                except Exception:
                                    logger.exception(
                                        "credit.local_estimation_failed",
                                        extra={
                                            "fields": {
                                                "request_id": request_id,
                                                "api_key_id": pinned_key.id,
                                                "upstream_path": upstream_path,
                                                "method": method,
                                            }
                                        },
                                    )

                            if self._config.quota.count_mode == "success":
                                self._consume_quota_on_success(db, client, pinned_key)
                            elif credit_changed:
                                try:
                                    db.commit()
                                except Exception:
                                    db.rollback()
                                    logger.exception(
                                        "db.credit_local_update_commit_failed",
                                        extra={"fields": {"request_id": request_id, "api_key_id": pinned_key.id}},
                                    )

                        return ForwardResult(
                            response=_to_fastapi_response(resp),
                            upstream_status_code=last_upstream_status,
                            api_key_id=last_api_key_id,
                            retry_count=retry_count,
                        )

            max_selection_tries = max(total_attempts * 20, 50)
            upstream_attempts = 0
            selection_tries = 0
            decrypt_failed_seen = False

            while upstream_attempts < total_attempts and selection_tries < max_selection_tries:
                selection_tries += 1
                try:
                    selected = self._key_pool.select(db, self._config, client_id=client.id, provider=provider)
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
                    upstream_headers.update(self._provider_auth_header(provider, plaintext_api_key))

                    auth_header_keys = {"authorization", "x-api-key"}
                    for hk in list(upstream_headers.keys()):
                        if hk.lower() in _DROP_REQUEST_HEADERS and hk.lower() not in auth_header_keys:
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
                            details={
                                "base_url": self._config.firecrawl.base_url,
                                "timeout_s": self._config.firecrawl.timeout,
                                "attempts": total_attempts,
                                "method": method,
                                "upstream_path": upstream_path,
                            },
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
                            details={
                                "base_url": self._config.firecrawl.base_url,
                                "attempts": total_attempts,
                                "method": method,
                                "upstream_path": upstream_path,
                                "error": str(exc),
                            },
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

                credit_changed = False
                if 200 <= resp.status_code < 300:
                    if (
                        self._config.credit_monitoring.enabled
                        and self._config.credit_monitoring.local_estimation.enabled
                        and self._config.credit_monitoring.local_estimation.sync_on_request
                        and method.upper() != "GET"
                    ):
                        try:
                            from app.core.credit_estimator import (
                                CREDIT_COST_MAP,
                                estimate_credit_cost,
                                normalize_endpoint,
                            )

                            endpoint = normalize_endpoint(upstream_path)
                            if endpoint in CREDIT_COST_MAP and key.cached_remaining_credits is not None:
                                response_data = None
                                content_type = (resp.headers.get("content-type") or "").lower()
                                if content_type.startswith("application/json"):
                                    try:
                                        response_data = resp.json()
                                    except Exception:
                                        response_data = None

                                cost = estimate_credit_cost(endpoint, response_data)
                                old_remaining = int(key.cached_remaining_credits)
                                new_remaining = max(0, old_remaining - int(cost))
                                if new_remaining != old_remaining:
                                    key.cached_remaining_credits = new_remaining
                                    credit_changed = True
                        except Exception:
                            logger.exception(
                                "credit.local_estimation_failed",
                                extra={
                                    "fields": {
                                        "request_id": request_id,
                                        "api_key_id": key.id,
                                        "upstream_path": upstream_path,
                                        "method": method,
                                    }
                                },
                            )

                    if self._config.quota.count_mode == "success":
                        self._consume_quota_on_success(db, client, key)
                    elif credit_changed:
                        try:
                            db.commit()
                        except Exception:
                            db.rollback()
                            logger.exception(
                                "db.credit_local_update_commit_failed",
                                extra={"fields": {"request_id": request_id, "api_key_id": key.id}},
                            )

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
                details={
                    "client_id": client.id,
                    "base_url": self._config.firecrawl.base_url,
                    "attempts": total_attempts,
                    "method": method,
                    "upstream_path": upstream_path,
                },
            )
        raise FcamError(
            status_code=503,
            code="UPSTREAM_UNAVAILABLE",
            message="Upstream unavailable",
            details={
                "client_id": client.id,
                "base_url": self._config.firecrawl.base_url,
                "attempts": total_attempts,
                "method": method,
                "upstream_path": upstream_path,
                "last_upstream_status_code": last_upstream_status,
                "last_api_key_id": last_api_key_id,
            },
        )

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

        provider = getattr(key, "provider", "firecrawl") or "firecrawl"

        if provider == "exa":
            if mode != "scrape":
                raise FcamError(status_code=400, code="VALIDATION_ERROR", message="Unsupported test mode")
            return self._test_key_exa(db=db, request_id=request_id, key=key, provider=provider)

        if mode != "scrape":
            raise FcamError(status_code=400, code="VALIDATION_ERROR", message="Unsupported test mode")

        timeout = self._provider_timeout(provider)
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
            upstream_headers.update(self._provider_auth_header(provider, plaintext_api_key))

            with httpx.Client(
                timeout=timeout,
                base_url=self._provider_base_url(provider),
                transport=self._transport,
                follow_redirects=False,
            ) as client_http:
                candidates = ["/v2/scrape", "/v1/scrape"]
                if self._firecrawl_upstream_version == "v1":
                    candidates = ["/v1/scrape", "/v2/scrape"]
                elif self._firecrawl_upstream_version == "v2":
                    candidates = ["/v2/scrape", "/v1/scrape"]

                resp: httpx.Response | None = None
                for candidate_path in candidates:
                    resp = client_http.request(
                        method="POST",
                        url=candidate_path,
                        headers=upstream_headers,
                        json={"url": test_url},
                    )
                    if resp.status_code not in {404, 405}:
                        break
                assert resp is not None

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

    def _test_key_exa(
        self,
        *,
        db: Session,
        request_id: str,
        key: ApiKey,
        provider: str,
    ) -> KeyTestResult:
        """Test an Exa API key by calling POST /search with a simple query."""
        timeout = self._provider_timeout(provider)
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
            upstream_headers.update(self._provider_auth_header(provider, plaintext_api_key))

            with httpx.Client(
                timeout=timeout,
                base_url=self._provider_base_url(provider),
                transport=self._transport,
                follow_redirects=False,
            ) as client_http:
                resp = client_http.request(
                    method="POST",
                    url="/search",
                    headers=upstream_headers,
                    json={"query": "test", "numResults": 1},
                )

            latency_ms = int((perf_counter() - start) * 1000)

        except httpx.TimeoutException:
            self._record_failure(db, key, reason="timeout")
            latency_ms = int((perf_counter() - start) * 1000)
            return KeyTestResult(
                ok=False,
                upstream_status_code=None,
                latency_ms=latency_ms,
                observed_status=key.status,
                observed_cooldown_until=key.cooldown_until,
            )
        except httpx.HTTPError:
            self._record_failure(db, key, reason="http_error")
            latency_ms = int((perf_counter() - start) * 1000)
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

        if resp.status_code in {401, 403}:
            self._disable_key(db, key, resp.status_code)

        if resp.status_code >= 500:
            self._record_failure(db, key, reason="upstream_5xx")

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
        if not key.is_active or key.status == "disabled":
            return
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
        if not key.is_active or key.status == "disabled":
            return
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
