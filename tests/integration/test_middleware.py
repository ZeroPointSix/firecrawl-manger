from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import AppConfig, Secrets
from app.db.models import Base, RequestLog
from app.main import create_app

pytestmark = pytest.mark.integration


def _make_app(
    tmp_path,
    *,
    max_body_bytes: int = 1024,
    allowed_paths: set[str] | None = None,
    scrape_handler=None,
) -> FastAPI:
    config = AppConfig()
    config.database.path = (tmp_path / "test.db").as_posix()
    config.security.request_limits.max_body_bytes = max_body_bytes
    config.security.request_limits.allowed_paths = sorted(allowed_paths or {"scrape"})
    secrets = Secrets(admin_token="admin", master_key="master")
    app = create_app(config=config, secrets=secrets)
    Base.metadata.create_all(app.state.db_engine)

    @app.post("/api/scrape")
    def _scrape():
        if scrape_handler is not None:
            return scrape_handler()
        return {"ok": True}

    return app


def test_request_id_header_is_set(tmp_path):
    app = _make_app(tmp_path)
    client = TestClient(app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert "X-Request-Id" in resp.headers
    assert len(resp.headers["X-Request-Id"]) >= 8


def test_request_id_header_is_preserved_when_valid(tmp_path):
    app = _make_app(tmp_path)
    client = TestClient(app)
    resp = client.get("/healthz", headers={"X-Request-Id": "req_12345678"})
    assert resp.headers["X-Request-Id"] == "req_12345678"


def test_request_id_header_is_replaced_when_invalid(tmp_path):
    app = _make_app(tmp_path)
    client = TestClient(app)
    resp = client.get("/healthz", headers={"X-Request-Id": "bad"})
    assert resp.status_code == 200
    assert resp.headers["X-Request-Id"] != "bad"


def test_request_limits_rejects_unsupported_content_type(tmp_path):
    app = _make_app(tmp_path)
    client = TestClient(app)
    resp = client.post("/api/scrape", data="x", headers={"Content-Type": "text/plain"})
    assert resp.status_code == 415
    body = resp.json()
    assert body["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"
    assert "X-Request-Id" in resp.headers

    SessionLocal = app.state.db_session_factory
    with SessionLocal() as db:
        log = db.query(RequestLog).order_by(RequestLog.id.desc()).first()
        assert log is not None
        assert log.endpoint == "scrape"
        assert log.status_code == 415
        assert log.error_message == "UNSUPPORTED_MEDIA_TYPE"
        assert log.error_details is not None


def test_request_limits_rejects_large_body(tmp_path):
    app = _make_app(tmp_path, max_body_bytes=10)
    client = TestClient(app)
    resp = client.post("/api/scrape", json={"k": "01234567890"})
    assert resp.status_code == 413
    body = resp.json()
    assert body["error"]["code"] == "REQUEST_TOO_LARGE"
    assert body["error"]["details"]["max_body_bytes"] == 10
    assert "X-Request-Id" in resp.headers


def test_path_whitelist_blocks_unknown_api_paths(tmp_path):
    app = _make_app(tmp_path, allowed_paths={"scrape"})
    client = TestClient(app)
    resp = client.post("/api/evil", json={"x": 1})
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "PATH_NOT_ALLOWED"


def test_path_whitelist_blocks_unknown_v1_paths(tmp_path):
    app = _make_app(tmp_path, allowed_paths={"scrape"})
    client = TestClient(app)
    resp = client.post("/v1/evil", json={"x": 1})
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "PATH_NOT_ALLOWED"


def test_path_whitelist_blocks_unknown_v2_paths(tmp_path):
    app = _make_app(tmp_path, allowed_paths={"scrape"})
    client = TestClient(app)
    resp = client.post("/v2/evil", json={"x": 1})
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "PATH_NOT_ALLOWED"


def test_path_whitelist_allows_known_v2_paths(tmp_path):
    app = _make_app(tmp_path, allowed_paths={"map"})

    client = TestClient(app)
    resp = client.post("/v2/map", json={"url": "https://example.com"})
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"]["code"] == "CLIENT_UNAUTHORIZED"


def test_request_log_captures_error_details_from_upstream_response(tmp_path):
    from starlette.responses import Response

    big = "x" * 9000
    body = json.dumps({"error": {"code": "BAD", "message": big}}, ensure_ascii=False).encode("utf-8")

    app = _make_app(tmp_path, allowed_paths={"scrape_error"})

    @app.post("/api/scrape_error")
    def _scrape_error():
        return Response(content=body, status_code=400, media_type="application/json")

    client = TestClient(app)

    resp = client.post("/api/scrape_error", json={"url": "https://example.com"})
    assert resp.status_code == 400
    assert resp.content == body

    SessionLocal = app.state.db_session_factory
    with SessionLocal() as db:
        log = db.query(RequestLog).order_by(RequestLog.id.desc()).first()
        assert log is not None
        assert log.endpoint == "scrape_error"
        assert log.status_code == 400
        assert log.error_message == "UPSTREAM_HTTP_ERROR"
        assert log.error_details is not None
        assert '"preview"' in log.error_details
        assert "BAD" in log.error_details
