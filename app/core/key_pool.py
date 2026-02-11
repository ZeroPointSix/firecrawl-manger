from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone

from sqlalchemy.orm import Session

from app.config import AppConfig
from app.core.cooldown import NoopCooldownStore
from app.core.time import now_utc, seconds_until_next_midnight, today_in_timezone
from app.db.models import ApiKey
from app.errors import FcamError

logger = logging.getLogger(__name__)


@dataclass
class SelectedKey:
    api_key: ApiKey
    today: object
    now: datetime


def _is_disabled(key: ApiKey) -> bool:
    return (not key.is_active) or (key.status == "disabled")


def _cooldown_remaining_seconds(key: ApiKey, now: datetime) -> int:
    if not key.cooldown_until:
        return 0
    cooldown_until = (
        key.cooldown_until
        if key.cooldown_until.tzinfo is not None
        else key.cooldown_until.replace(tzinfo=timezone.utc)
    )
    remaining = int((cooldown_until - now).total_seconds())
    return max(remaining, 0)


class KeyPool:
    def __init__(self, *, cooldown_store: object | None = None) -> None:
        self._lock = threading.Lock()
        self._rr_index = 0
        self._cooldown_store = cooldown_store or NoopCooldownStore()

    def select(self, db: Session, config: AppConfig) -> SelectedKey:
        now = now_utc()
        today = today_in_timezone(config.quota.timezone)

        try:
            keys = db.query(ApiKey).order_by(ApiKey.id.asc()).all()
        except Exception as exc:
            logger.exception("db.keys_list_failed", extra={"fields": {"op": "keys_list"}})
            raise FcamError(status_code=503, code="DB_UNAVAILABLE", message="Database unavailable") from exc

        if not keys:
            raise FcamError(status_code=503, code="NO_KEY_CONFIGURED", message="No key configured")

        any_active = any(k.is_active for k in keys)
        if not any_active:
            raise FcamError(status_code=503, code="ALL_KEYS_DISABLED", message="All keys disabled")

        cooling_retry_after: int | None = None
        quota_retry_after: int | None = None
        cooling_seen = 0
        quota_seen = 0
        disabled_seen = 0

        with self._lock:
            start = self._rr_index % len(keys)

        for offset in range(len(keys)):
            idx = (start + offset) % len(keys)
            key = keys[idx]

            if _is_disabled(key):
                disabled_seen += 1
                continue

            if key.quota_reset_at != today:
                key.daily_usage = 0
                key.quota_reset_at = today
                if key.status == "quota_exceeded":
                    key.status = "active"

            if key.status in {"cooling", "failed"}:
                remaining = _cooldown_remaining_seconds(key, now)
                store_remaining = None
                if key.status == "cooling" and hasattr(self._cooldown_store, "remaining_seconds"):
                    store_remaining = self._cooldown_store.remaining_seconds(key_id=key.id)  # type: ignore[arg-type]
                if store_remaining is not None:
                    remaining = max(remaining, int(store_remaining))
                if remaining > 0:
                    cooling_seen += 1
                    cooling_retry_after = remaining if cooling_retry_after is None else min(
                        cooling_retry_after, remaining
                    )
                    continue
                key.cooldown_until = None
                key.status = "active"

            if key.daily_quota is not None and key.daily_usage >= key.daily_quota:
                key.status = "quota_exceeded"
                quota_seen += 1
                quota_retry_after = seconds_until_next_midnight(config.quota.timezone)
                continue

            try:
                db.commit()
            except Exception as exc:
                db.rollback()
                logger.exception(
                    "db.key_state_commit_failed",
                    extra={"fields": {"api_key_id": key.id}},
                )
                raise FcamError(status_code=503, code="DB_UNAVAILABLE", message="Database unavailable") from exc

            with self._lock:
                self._rr_index = idx + 1

            return SelectedKey(api_key=key, today=today, now=now)

        if disabled_seen == len(keys):
            raise FcamError(status_code=503, code="ALL_KEYS_DISABLED", message="All keys disabled")

        if quota_seen and quota_seen + disabled_seen == len(keys):
            raise FcamError(
                status_code=429,
                code="ALL_KEYS_QUOTA_EXCEEDED",
                message="All keys quota exceeded",
                retry_after=quota_retry_after,
            )

        if cooling_seen and cooling_seen + disabled_seen == len(keys):
            raise FcamError(
                status_code=429,
                code="ALL_KEYS_COOLING",
                message="All keys cooling",
                retry_after=cooling_retry_after,
            )

        raise FcamError(status_code=503, code="NO_KEY_CONFIGURED", message="No key available")
