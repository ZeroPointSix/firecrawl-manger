from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.credit_aggregator import aggregate_client_credits, get_key_credits

pytestmark = pytest.mark.unit


class _FakeKey:
    def __init__(
        self,
        key_id: int,
        client_id: int | None = None,
        cached_remaining: int | None = None,
        cached_plan: int | None = None,
        last_check: datetime | None = None,
        next_refresh: datetime | None = None,
        snapshot_id: int | None = None,
    ):
        self.id = key_id
        self.client_id = client_id
        self.cached_remaining_credits = cached_remaining
        self.cached_plan_credits = cached_plan
        self.last_credit_check_at = last_check
        self.next_refresh_at = next_refresh
        self.last_credit_snapshot_id = snapshot_id
        self.name = f"key-{key_id}"


class _FakeSnapshot:
    def __init__(
        self,
        snapshot_id: int,
        api_key_id: int,
        remaining: int,
        plan: int,
        fetch_success: bool = True,
        snapshot_at: datetime | None = None,
    ):
        self.id = snapshot_id
        self.api_key_id = api_key_id
        self.remaining_credits = remaining
        self.plan_credits = plan
        self.fetch_success = fetch_success
        self.snapshot_at = snapshot_at or datetime.now(timezone.utc)
        self.billing_period_start = None
        self.billing_period_end = None
        self.error_message = None


class _FakeClient:
    def __init__(self, client_id: int, name: str):
        self.id = client_id
        self.name = name


class _FakeQuery:
    def __init__(self, result=None):
        self._result = result or []
        self._filters = []
        self._order = None
        self._limit = None

    def filter(self, *args, **kwargs):
        self._filters.append((args, kwargs))
        return self

    def order_by(self, *args):
        self._order = args
        return self

    def first(self):
        return self._result[0] if self._result else None

    def one_or_none(self):
        return self._result[0] if self._result else None

    def all(self):
        return self._result


class _FakeDB:
    def __init__(self):
        self.queries = {}

    def query(self, model):
        return self.queries.get(model.__name__, _FakeQuery())


def test_get_key_credits_returns_none_when_key_not_found():
    db = _FakeDB()
    db.queries["ApiKey"] = _FakeQuery(result=[])

    with pytest.raises(ValueError, match="Key 999 not found"):
        get_key_credits(db, 999)


def test_get_key_credits_returns_cached_credits_without_snapshot():
    now = datetime.now(timezone.utc)
    key = _FakeKey(
        key_id=1,
        cached_remaining=450,
        cached_plan=0,
        last_check=now,
        next_refresh=now,
    )

    db = _FakeDB()
    db.queries["ApiKey"] = _FakeQuery(result=[key])
    db.queries["CreditSnapshot"] = _FakeQuery(result=[])

    result = get_key_credits(db, 1)

    assert result["api_key_id"] == 1
    assert result["cached_credits"]["remaining_credits"] == 450
    assert result["cached_credits"]["plan_credits"] == 0
    assert result["cached_credits"]["total_credits"] == 450  # 使用 cached 作为 total
    assert result["cached_credits"]["is_estimated"] is False
    assert result["latest_snapshot"] is None


def test_get_key_credits_calculates_total_from_first_snapshot():
    now = datetime.now(timezone.utc)
    key = _FakeKey(
        key_id=1,
        cached_remaining=450,
        cached_plan=0,
        last_check=now,
        snapshot_id=2,
    )

    # 第一条快照（初始额度 500）
    first_snapshot = _FakeSnapshot(snapshot_id=1, api_key_id=1, remaining=500, plan=0)
    # 最新快照（当前额度 450）
    latest_snapshot = _FakeSnapshot(snapshot_id=2, api_key_id=1, remaining=450, plan=0)

    db = _FakeDB()
    db.queries["ApiKey"] = _FakeQuery(result=[key])
    # 模拟两次查询：一次获取最新快照，一次获取第一条快照
    db.queries["CreditSnapshot"] = _FakeQuery(result=[latest_snapshot, first_snapshot])

    result = get_key_credits(db, 1)

    assert result["api_key_id"] == 1
    assert result["cached_credits"]["total_credits"] == 500  # 从第一条快照获取
    assert result["cached_credits"]["remaining_credits"] == 450
    assert result["cached_credits"]["is_estimated"] is False


