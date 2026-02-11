from __future__ import annotations

import fakeredis

from app.core.concurrency import RedisConcurrencyManager
from app.core.cooldown import RedisCooldownStore
from app.core.rate_limit import RedisTokenBucketRateLimiter


def test_redis_concurrency_manager_try_acquire_and_release():
    r = fakeredis.FakeRedis(decode_responses=True)
    mgr = RedisConcurrencyManager(client=r, key_prefix="t", scope="client", lease_ttl_ms=5000)

    a1 = mgr.try_acquire("k", 2)
    assert a1 is not None
    a2 = mgr.try_acquire("k", 2)
    assert a2 is not None
    a3 = mgr.try_acquire("k", 2)
    assert a3 is None

    assert mgr.current("k") == 2
    a1.release()
    assert mgr.current("k") == 1
    a2.release()
    assert mgr.current("k") == 0


def test_redis_rate_limiter_denies_when_exceeded():
    r = fakeredis.FakeRedis(decode_responses=True)
    limiter = RedisTokenBucketRateLimiter(client=r, key_prefix="t", scope="client")

    ok1, _ = limiter.allow("c1", 1)
    ok2, retry_after = limiter.allow("c1", 1)

    assert ok1 is True
    assert ok2 is False
    assert retry_after >= 1


def test_redis_cooldown_store_sets_ttl():
    r = fakeredis.FakeRedis(decode_responses=True)
    store = RedisCooldownStore(client=r, key_prefix="t", scope="key")

    store.set_cooldown(key_id=123, cooldown_seconds=60)
    remaining = store.remaining_seconds(key_id=123)

    assert remaining is not None
    assert remaining > 0
