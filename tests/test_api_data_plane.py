from __future__ import annotations

import base64
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig, Secrets
from app.core.concurrency import ConcurrencyManager
from app.core.forwarder import Forwarder
from app.core.key_pool import KeyPool
from app.core.security import derive_master_key_bytes, encrypt_api_key, hmac_sha256_hex
from app.core.time import today_in_timezone
from app.db.models import ApiKey, Base, Client, IdempotencyRecord, RequestLog
from app.main import create_app


_ENDPOINT_CASES = [
    ("POST", "/api/scrape", "/v1/scrape"),
    ("POST", "/api/crawl", "/v1/crawl"),
    ("GET", "/api/crawl/abc123", "/v1/crawl/abc123"),
    ("POST", "/api/search", "/v1/search"),
    ("POST", "/api/agent", "/v1/agent"),
]

_COMPAT_ENDPOINT_CASES = [
    ("POST", "/v1/scrape", "/v1/scrape"),
    ("POST", "/v1/crawl", "/v1/crawl"),
    ("GET", "/v1/crawl/abc123", "/v1/crawl/abc123"),
    ("POST", "/v1/search", "/v1/search"),
    ("POST", "/v1/agent", "/v1/agent"),
]


def _make_app(tmp_path, *, handler):
    config = AppConfig()
    config.database.path = (tmp_path / "api.db").as_posix()
    config.firecrawl.base_url = "http://firecrawl.test/v1"
    secrets = Secrets(admin_token="admin", master_key="master")
    app = create_app(config=config, secrets=secrets)
    Base.metadata.create_all(app.state.db_engine)

    transport = httpx.MockTransport(handler)
    app.state.forwarder = Forwarder(
        config=config,
        secrets=secrets,
        key_pool=KeyPool(),
        key_concurrency=ConcurrencyManager(),
        transport=transport,
    )

    SessionLocal = app.state.db_session_factory
    with SessionLocal() as db:
        token = "fcam_client_token"
        token_hash = hmac_sha256_hex(derive_master_key_bytes(secrets.master_key), token)
        c = Client(
            name="svc",
            token_hash=token_hash,
            is_active=True,
            daily_quota=10,
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
            api_key_ciphertext=encrypt_api_key(key_bytes, "fc-key-1"),
            api_key_hash="h1",
            api_key_last4="0001",
            is_active=True,
            status="active",
            daily_quota=100,
            daily_usage=0,
            quota_reset_at=today_in_timezone("UTC"),
            max_concurrent=10,
        )
        db.add(k)
        db.commit()
        db.refresh(c)
        db.refresh(k)

    return app, token


