from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import AppConfig, Secrets
from app.db.models import Base
from app.main import create_app


def test_control_plane_can_be_disabled(tmp_path):
    config = AppConfig()
    config.server.enable_control_plane = False
    config.database.path = (tmp_path / "api.db").as_posix()
    app = create_app(config=config, secrets=Secrets(admin_token="admin", master_key="master"))
    Base.metadata.create_all(app.state.db_engine)

    with TestClient(app) as client:
        resp = client.get("/admin/keys", headers={"Authorization": "Bearer admin"})

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_data_plane_can_be_disabled(tmp_path):
    config = AppConfig()
    config.server.enable_data_plane = False
    config.database.path = (tmp_path / "api.db").as_posix()
    app = create_app(config=config, secrets=Secrets(admin_token="admin", master_key="master"))
    Base.metadata.create_all(app.state.db_engine)

    with TestClient(app) as client:
        resp = client.post("/api/scrape", json={"url": "https://example.com"}, headers={"Authorization": "Bearer x"})

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"
