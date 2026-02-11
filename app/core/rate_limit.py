from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass

from app.errors import FcamError
from redis.exceptions import WatchError


@dataclass
class _Bucket:
    tokens: float
    last_refill: float


class TokenBucketRateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, _Bucket] = {}

    def allow(self, key: str, rate_per_min: int) -> tuple[bool, int]:
        if rate_per_min <= 0:
            return True, 0

        capacity = float(rate_per_min)
        refill_per_sec = capacity / 60.0
        now = time.monotonic()

        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(tokens=capacity, last_refill=now)
                self._buckets[key] = bucket

            elapsed = max(now - bucket.last_refill, 0.0)
            bucket.tokens = min(capacity, bucket.tokens + elapsed * refill_per_sec)
            bucket.last_refill = now

            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True, 0

            needed = 1.0 - bucket.tokens
            retry_after = int(math.ceil(needed / refill_per_sec))
            return False, max(retry_after, 1)


_ALLOW_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local refill_per_ms = tonumber(ARGV[3])
if capacity <= 0 then
  return {1, 0}
end
local data = redis.call('HMGET', key, 'tokens', 'last')
local tokens = tonumber(data[1])
local last = tonumber(data[2])
if tokens == nil then tokens = capacity end
if last == nil then last = now end
local elapsed = now - last
if elapsed < 0 then elapsed = 0 end
tokens = math.min(capacity, tokens + elapsed * refill_per_ms)
last = now
local allowed = 0
local retry_after = 0
if tokens >= 1.0 then
  tokens = tokens - 1.0
  allowed = 1
else
  local needed = 1.0 - tokens
  local ms_needed = needed / refill_per_ms
  retry_after = math.ceil(ms_needed / 1000)
  if retry_after < 1 then retry_after = 1 end
end
redis.call('HMSET', key, 'tokens', tokens, 'last', last)
redis.call('EXPIRE', key, 120)
return {allowed, retry_after}
"""


class RedisTokenBucketRateLimiter:
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

    def _redis_key(self, key: str) -> str:
        safe_key = key.strip(":") or "unknown"
        return f"{self._key_prefix}:rate_limit:{self._scope}:{safe_key}"

    def allow(self, key: str, rate_per_min: int) -> tuple[bool, int]:
        if rate_per_min <= 0:
            return True, 0

        now_ms = int(time.time() * 1000)
        capacity = float(rate_per_min)
        refill_per_ms = capacity / 60000.0

        try:
            allowed, retry_after = self._client.eval(
                _ALLOW_LUA,
                1,
                self._redis_key(key),
                now_ms,
                capacity,
                refill_per_ms,
            )
        except Exception as exc:
            msg = str(exc).lower()
            if "unknown command" in msg and "eval" in msg:
                allowed, retry_after = self._allow_fallback(
                    key=key, now_ms=now_ms, capacity=capacity, refill_per_ms=refill_per_ms
                )
            else:
                raise FcamError(status_code=503, code="DB_UNAVAILABLE", message="State backend unavailable") from exc

        return bool(int(allowed)), int(retry_after or 0)

    def _allow_fallback(
        self,
        *,
        key: str,
        now_ms: int,
        capacity: float,
        refill_per_ms: float,
    ) -> tuple[int, int]:
        redis_key = self._redis_key(key)
        pipe = self._client.pipeline()
        for _ in range(5):
            try:
                pipe.watch(redis_key)
                raw_tokens, raw_last = pipe.hmget(redis_key, "tokens", "last")
                tokens = float(raw_tokens) if raw_tokens is not None else capacity
                last = int(raw_last) if raw_last is not None else now_ms

                elapsed = max(now_ms - last, 0)
                tokens = min(capacity, tokens + elapsed * refill_per_ms)
                last = now_ms

                allowed = 0
                retry_after = 0
                if tokens >= 1.0:
                    tokens -= 1.0
                    allowed = 1
                else:
                    needed = 1.0 - tokens
                    ms_needed = needed / refill_per_ms
                    retry_after = int(math.ceil(ms_needed / 1000.0))
                    retry_after = max(retry_after, 1)

                pipe.multi()
                pipe.hset(redis_key, mapping={"tokens": f"{tokens:.17g}", "last": str(int(last))})
                pipe.expire(redis_key, 120)
                pipe.execute()
                return allowed, retry_after
            except WatchError:
                continue
            finally:
                try:
                    pipe.reset()
                except Exception:
                    pass

        raise FcamError(status_code=503, code="DB_UNAVAILABLE", message="State backend unavailable")
