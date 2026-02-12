from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import AppConfig, Secrets
from app.db.models import Base
from app.main import create_app


def test_ui_is_served_when_control_plane_enabled(tmp_path):
    config = AppConfig()
    config.database.path = (tmp_path / "ui.db").as_posix()
    app = create_app(config=config, secrets=Secrets(admin_token="admin", master_key="master"))
    Base.metadata.create_all(app.state.db_engine)

    with TestClient(app) as client:
        resp = client.get("/ui/")

    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "FCAM WebUI" in resp.text
    assert 'class="tabs"' in resp.text
    assert 'class="nav-item" type="button" data-view="dashboard"' in resp.text
    assert 'class="nav-item" type="button" data-view="keys"' in resp.text
    assert 'class="nav-item" type="button" data-view="clients"' in resp.text
    assert 'class="nav-item" type="button" data-view="logs"' in resp.text
    assert 'class="nav-item" type="button" data-view="audit"' in resp.text
    assert 'class="nav-item" type="button" data-view="help"' in resp.text
    assert 'id="connRememberMode"' in resp.text
    assert 'id="connRememberHours"' in resp.text
    assert 'id="keyTestUrl"' in resp.text
    assert 'id="clientLog"' in resp.text
    assert 'id="dpClientToken"' in resp.text
    assert 'id="dpTestUrl"' in resp.text
    assert 'id="dpTestRun"' in resp.text
    assert 'id="dpTestOutput"' in resp.text
    assert "app.css" in resp.text
    assert "app.js" in resp.text


def test_ui_static_assets_are_served(tmp_path):
    config = AppConfig()
    config.database.path = (tmp_path / "ui.db").as_posix()
    app = create_app(config=config, secrets=Secrets(admin_token="admin", master_key="master"))
    Base.metadata.create_all(app.state.db_engine)

    with TestClient(app) as client:
        css = client.get("/ui/app.css")
        js = client.get("/ui/app.js")

    assert css.status_code == 200
    assert "text/css" in css.headers.get("content-type", "")
    assert "[hidden]" in css.text
    assert "display: none !important" in css.text
    assert js.status_code == 200
    assert "javascript" in js.headers.get("content-type", "")


def test_ui_is_not_served_when_control_plane_disabled(tmp_path):
    config = AppConfig()
    config.server.enable_control_plane = False
    config.database.path = (tmp_path / "ui.db").as_posix()
    app = create_app(config=config, secrets=Secrets(admin_token="admin", master_key="master"))
    Base.metadata.create_all(app.state.db_engine)

    with TestClient(app) as client:
        resp = client.get("/ui/")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"
