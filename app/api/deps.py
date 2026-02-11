from __future__ import annotations

import logging
from collections.abc import Generator

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.config import AppConfig, Secrets
from app.core.concurrency import ConcurrencyManager
from app.core.rate_limit import TokenBucketRateLimiter
from app.core.security import constant_time_equals, derive_master_key_bytes, hmac_sha256_hex
from app.core.time import seconds_until_next_midnight, today_in_timezone
from app.db.models import Client
from app.errors import FcamError

logger = logging.getLogger(__name__)


def get_config(request: Request) -> AppConfig:
    return request.app.state.config


def get_secrets(request: Request) -> Secrets:
    return request.app.state.secrets


def get_db(request: Request) -> Generator[Session, None, None]:
    SessionLocal = request.app.state.db_session_factory
    session: Session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2:
        return None
    if parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def require_admin(request: Request, secrets: Secrets = Depends(get_secrets)) -> None:
    if not secrets.admin_token:
        raise FcamError(status_code=503, code="NOT_READY", message="Admin token not configured")

    token = _bearer_token(request.headers.get("authorization"))
    if not token or not constant_time_equals(token, secrets.admin_token):
        raise FcamError(status_code=401, code="ADMIN_UNAUTHORIZED", message="Missing or invalid admin token")


def require_client(
    request: Request,
    db: Session = Depends(get_db),
    secrets: Secrets = Depends(get_secrets),
) -> Client:
    if not secrets.master_key:
        raise FcamError(status_code=503, code="NOT_READY", message="Master key not configured")

    token = _bearer_token(request.headers.get("authorization"))
    if not token:
        raise FcamError(status_code=401, code="CLIENT_UNAUTHORIZED", message="Missing or invalid client token")

    master_key_bytes = derive_master_key_bytes(secrets.master_key)
    token_hash = hmac_sha256_hex(master_key_bytes, token)

    try:
        client = db.query(Client).filter(Client.token_hash == token_hash).one_or_none()
    except Exception as exc:
        logger.exception("db.client_lookup_failed", extra={"fields": {"op": "client_lookup"}})
        raise FcamError(status_code=503, code="DB_UNAVAILABLE", message="Database unavailable") from exc

    if client is None:
        raise FcamError(status_code=401, code="CLIENT_UNAUTHORIZED", message="Missing or invalid client token")
    if not client.is_active:
        raise FcamError(status_code=403, code="CLIENT_DISABLED", message="Client disabled")

    request.state.client_id = client.id
    return client


def enforce_client_governance(
    request: Request,
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
) -> Generator[None, None, None]:
    concurrency: ConcurrencyManager = request.app.state.client_concurrency
    rate_limiter: TokenBucketRateLimiter = request.app.state.client_rate_limiter

    lease = concurrency.try_acquire(str(client.id), client.max_concurrent)
    if lease is None:
        logger.info(
            "client.concurrency_limited",
            extra={"fields": {"client_id": client.id, "max_concurrent": client.max_concurrent}},
        )
        raise FcamError(
            status_code=429,
            code="CLIENT_CONCURRENCY_LIMITED",
            message="Client concurrency limited",
        )

    allowed, retry_after = rate_limiter.allow(str(client.id), client.rate_limit_per_min)
    if not allowed:
        logger.info(
            "client.rate_limited",
            extra={"fields": {"client_id": client.id, "rate_per_min": client.rate_limit_per_min}},
        )
        lease.release()
        raise FcamError(
            status_code=429,
            code="CLIENT_RATE_LIMITED",
            message="Client rate limited",
            retry_after=retry_after,
        )

    if config.quota.enable_quota_check and client.daily_quota is not None:
        try:
            today = today_in_timezone(config.quota.timezone)
            if client.quota_reset_at != today:
                client.daily_usage = 0
                client.quota_reset_at = today
                db.commit()

            if client.daily_usage >= client.daily_quota:
                retry_after_q = seconds_until_next_midnight(config.quota.timezone)
                logger.info(
                    "client.quota_exceeded",
                    extra={
                        "fields": {
                            "client_id": client.id,
                            "daily_quota": client.daily_quota,
                            "daily_usage": client.daily_usage,
                        }
                    },
                )
                lease.release()
                raise FcamError(
                    status_code=429,
                    code="CLIENT_QUOTA_EXCEEDED",
                    message="Client quota exceeded",
                    retry_after=retry_after_q,
                )

        except FcamError:
            raise
        except Exception as exc:
            logger.exception(
                "db.client_quota_check_failed",
                extra={"fields": {"client_id": client.id}},
            )
            lease.release()
            raise FcamError(status_code=503, code="DB_UNAVAILABLE", message="Database unavailable") from exc

    try:
        yield
    finally:
        lease.release()
