"""
Integration tests for Firecrawl API v2 missing endpoints.

这些端点此前缺失/仅能被 wildcard 捕获时，会带来两个问题：
1) 用户调用会 404（不可用）
2) 即使可转发，也会导致 request_logs 的 endpoint 退化为推断值（如 "team"），排障与审计困难

因此本文件重点验证：
1) 端点存在且需要鉴权（无鉴权返回 401）
2) 端点转发到上游（MockTransport）且保持路径/查询参数
3) request_logs.endpoint 写入显式标识（而不是 middleware 推断）

Reference:
- PRD: docs/PRD/2026-02-25-firecrawl-v2-missing-endpoints.md
- FD: docs/FD/2026-02-25-firecrawl-v2-missing-endpoints-fd.md
- TDD: docs/TDD/2026-02-25-firecrawl-v2-missing-endpoints-tdd.md
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig, Secrets
from app.core.concurrency import ConcurrencyManager
from app.core.forwarder import Forwarder
from app.core.key_pool import KeyPool
from app.core.security import derive_master_key_bytes, encrypt_api_key, hmac_sha256_hex
from app.core.time import today_in_timezone
from app.db.models import ApiKey, Base, Client, RequestLog
from app.main import create_app

pytestmark = pytest.mark.integration

# ============================================================================
# Helpers
# ============================================================================


def _get_request_log(client: TestClient, request_id: str) -> RequestLog:
    SessionLocal = client.app.state.db_session_factory
    with SessionLocal() as db:
        log = db.query(RequestLog).filter(RequestLog.request_id == request_id).one_or_none()
        assert log is not None
        return log

_ENDPOINT_CASES: list[tuple[str, str, Any | None, str, str]] = [
    ("POST", "/v2/scrape?timeout=30000&waitFor=5000", {"url": "https://example.com"}, "scrape", "timeout=30000&waitFor=5000"),
    ("POST", "/v2/search", {"query": "test"}, "search", ""),
    ("POST", "/v2/map", {"url": "https://example.com"}, "map", ""),
    ("GET", "/v2/team/credit-usage", None, "team_credit_usage", ""),
    ("GET", "/v2/team/queue-status", None, "team_queue_status", ""),
    ("GET", "/v2/team/credit-usage/historical", None, "team_credit_usage_historical", ""),
    ("GET", "/v2/team/token-usage", None, "team_token_usage", ""),
    ("GET", "/v2/team/token-usage/historical", None, "team_token_usage_historical", ""),
    ("GET", "/v2/crawl/active", None, "crawl_active", ""),
    ("POST", "/v2/crawl/params-preview", {"prompt": "test"}, "crawl_params_preview", ""),
]


def _call(client: TestClient, *, method: str, path: str, payload: Any | None, headers: dict[str, str] | None = None):
    if method == "GET":
        return client.get(path, headers=headers)
    if method == "POST":
        return client.post(path, json=payload, headers=headers)
    raise AssertionError(f"Unexpected method: {method}")


def test_missing_endpoints_require_auth(client: TestClient):
    for method, path, payload, _endpoint, _expected_query in _ENDPOINT_CASES:
        resp = _call(client, method=method, path=path, payload=payload, headers=None)
        assert resp.status_code == 401, (method, path, resp.text)


def test_missing_endpoints_forward_and_persist_endpoint_to_request_log(
    client: TestClient,
    client_headers: dict[str, str],
):
    for method, path, payload, expected_endpoint, expected_query in _ENDPOINT_CASES:
        resp = _call(client, method=method, path=path, payload=payload, headers=client_headers)
        assert resp.status_code == 200, (method, path, resp.text)
        request_id = resp.headers.get("X-Request-Id")
        assert request_id

        body = resp.json()
        assert body["ok"] is True
        assert body["method"] == method
        assert body["path"] == path.split("?", 1)[0]
        assert body["query"] == expected_query

        log = _get_request_log(client, request_id)
        assert log.endpoint == expected_endpoint


def test_invalid_token_returns_401(client: TestClient):
    response = client.post(
        "/v2/scrape",
        json={"url": "https://example.com"},
        headers={"Authorization": "Bearer invalid_token_12345"},
    )
    assert response.status_code == 401, "Invalid token should return 401"


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def app(tmp_path):
    """Create and configure FastAPI app for testing."""
    config = AppConfig()
    config.database.path = (tmp_path / "test.db").as_posix()
    config.firecrawl.base_url = "http://firecrawl.test"
    config.security.request_limits.allowed_paths = sorted(
        {*(config.security.request_limits.allowed_paths or []), "map", "extract", "batch", "team", "crawl"}
    )

    secrets = Secrets(admin_token="admin", master_key="master")
    app = create_app(config=config, secrets=secrets)
    Base.metadata.create_all(app.state.db_engine)

    # Mock upstream API
    def handler(request: httpx.Request) -> httpx.Response:
        raw_query = request.url.query or b""
        query = raw_query.decode("utf-8") if isinstance(raw_query, (bytes, bytearray)) else str(raw_query)
        return httpx.Response(
            200,
            json={
                "ok": True,
                "method": request.method.upper(),
                "path": request.url.path,
                "query": query,
            },
        )

    app.state.forwarder = Forwarder(
        config=config,
        secrets=secrets,
        key_pool=KeyPool(),
        key_concurrency=ConcurrencyManager(),
        transport=httpx.MockTransport(handler),
    )

    # Create test client and API key
    SessionLocal = app.state.db_session_factory
    with SessionLocal() as db:
        token = "test_client_token"
        token_hash = hmac_sha256_hex(derive_master_key_bytes(secrets.master_key), token)
        c = Client(
            name="test-client",
            token_hash=token_hash,
            is_active=True,
            daily_quota=1000,
            daily_usage=0,
            quota_reset_at=today_in_timezone("UTC"),
            rate_limit_per_min=60,
            max_concurrent=10,
        )
        db.add(c)
        db.flush()

        key_bytes = derive_master_key_bytes(secrets.master_key)
        k = ApiKey(
            client_id=c.id,
            api_key_ciphertext=encrypt_api_key(key_bytes, "fc-test-key"),
            api_key_hash="test_hash",
            api_key_last4="test",
            is_active=True,
            status="active",
            daily_quota=1000,
            daily_usage=0,
            quota_reset_at=today_in_timezone("UTC"),
            max_concurrent=10,
            rate_limit_per_min=10_000,
        )
        db.add(k)
        db.commit()

    return app, token


@pytest.fixture
def test_client_token(app) -> str:
    """Return test client token."""
    _, token = app
    return token


@pytest.fixture
def client(app) -> TestClient:
    """Return FastAPI TestClient."""
    app_instance, _ = app
    with TestClient(app_instance) as http:
        yield http


@pytest.fixture
def client_headers(test_client_token: str) -> dict[str, str]:
    """Return headers with valid client token for testing."""
    return {
        "Authorization": f"Bearer {test_client_token}",
        "Content-Type": "application/json",
    }