def test_api_requires_client_auth(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    app, _ = _make_app(tmp_path, handler=handler)
    with TestClient(app) as client:
        resp = client.post("/api/scrape", json={"url": "https://example.com"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "CLIENT_UNAUTHORIZED"
    assert "X-Request-Id" in resp.headers


def test_api_rate_limited(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    app, token = _make_app(tmp_path, handler=handler)
    SessionLocal = app.state.db_session_factory
    with SessionLocal() as db:
        c = db.query(Client).one()
        c.rate_limit_per_min = 1
        db.commit()

    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app) as client:
        r1 = client.post("/api/scrape", headers=headers, json={"url": "https://example.com"})
        r2 = client.post("/api/scrape", headers=headers, json={"url": "https://example.com"})

    assert r1.status_code == 200
    assert r2.status_code == 429
    assert r2.json()["error"]["code"] == "CLIENT_RATE_LIMITED"
    assert int(r2.headers.get("Retry-After", "0")) >= 1


def test_api_quota_exceeded(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    app, token = _make_app(tmp_path, handler=handler)
    SessionLocal = app.state.db_session_factory
    with SessionLocal() as db:
        c = db.query(Client).one()
        c.daily_quota = 1
        c.daily_usage = 1
        c.quota_reset_at = today_in_timezone("UTC")
        db.commit()

    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app) as client:
        resp = client.post("/api/scrape", headers=headers, json={"url": "https://example.com"})

    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "CLIENT_QUOTA_EXCEEDED"
    assert int(resp.headers.get("Retry-After", "0")) >= 1


def test_api_concurrency_limited(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        time.sleep(0.2)
        return httpx.Response(200, json={"ok": True})

    app, token = _make_app(tmp_path, handler=handler)
    SessionLocal = app.state.db_session_factory
    with SessionLocal() as db:
        c = db.query(Client).one()
        c.max_concurrent = 1
        db.commit()

    headers = {"Authorization": f"Bearer {token}"}

    def do_call():
        with TestClient(app) as client:
            return client.post("/api/scrape", headers=headers, json={"url": "https://example.com"}).status_code

    with ThreadPoolExecutor(max_workers=2) as ex:
        s1 = ex.submit(do_call)
        s2 = ex.submit(do_call)
        codes = sorted([s1.result(), s2.result()])

    assert codes == [200, 429]


@pytest.mark.parametrize(
    ("method", "path", "expected_upstream_path"),
    _ENDPOINT_CASES,
)
def test_api_endpoints_forward_to_expected_upstream(tmp_path, method: str, path: str, expected_upstream_path: str):
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(200, json={"ok": True})

    app, token = _make_app(tmp_path, handler=handler)
    headers = {"Authorization": f"Bearer {token}"}

    with TestClient(app) as client:
        if method == "GET":
            resp = client.get(path, headers=headers)
        else:
            resp = client.request(method, path, headers=headers, json={"url": "https://example.com"})

    assert resp.status_code == 200
    assert seen["method"] == method
    assert seen["path"] == expected_upstream_path


@pytest.mark.parametrize(
    ("method", "path", "expected_upstream_path"),
    _COMPAT_ENDPOINT_CASES,
)
def test_firecrawl_compat_endpoints_forward_to_expected_upstream(
    tmp_path, method: str, path: str, expected_upstream_path: str
):
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(200, json={"ok": True})

    app, token = _make_app(tmp_path, handler=handler)
    headers = {"Authorization": f"Bearer {token}"}

    with TestClient(app) as client:
        if method == "GET":
            resp = client.get(path, headers=headers)
        else:
            resp = client.request(method, path, headers=headers, json={"url": "https://example.com"})

    assert resp.status_code == 200
    assert seen["method"] == method
    assert seen["path"] == expected_upstream_path


@pytest.mark.parametrize(("method", "path", "expected_upstream_path"), _ENDPOINT_CASES)
def test_api_endpoints_passthrough_upstream_429(tmp_path, method: str, path: str, expected_upstream_path: str):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == expected_upstream_path
        return httpx.Response(429, headers={"Retry-After": "1"}, json={"error": "rate"})

    app, token = _make_app(tmp_path, handler=handler)
    app.state.config.firecrawl.max_retries = 0

    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app) as client:
        if method == "GET":
            resp = client.get(path, headers=headers)
        else:
            resp = client.request(method, path, headers=headers, json={"url": "https://example.com"})

    assert resp.status_code == 429
    assert resp.json() == {"error": "rate"}
    assert resp.headers.get("Retry-After") == "1"
    assert "X-Request-Id" in resp.headers


@pytest.mark.parametrize(("method", "path", "expected_upstream_path"), _ENDPOINT_CASES)
def test_api_endpoints_passthrough_upstream_5xx(tmp_path, method: str, path: str, expected_upstream_path: str):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == expected_upstream_path
        return httpx.Response(500, json={"error": "boom"})

    app, token = _make_app(tmp_path, handler=handler)
    app.state.config.firecrawl.max_retries = 0

    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app) as client:
        if method == "GET":
            resp = client.get(path, headers=headers)
        else:
            resp = client.request(method, path, headers=headers, json={"url": "https://example.com"})

    assert resp.status_code == 500
    assert resp.json() == {"error": "boom"}
    assert "X-Request-Id" in resp.headers


@pytest.mark.parametrize(("method", "path", "expected_upstream_path"), _ENDPOINT_CASES)
def test_api_endpoints_timeout_returns_gateway_error(tmp_path, method: str, path: str, expected_upstream_path: str):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == expected_upstream_path
        raise httpx.ReadTimeout("timeout", request=request)

    app, token = _make_app(tmp_path, handler=handler)
    app.state.config.firecrawl.max_retries = 0

    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app) as client:
        if method == "GET":
            resp = client.get(path, headers=headers)
        else:
            resp = client.request(method, path, headers=headers, json={"url": "https://example.com"})

    assert resp.status_code == 504
    assert resp.json()["error"]["code"] == "UPSTREAM_TIMEOUT"
    assert "X-Request-Id" in resp.headers


def test_api_writes_request_log_on_success(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    app, token = _make_app(tmp_path, handler=handler)
    headers = {"Authorization": f"Bearer {token}"}

    with TestClient(app) as client:
        resp = client.post("/api/scrape", headers=headers, json={"url": "https://example.com"})

    assert resp.status_code == 200
    request_id = resp.headers.get("X-Request-Id")
    assert request_id

    SessionLocal = app.state.db_session_factory
    with SessionLocal() as db:
        logs = db.query(RequestLog).order_by(RequestLog.id.asc()).all()
        assert len(logs) == 1
        log = logs[0]
        assert log.request_id == request_id
        assert log.client_id is not None
        assert log.api_key_id is not None
        assert log.endpoint == "scrape"
        assert log.method == "POST"
        assert log.status_code == 200
        assert log.success is True
        assert log.retry_count == 0
        assert log.error_message is None
        assert log.response_time_ms is not None and log.response_time_ms >= 0


def test_api_writes_request_log_on_rejection(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    app, _ = _make_app(tmp_path, handler=handler)
    with TestClient(app) as client:
        resp = client.post("/api/scrape", json={"url": "https://example.com"})

    assert resp.status_code == 401
    request_id = resp.headers.get("X-Request-Id")
    assert request_id

    SessionLocal = app.state.db_session_factory
    with SessionLocal() as db:
        logs = db.query(RequestLog).order_by(RequestLog.id.asc()).all()
        assert len(logs) == 1
        log = logs[0]
        assert log.request_id == request_id
        assert log.client_id is None
        assert log.api_key_id is None
        assert log.endpoint == "scrape"
        assert log.method == "POST"
        assert log.status_code == 401
        assert log.success is False
        assert log.retry_count == 0
        assert log.error_message == "CLIENT_UNAUTHORIZED"


def test_api_request_log_includes_retry_count(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization")
        if auth == "Bearer fc-key-1":
            return httpx.Response(429, headers={"Retry-After": "1"}, json={"error": "rate"})
        return httpx.Response(200, json={"ok": True})

    app, token = _make_app(tmp_path, handler=handler)

    SessionLocal = app.state.db_session_factory
    with SessionLocal() as db:
        c = db.query(Client).one()
        key_bytes = derive_master_key_bytes(app.state.secrets.master_key)
        k2 = ApiKey(
            client_id=c.id,
            api_key_ciphertext=encrypt_api_key(key_bytes, "fc-key-2"),
            api_key_hash="h2",
            api_key_last4="0002",
            is_active=True,
            status="active",
            daily_quota=100,
            daily_usage=0,
            quota_reset_at=today_in_timezone("UTC"),
            max_concurrent=10,
        )
        db.add(k2)
        db.commit()
        db.refresh(k2)
        key2_id = k2.id

    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app) as client:
        resp = client.post("/api/scrape", headers=headers, json={"url": "https://example.com"})

    assert resp.status_code == 200

    with SessionLocal() as db:
        logs = db.query(RequestLog).order_by(RequestLog.id.asc()).all()
        assert len(logs) == 1
        log = logs[0]
        assert log.endpoint == "scrape"
        assert log.status_code == 200
        assert log.success is True
        assert log.api_key_id == key2_id
        assert log.retry_count == 1


def test_api_idempotency_replays_response_without_upstream_call(tmp_path):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"task_id": "t1"})

    app, token = _make_app(tmp_path, handler=handler)
    headers = {"Authorization": f"Bearer {token}", "X-Idempotency-Key": "idem-1"}

    with TestClient(app) as client:
        r1 = client.post("/api/crawl", headers=headers, json={"url": "https://example.com"})
        r2 = client.post("/api/crawl", headers=headers, json={"url": "https://example.com"})

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json() == {"task_id": "t1"}
    assert r2.json() == {"task_id": "t1"}
    assert calls["n"] == 1

    SessionLocal = app.state.db_session_factory
    with SessionLocal() as db:
        logs = db.query(RequestLog).filter(RequestLog.endpoint == "crawl").all()
        assert len(logs) == 2
        records = db.query(IdempotencyRecord).all()
        assert len(records) == 1
        assert records[0].status == "completed"


def test_api_idempotency_conflict_returns_409(tmp_path):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"ok": True})

    app, token = _make_app(tmp_path, handler=handler)
    headers = {"Authorization": f"Bearer {token}", "X-Idempotency-Key": "idem-2"}

    with TestClient(app) as client:
        r1 = client.post("/api/agent", headers=headers, json={"prompt": "a"})
        r2 = client.post("/api/agent", headers=headers, json={"prompt": "b"})

    assert r1.status_code == 200
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"
    assert calls["n"] == 1


