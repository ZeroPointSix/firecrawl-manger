from __future__ import annotations

import logging

from app.errors import FcamError

logger = logging.getLogger(__name__)


class NoopCooldownStore:
    def set_cooldown(self, *, key_id: int, cooldown_seconds: int) -> None:
        return

    def remaining_seconds(self, *, key_id: int) -> int | None:
        return None


class RedisCooldownStore:
    def __init__(
        self,
        *,
        client,
        key_prefix: str,
        scope: str,
    ) -> None:
        self._client = client
        self._key_prefix = (key_prefix or "fcam").strip(":")
        self._scope = scope.strip(":")

    def _redis_key(self, key_id: int) -> str:
        return f"{self._key_prefix}:cooldown:{self._scope}:{int(key_id)}"

    def set_cooldown(self, *, key_id: int, cooldown_seconds: int) -> None:
        seconds = max(int(cooldown_seconds), 0)
        try:
            if seconds <= 0:
                self._client.delete(self._redis_key(key_id))
                return
            self._client.setex(self._redis_key(key_id), seconds, "1")
        except Exception as exc:
            logger.exception("redis.cooldown_set_failed", extra={"fields": {"key_id": int(key_id)}})
            raise FcamError(status_code=503, code="DB_UNAVAILABLE", message="State backend unavailable") from exc

    def remaining_seconds(self, *, key_id: int) -> int | None:
        try:
            ttl = int(self._client.ttl(self._redis_key(key_id)) or 0)
        except Exception as exc:
            logger.exception("redis.cooldown_ttl_failed", extra={"fields": {"key_id": int(key_id)}})
            raise FcamError(status_code=503, code="DB_UNAVAILABLE", message="State backend unavailable") from exc

        if ttl <= 0:
            return None
        return ttl
