from __future__ import annotations

import logging
import math
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from redis.exceptions import WatchError

from app.errors import FcamError

logger = logging.getLogger(__name__)


@dataclass
class _State:
    current: int = 0
    maximum: int = 0


class Lease:
    def __init__(self, release_fn: Callable[[], None]):
        self._release_fn = release_fn
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._release_fn()

    def __enter__(self) -> "Lease":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


class ConcurrencyManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._states: dict[str, _State] = {}

    def try_acquire(self, key: str, maximum: int) -> Lease | None:
        if maximum <= 0:
            return Lease(lambda: None)
        with self._lock:
            state = self._states.get(key)
            if state is None:
                state = _State(current=0, maximum=maximum)
                self._states[key] = state
            state.maximum = maximum
            if state.current >= state.maximum:
                return None
            state.current += 1
            return Lease(lambda: self._release(key))

    def _release(self, key: str) -> None:
        with self._lock:
            state = self._states.get(key)
            if not state:
                return
            state.current = max(state.current - 1, 0)

    def current(self, key: str) -> int:
        with self._lock:
            state = self._states.get(key)
            return state.current if state else 0


_ACQUIRE_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])
local max = tonumber(ARGV[3])
local token = ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
local count = redis.call('ZCARD', key)
if count >= max then
  return 0
end
redis.call('ZADD', key, now + ttl, token)
redis.call('EXPIRE', key, math.max(1, math.ceil(ttl / 1000)))
return 1
"""

_CURRENT_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
return redis.call('ZCARD', key)
"""


class RedisConcurrencyManager:
    def __init__(
        self,
        *,
        client,
        key_prefix: str,
        scope: str,
        lease_ttl_ms: int,
    ) -> None:
        self._client = client
        self._key_prefix = (key_prefix or "fcam").strip(":")
        self._scope = scope.strip(":")
        self._lease_ttl_ms = max(int(lease_ttl_ms), 1000)

    def _redis_key(self, key: str) -> str:
        safe_key = key.strip(":") or "unknown"
        return f"{self._key_prefix}:concurrency:{self._scope}:{safe_key}"

    def try_acquire(self, key: str, maximum: int) -> Lease | None:
        if maximum <= 0:
            return Lease(lambda: None)

        token = uuid.uuid4().hex
        now_ms = int(time.time() * 1000)
        ttl_ms = self._lease_ttl_ms

        try:
            ok = self._client.eval(
                _ACQUIRE_LUA,
                1,
                self._redis_key(key),
                now_ms,
                ttl_ms,
                int(maximum),
                token,
            )
        except Exception as exc:
            msg = str(exc).lower()
            if "unknown command" in msg and "eval" in msg:
                ok = self._acquire_fallback(key=key, maximum=maximum, now_ms=now_ms, ttl_ms=ttl_ms, token=token)
            else:
                logger.exception("redis.concurrency_acquire_failed", extra={"fields": {"scope": self._scope}})
                raise FcamError(status_code=503, code="DB_UNAVAILABLE", message="State backend unavailable") from exc

        if int(ok or 0) != 1:
            return None

        def _release() -> None:
            try:
                self._client.zrem(self._redis_key(key), token)
            except Exception:
                logger.exception(
                    "redis.concurrency_release_failed",
                    extra={"fields": {"scope": self._scope}},
                )

        return Lease(_release)

    def current(self, key: str) -> int:
        now_ms = int(time.time() * 1000)
        try:
            n = self._client.eval(_CURRENT_LUA, 1, self._redis_key(key), now_ms)
            return int(n or 0)
        except Exception:
            try:
                redis_key = self._redis_key(key)
                self._client.zremrangebyscore(redis_key, "-inf", now_ms)
                return int(self._client.zcard(redis_key) or 0)
            except Exception:
                logger.exception("redis.concurrency_current_failed", extra={"fields": {"scope": self._scope}})
                return 0

    def _acquire_fallback(
        self,
        *,
        key: str,
        maximum: int,
        now_ms: int,
        ttl_ms: int,
        token: str,
    ) -> int:
        redis_key = self._redis_key(key)
        expire_s = max(1, int(math.ceil(ttl_ms / 1000)))

        pipe = self._client.pipeline()
        for _ in range(5):
            try:
                pipe.watch(redis_key)
                pipe.zremrangebyscore(redis_key, "-inf", now_ms)
                count = int(pipe.zcard(redis_key) or 0)
                if count >= int(maximum):
                    pipe.unwatch()
                    return 0
                pipe.multi()
                pipe.zadd(redis_key, {token: now_ms + int(ttl_ms)})
                pipe.expire(redis_key, expire_s)
                pipe.execute()
                return 1
            except WatchError:
                continue
            finally:
                try:
                    pipe.reset()
                except Exception:
                    pass

        raise FcamError(status_code=503, code="DB_UNAVAILABLE", message="State backend unavailable")