def test_api_idempotency_in_progress_returns_409(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    app, token = _make_app(tmp_path, handler=handler)
    SessionLocal = app.state.db_session_factory

    payload = {"url": "https://example.com"}
    request_hash = hashlib.sha256(
        json.dumps(
            {"method": "POST", "endpoint": "crawl", "payload": payload},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()

    with SessionLocal() as db:
        client_id = db.query(Client).one().id
        db.add(
            IdempotencyRecord(
                client_id=client_id,
                idempotency_key="idem-3",
                request_hash=request_hash,
                status="in_progress",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        )
        db.commit()

    headers = {"Authorization": f"Bearer {token}", "X-Idempotency-Key": "idem-3"}
    with TestClient(app) as client:
        resp = client.post("/api/crawl", headers=headers, json=payload)

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "IDEMPOTENCY_IN_PROGRESS"
    assert int(resp.headers.get("Retry-After", "0")) >= 1


def test_api_idempotency_required_when_configured(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    app, token = _make_app(tmp_path, handler=handler)
    app.state.config.idempotency.require_on = ["crawl"]

    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app) as client:
        resp = client.post("/api/crawl", headers=headers, json={"url": "https://example.com"})

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"


def test_api_idempotency_expired_record_is_cleaned_and_recreated(tmp_path):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"task_id": "new"})

    app, token = _make_app(tmp_path, handler=handler)
    SessionLocal = app.state.db_session_factory

    payload = {"url": "https://example.com"}
    request_hash = hashlib.sha256(
        json.dumps(
            {"method": "POST", "endpoint": "crawl", "payload": payload},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()

    old_body = json.dumps(
        {
            "v": 1,
            "headers": {"content-type": "application/json"},
            "body_b64": base64.b64encode(b'{"task_id":"old"}').decode("ascii"),
        },
        ensure_ascii=False,
    )

    with SessionLocal() as db:
        client_id = db.query(Client).one().id
        db.add(
            IdempotencyRecord(
                client_id=client_id,
                idempotency_key="idem-expired",
                request_hash=request_hash,
                status="completed",
                response_status_code=200,
                response_body=old_body,
                expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            )
        )
        db.commit()

    headers = {"Authorization": f"Bearer {token}", "X-Idempotency-Key": "idem-expired"}
    with TestClient(app) as client:
        r1 = client.post("/api/crawl", headers=headers, json=payload)
        r2 = client.post("/api/crawl", headers=headers, json=payload)

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json() == {"task_id": "new"}
    assert r2.json() == {"task_id": "new"}
    assert calls["n"] == 1


def test_api_no_key_configured_returns_503(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    app, token = _make_app(tmp_path, handler=handler)
    SessionLocal = app.state.db_session_factory
    with SessionLocal() as db:
        db.query(ApiKey).delete()
        db.commit()

    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app) as client:
        resp = client.post("/api/scrape", headers=headers, json={"url": "https://example.com"})

    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "NO_KEY_CONFIGURED"


def test_api_all_keys_cooling_returns_429(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    app, token = _make_app(tmp_path, handler=handler)
    SessionLocal = app.state.db_session_factory
    with SessionLocal() as db:
        k = db.query(ApiKey).one()
        k.status = "cooling"
        k.cooldown_until = datetime.now(timezone.utc) + timedelta(seconds=60)
        db.commit()

    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app) as client:
        resp = client.post("/api/scrape", headers=headers, json={"url": "https://example.com"})

    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "ALL_KEYS_COOLING"
    assert int(resp.headers.get("Retry-After", "0")) >= 1


def test_api_all_keys_quota_exceeded_returns_429(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    app, token = _make_app(tmp_path, handler=handler)
    SessionLocal = app.state.db_session_factory
    with SessionLocal() as db:
        k = db.query(ApiKey).one()
        k.daily_quota = 1
        k.daily_usage = 1
        k.quota_reset_at = today_in_timezone("UTC")
        db.commit()

    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app) as client:
        resp = client.post("/api/scrape", headers=headers, json={"url": "https://example.com"})

    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "ALL_KEYS_QUOTA_EXCEEDED"
    assert int(resp.headers.get("Retry-After", "0")) >= 1


def test_api_client_quota_resets_lazily(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    app, token = _make_app(tmp_path, handler=handler)
    SessionLocal = app.state.db_session_factory
    today = today_in_timezone("UTC")

    with SessionLocal() as db:
        c = db.query(Client).one()
        c.daily_quota = 1
        c.daily_usage = 1
        c.quota_reset_at = today - timedelta(days=1)
        db.commit()

    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app) as client:
        resp = client.post("/api/scrape", headers=headers, json={"url": "https://example.com"})

    assert resp.status_code == 200

    with SessionLocal() as db:
        c = db.query(Client).one()
        assert c.daily_usage == 1
        assert c.quota_reset_at == today


def test_api_db_unavailable_returns_503(tmp_path):
    config = AppConfig()
    config.database.path = (tmp_path / "api.db").as_posix()
    config.firecrawl.base_url = "http://firecrawl.test/v1"
    secrets = Secrets(admin_token="admin", master_key="master")
    app = create_app(config=config, secrets=secrets)

    with TestClient(app) as client:
        resp = client.post(
            "/api/scrape",
            headers={"Authorization": "Bearer fcam_client_token"},
            json={"url": "https://example.com"},
        )

    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "DB_UNAVAILABLE"
