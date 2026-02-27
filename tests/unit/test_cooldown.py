from __future__ import annotations

import pytest

from app.core.cooldown import RedisCooldownStore
from app.errors import FcamError

pytestmark = pytest.mark.unit


class _FakeRedis:
    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.setex_calls: list[tuple[str, int, str]] = []
        self.ttl_value: int | None = None
        self.raise_on: dict[str, Exception] = {}

    def delete(self, key: str) -> None:
        exc = self.raise_on.get("delete")
        if exc:
            raise exc
        self.deleted.append(key)

    def setex(self, key: str, seconds: int, value: str) -> None:
        exc = self.raise_on.get("setex")
        if exc:
            raise exc
        self.setex_calls.append((key, int(seconds), str(value)))

    def ttl(self, key: str) -> int | None:
        exc = self.raise_on.get("ttl")
        if exc:
            raise exc
        return self.ttl_value


def test_redis_cooldown_store_set_cooldown_deletes_on_zero_or_negative():
    r = _FakeRedis()
    store = RedisCooldownStore(client=r, key_prefix=":fcam:", scope=":test:")

    store.set_cooldown(key_id=123, cooldown_seconds=0)
    store.set_cooldown(key_id=123, cooldown_seconds=-5)

    assert r.deleted == ["fcam:cooldown:test:123", "fcam:cooldown:test:123"]
    assert r.setex_calls == []


def test_redis_cooldown_store_set_cooldown_uses_setex_for_positive_seconds():
    r = _FakeRedis()
    store = RedisCooldownStore(client=r, key_prefix="fcam", scope="s")

    store.set_cooldown(key_id=7, cooldown_seconds=12)

    assert r.setex_calls == [("fcam:cooldown:s:7", 12, "1")]
    assert r.deleted == []


def test_redis_cooldown_store_set_cooldown_raises_when_backend_unavailable():
    r = _FakeRedis()
    r.raise_on["setex"] = RuntimeError("boom")
    store = RedisCooldownStore(client=r, key_prefix="fcam", scope="s")

    with pytest.raises(FcamError) as exc:
        store.set_cooldown(key_id=1, cooldown_seconds=3)

    assert exc.value.status_code == 503
    assert exc.value.code == "DB_UNAVAILABLE"


def test_redis_cooldown_store_remaining_seconds_returns_none_for_non_positive_ttl():
    r = _FakeRedis()
    store = RedisCooldownStore(client=r, key_prefix="fcam", scope="s")

    r.ttl_value = None
    assert store.remaining_seconds(key_id=1) is None

    r.ttl_value = 0
    assert store.remaining_seconds(key_id=1) is None

    r.ttl_value = -2
    assert store.remaining_seconds(key_id=1) is None


def test_redis_cooldown_store_remaining_seconds_raises_when_backend_unavailable():
    r = _FakeRedis()
    r.raise_on["ttl"] = RuntimeError("boom")
    store = RedisCooldownStore(client=r, key_prefix="fcam", scope="s")

    with pytest.raises(FcamError) as exc:
        store.remaining_seconds(key_id=1)

    assert exc.value.status_code == 503
    assert exc.value.code == "DB_UNAVAILABLE"

