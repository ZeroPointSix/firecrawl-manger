from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.db.models import RequestLog

pytestmark = pytest.mark.integration


def _make_app(
    tmp_path,
    *,
    make_app,
    max_body_bytes: int = 1024,
    allowed_paths: set[str] | None = None,
):
    def mutate(config):  # noqa: ANN001
        config.security.request_limits.max_body_bytes = max_body_bytes
        config.security.request_limits.allowed_paths = sorted(allowed_paths or {"scrape"})

    app, _, _ = make_app(tmp_path, config_mutate=mutate)
    return app


def test_request_id_header_is_set(tmp_path, make_app):
    app = _make_app(tmp_path, make_app=make_app)
    with TestClient(app) as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200
    assert "X-Request-Id" in resp.headers
    assert len(resp.headers["X-Request-Id"]) >= 8


def test_request_id_header_is_preserved_when_valid(tmp_path, make_app):
    app = _make_app(tmp_path, make_app=make_app)
    with TestClient(app) as client:
        resp = client.get("/healthz", headers={"X-Request-Id": "req_12345678"})
    assert resp.headers["X-Request-Id"] == "req_12345678"


def test_request_id_header_is_replaced_when_invalid(tmp_path, make_app):
    app = _make_app(tmp_path, make_app=make_app)
    with TestClient(app) as client:
        resp = client.get("/healthz", headers={"X-Request-Id": "bad"})
    assert resp.status_code == 200
    assert resp.headers["X-Request-Id"] != "bad"


def test_request_limits_rejects_unsupported_content_type(tmp_path, make_app):
    app = _make_app(tmp_path, make_app=make_app)
    with TestClient(app) as client:
        resp = client.post("/api/scrape", content="x", headers={"Content-Type": "text/plain"})
    assert resp.status_code == 415
    body = resp.json()
    assert body["success"] is False
    assert body["error"] == "Only application/json is supported"
    assert "X-Request-Id" in resp.headers

    SessionLocal = app.state.db_session_factory
    with SessionLocal() as db:
        log = db.query(RequestLog).order_by(RequestLog.id.desc()).first()
        assert log is not None
        assert log.endpoint == "scrape"
        assert log.status_code == 415
        assert log.error_message == "UNSUPPORTED_MEDIA_TYPE"
        assert log.error_details is not None


def test_request_limits_rejects_large_body(tmp_path, make_app):
    app = _make_app(tmp_path, make_app=make_app, max_body_bytes=10)
    with TestClient(app) as client:
        resp = client.post("/api/scrape", json={"k": "01234567890"})
    assert resp.status_code == 413
    body = resp.json()
    assert body["success"] is False
    assert body["error"] == "Request body too large"
    assert "X-Request-Id" in resp.headers


def test_path_whitelist_blocks_unknown_api_paths(tmp_path, make_app):
    app = _make_app(tmp_path, make_app=make_app, allowed_paths={"scrape"})
    with TestClient(app) as client:
        resp = client.post("/api/evil", json={"x": 1})
    assert resp.status_code == 404
    body = resp.json()
    assert body["success"] is False
    assert body["error"] == "Path not allowed"


def test_path_whitelist_blocks_unknown_v1_paths(tmp_path, make_app):
    app = _make_app(tmp_path, make_app=make_app, allowed_paths={"scrape"})
    with TestClient(app) as client:
        resp = client.post("/v1/evil", json={"x": 1})
    assert resp.status_code == 404
    body = resp.json()
    assert body["success"] is False
    assert body["error"] == "Path not allowed"


def test_path_whitelist_blocks_unknown_v2_paths(tmp_path, make_app):
    app = _make_app(tmp_path, make_app=make_app, allowed_paths={"scrape"})
    with TestClient(app) as client:
        resp = client.post("/v2/evil", json={"x": 1})
    assert resp.status_code == 404
    body = resp.json()
    assert body["success"] is False
    assert body["error"] == "Path not allowed"


def test_path_whitelist_allows_known_v2_paths(tmp_path, make_app):
    app = _make_app(tmp_path, make_app=make_app, allowed_paths={"map"})

    with TestClient(app) as client:
        resp = client.post("/v2/map", json={"url": "https://example.com"})
    assert resp.status_code == 401
    body = resp.json()
    assert body["success"] is False
    assert body["error"] == "Missing or invalid client token"


def test_request_log_captures_error_details_from_upstream_response(tmp_path, make_app):
    from starlette.responses import Response

    big = "x" * 9000
    body = json.dumps({"error": {"code": "BAD", "message": big}}, ensure_ascii=False).encode(
        "utf-8"
    )

    app = _make_app(tmp_path, make_app=make_app, allowed_paths={"scrape_error"})

    @app.post("/api/scrape_error")
    def _scrape_error():
        return Response(content=body, status_code=400, media_type="application/json")

    with TestClient(app) as client:
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
