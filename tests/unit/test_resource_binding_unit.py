from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.resource_binding import should_use_sticky_binding

pytestmark = pytest.mark.unit


def test_should_use_sticky_binding_returns_false_for_non_resource_endpoints():
    assert should_use_sticky_binding("/v1/scrape") is False
    assert should_use_sticky_binding("/v1/map") is False
    assert should_use_sticky_binding("/v1/extract") is False
    assert should_use_sticky_binding("/v2/scrape") is False


def test_should_use_sticky_binding_returns_true_for_crawl_status():
    assert should_use_sticky_binding("/v1/crawl/abc123/status") is True
    assert should_use_sticky_binding("/v2/crawl/abc123") is True


def test_should_use_sticky_binding_returns_true_for_batch_scrape_status():
    assert should_use_sticky_binding("/v1/batch/scrape/abc123") is True


def test_should_use_sticky_binding_handles_trailing_slash():
    assert should_use_sticky_binding("/v1/crawl/abc123/status/") is True
    assert should_use_sticky_binding("/v2/crawl/abc123/") is True


def test_should_use_sticky_binding_handles_query_parameters():
    assert should_use_sticky_binding("/v1/crawl/abc123/status?foo=bar") is True


class _FakeBinding:
    def __init__(self, api_key_id: int, created_at: datetime | None = None):
        self.api_key_id = api_key_id
        self.created_at = created_at or datetime.now(timezone.utc)


class _FakeQuery:
    def __init__(self, result=None):
        self._result = result
        self._filters = []

    def filter(self, *args, **kwargs):
        self._filters.append((args, kwargs))
        return self

    def first(self):
        return self._result


class _FakeDB:
    def __init__(self):
        self.queries = {}
        self.added = []
        self.committed = False

    def query(self, model):
        return self.queries.get(model.__name__, _FakeQuery())

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.committed = True


def test_get_or_create_binding_returns_existing_binding():
    from app.core.resource_binding import get_or_create_binding
    from app.db.models import UpstreamResourceBinding

    existing = _FakeBinding(api_key_id=123)
    db = _FakeDB()
    db.queries["UpstreamResourceBinding"] = _FakeQuery(result=existing)

    result = get_or_create_binding(db, client_id=1, resource_id="abc123", api_key_id=123)

    assert result == 123
    assert len(db.added) == 0  # 没有创建新记录


def test_get_or_create_binding_creates_new_binding_when_not_exists():
    from app.core.resource_binding import get_or_create_binding

    db = _FakeDB()
    db.queries["UpstreamResourceBinding"] = _FakeQuery(result=None)

    result = get_or_create_binding(db, client_id=1, resource_id="abc123", api_key_id=456)

    assert result == 456
    assert len(db.added) == 1
    assert db.committed is True


def test_get_or_create_binding_handles_expired_binding():
    from app.core.resource_binding import get_or_create_binding

    # 创建一个过期的绑定（超过 24 小时）
    expired_time = datetime.now(timezone.utc) - timedelta(hours=25)
    expired_binding = _FakeBinding(api_key_id=123, created_at=expired_time)

    db = _FakeDB()
    db.queries["UpstreamResourceBinding"] = _FakeQuery(result=expired)

    result = get_or_create_binding(db, client_id=1, resource_id="abc123", api_key_id=456)

    # 应该创建新绑定，因为旧的已过期
    assert result == 456
    assert len(db.added) == 1


def test_extract_resource_id_from_crawl_status():
    from app.core.resource_binding import extract_resource_id

    resource_id = extract_resource_id("/v1/crawl/abc123/status")
    assert resource_id == "abc123"


def test_extract_resource_id_from_v2_crawl():
    from app.core.resource_binding import extract_resource_id

    resource_id = extract_resource_id("/v2/crawl/xyz789")
    assert resource_id == "xyz789"


def test_extract_resource_id_from_batch_scrape():
    from app.core.resource_binding import extract_resource_id

    resource_id = extract_resource_id("/v1/batch/scrape/batch456")
    assert resource_id == "batch456"


def test_extract_resource_id_returns_none_for_non_resource_endpoint():
    from app.core.resource_binding import extract_resource_id

    assert extract_resource_id("/v1/scrape") is None
    assert extract_resource_id("/v1/map") is None


def test_extract_resource_id_handles_trailing_slash():
    from app.core.resource_binding import extract_resource_id

    resource_id = extract_resource_id("/v1/crawl/abc123/status/")
    assert resource_id == "abc123"


def test_extract_resource_id_handles_query_parameters():
    from app.core.resource_binding import extract_resource_id

    resource_id = extract_resource_id("/v1/crawl/abc123/status?foo=bar")
    assert resource_id == "abc123"
