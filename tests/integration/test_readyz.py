from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def test_readyz_reports_missing_secrets(tmp_path, make_app):
    app, _, _ = make_app(tmp_path, admin_token=None, master_key=None)
    with TestClient(app) as client:
        resp = client.get("/readyz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["error"]["code"] == "NOT_READY"
    assert "issues" in body["error"]["details"]


def test_readyz_ok_when_db_initialized(tmp_path, make_app):
    app, _, _ = make_app(tmp_path, db_name="initialized.db")
    with TestClient(app) as client:
        resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_readyz_does_not_require_admin_token_when_control_plane_disabled(tmp_path, make_app):
    def mutate(config):  # noqa: ANN001
        config.server.enable_control_plane = False

    app, _, _ = make_app(tmp_path, db_name="initialized.db", config_mutate=mutate, admin_token=None)
    with TestClient(app) as client:
        resp = client.get("/readyz")
    assert resp.status_code == 200


def test_readyz_reports_redis_unavailable_when_enabled(tmp_path, make_app):
    def mutate(config):  # noqa: ANN001
        config.state.mode = "redis"
        config.state.redis.url = "redis://127.0.0.1:1/0"

    app, _, _ = make_app(tmp_path, db_name="initialized.db", config_mutate=mutate)
    with TestClient(app) as client:
        resp = client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "NOT_READY"
