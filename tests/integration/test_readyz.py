from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig, Secrets
from app.main import create_app

pytestmark = pytest.mark.integration


def test_readyz_reports_missing_secrets(tmp_path):
    config = AppConfig()
    config.database.path = (tmp_path / "test.db").as_posix()
    app = create_app(config=config, secrets=Secrets(admin_token=None, master_key=None))
    client = TestClient(app)

    resp = client.get("/readyz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["error"]["code"] == "NOT_READY"
    assert "issues" in body["error"]["details"]


def test_readyz_ok_when_db_initialized(tmp_path):
    config = AppConfig()
    config.database.path = (tmp_path / "initialized.db").as_posix()
    secrets = Secrets(admin_token="admin", master_key="master")
    app = create_app(config=config, secrets=secrets)

    from app.db.models import Base

    Base.metadata.create_all(app.state.db_engine)

    client = TestClient(app)
    resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_readyz_does_not_require_admin_token_when_control_plane_disabled(tmp_path):
    config = AppConfig()
    config.server.enable_control_plane = False
    config.database.path = (tmp_path / "initialized.db").as_posix()
    secrets = Secrets(admin_token=None, master_key="master")
    app = create_app(config=config, secrets=secrets)

    from app.db.models import Base

    Base.metadata.create_all(app.state.db_engine)

    client = TestClient(app)
    resp = client.get("/readyz")
    assert resp.status_code == 200


def test_readyz_reports_redis_unavailable_when_enabled(tmp_path):
    config = AppConfig()
    config.state.mode = "redis"
    config.state.redis.url = "redis://127.0.0.1:1/0"
    config.database.path = (tmp_path / "initialized.db").as_posix()
    secrets = Secrets(admin_token="admin", master_key="master")
    app = create_app(config=config, secrets=secrets)

    from app.db.models import Base

    Base.metadata.create_all(app.state.db_engine)

    client = TestClient(app)
    resp = client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "NOT_READY"
