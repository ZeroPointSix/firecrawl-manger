from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import AppConfig, Secrets
from app.db.models import Base
from app.main import create_app


def test_ui2_is_served_when_control_plane_enabled(tmp_path):
    config = AppConfig()
    config.database.path = (tmp_path / "ui2.db").as_posix()
    app = create_app(config=config, secrets=Secrets(admin_token="admin", master_key="master"))
    Base.metadata.create_all(app.state.db_engine)

    with TestClient(app) as client:
        resp = client.get("/ui2/")

    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "FCAM WebUI" in resp.text
    assert 'id="app"' in resp.text


def test_ui_alias_redirects_to_ui2(tmp_path):
    config = AppConfig()
    config.database.path = (tmp_path / "ui2.db").as_posix()
    app = create_app(config=config, secrets=Secrets(admin_token="admin", master_key="master"))
    Base.metadata.create_all(app.state.db_engine)

    with TestClient(app) as client:
        resp = client.get("/ui/", follow_redirects=False)

    assert resp.status_code == 307
    assert resp.headers.get("location") == "/ui2/"


def test_ui2_is_not_served_when_control_plane_disabled(tmp_path):
    config = AppConfig()
    config.server.enable_control_plane = False
    config.database.path = (tmp_path / "ui2.db").as_posix()
    app = create_app(config=config, secrets=Secrets(admin_token="admin", master_key="master"))
    Base.metadata.create_all(app.state.db_engine)

    with TestClient(app) as client:
        resp = client.get("/ui2/")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"