def test_get_key_credits_marks_estimated_when_cached_differs_from_snapshot():
    now = datetime.now(timezone.utc)
    key = _FakeKey(
        key_id=1,
        cached_remaining=440,  # 本地估算值
        cached_plan=0,
        last_check=now,
        snapshot_id=1,
    )

    snapshot = _FakeSnapshot(snapshot_id=1, api_key_id=1, remaining=450, plan=0)  # 真实值

    db = _FakeDB()
    db.queries["ApiKey"] = _FakeQuery(result=[key])
    db.queries["CreditSnapshot"] = _FakeQuery(result=[snapshot])

    result = get_key_credits(db, 1)

    assert result["cached_credits"]["is_estimated"] is True  # 440 != 450


def test_aggregate_client_credits_raises_when_client_not_found():
    db = _FakeDB()
    db.queries["Client"] = _FakeQuery(result=[])

    with pytest.raises(ValueError, match="Client 999 not found"):
        aggregate_client_credits(db, 999)


def test_aggregate_client_credits_returns_zero_when_no_keys():
    client = _FakeClient(client_id=1, name="test-client")

    db = _FakeDB()
    db.queries["Client"] = _FakeQuery(result=[client])
    db.queries["ApiKey"] = _FakeQuery(result=[])

    result = aggregate_client_credits(db, 1)

    assert result["client_id"] == 1
    assert result["client_name"] == "test-client"
    assert result["total_remaining_credits"] == 0
    assert result["total_plan_credits"] == 0
    assert result["total_credits"] == 0
    assert result["usage_percentage"] == 0.0
    assert result["keys"] == []


def test_aggregate_client_credits_sums_multiple_keys():
    client = _FakeClient(client_id=1, name="test-client")

    key1 = _FakeKey(key_id=1, client_id=1, cached_remaining=400, cached_plan=0)
    key2 = _FakeKey(key_id=2, client_id=1, cached_remaining=300, cached_plan=0)

    # 第一条快照用于计算总额度
    snapshot1 = _FakeSnapshot(snapshot_id=1, api_key_id=1, remaining=0)
    snapshot2 = _FakeSnapshot(snapshot_id=2, api_key_id=2, remaining=500, plan=0)

    db = _FakeDB()
    db.queries["Client"] = _FakeQuery(result=[client])
    db.queries["ApiKey"] = _FakeQuery(result=[key1, key2])
    db.queries["CreditSnapshot"] = _FakeQuery(result=[snapshot1, snapshot2])

    result = aggregate_client_credits(db, 1)

    assert result["total_remaining_credits"] == 700  # 400 + 300
    assert result["total_credits"] == 1000  # 500 + 500
    assert result["usage_percentage"] == 30.0  # (1000 - 700) / 1000 * 100
    assert len(result["keys"]) == 2


def test_aggregate_client_credits_calculates_usage_percentage_correctly():
    client = _FakeClient(client_id=1, name="test-client")

    # 已使用 50%
    key1 = _FakeKey(key_id=1, client_id=1, cached_remaining=250, cached_plan=0)
    snapshot1 = _FakeSnapshot(snapshot_id=1, api_key_id=1, remaining=500, plan=0)

    db = _FakeDB()
    db.queries["Client"] = _FakeQuery(result=[client])
    db.queries["ApiKey"] = _FakeQuery(result=[key1])
    db.queries["CreditSnapshot"] = _FakeQuery(result=[snapshot1])

    result = aggregate_client_credits(db, 1)

    assert result["usage_percentage"] == 50.0  # (500 - 250) / 500 * 100


def test_aggregate_client_credits_handles_zero_total_credits():
    client = _FakeClient(client_id=1, name="test-client")

    key1 = _FakeKey(key_id=1, client_id=1, cached_remaining=0, cached_plan=0)

    db = _FakeDB()
    db.queries["Client"] = _FakeQuery(result=[client])
    db.queries["ApiKey"] = _FakeQuery(result=[key1])
    db.queries["CreditSnapshot"] = _FakeQuery(result=[])

    result = aggregate_client_credits(db, 1)

    assert result["usage_percentage"] == 0.0  # 避免除以零
