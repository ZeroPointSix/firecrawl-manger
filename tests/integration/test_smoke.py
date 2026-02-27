from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig, Secrets
from app.core.concurrency import ConcurrencyManager
from app.core.forwarder import Forwarder
from app.core.key_pool import KeyPool
from app.db.models import Base
from app.main import create_app

pytestmark = [pytest.mark.integration, pytest.mark.smoke]


def _admin_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _client_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _make_app(tmp_path, *, admin_token: str, master_key: str, upstream_api_key: str):
    config = AppConfig()
    config.database.path = (tmp_path / "smoke.db").as_posix()
    config.firecrawl.base_url = "http://firecrawl.test"

    secrets = Secrets(admin_token=admin_token, master_key=master_key)
    app = create_app(config=config, secrets=secrets)
    Base.metadata.create_all(app.state.db_engine)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != f"Bearer {upstream_api_key}":
            return httpx.Response(401, json={"error": "missing or invalid upstream api key"})
        return httpx.Response(200, json={"ok": True, "method": request.method, "path": request.url.path})

    app.state.forwarder = Forwarder(
        config=config,
        secrets=secrets,
        key_pool=KeyPool(),
        key_concurrency=ConcurrencyManager(),
        transport=httpx.MockTransport(handler),
    )
    return app


def test_smoke_core_flow(tmp_path):
    admin_token = "admin"
    master_key = "master"
    upstream_api_key = "fc-xxxxxxxxxxxxxxxx0001"

    app = _make_app(
        tmp_path,
        admin_token=admin_token,
        master_key=master_key,
        upstream_api_key=upstream_api_key,
    )

    with TestClient(app) as http:
        r_health = http.get("/healthz")
        assert r_health.status_code == 200
        assert r_health.json() == {"ok": True}

        r_ready = http.get("/readyz")
        assert r_ready.status_code == 200
        assert r_ready.json() == {"ok": True}

        r_admin_unauth = http.get("/admin/stats")
        assert r_admin_unauth.status_code == 401
        assert r_admin_unauth.json()["error"]["code"] == "ADMIN_UNAUTHORIZED"

        r_admin = http.get("/admin/stats", headers=_admin_headers(admin_token))
        assert r_admin.status_code == 200

        r_client = http.post(
            "/admin/clients",
            headers=_admin_headers(admin_token),
            json={
                "name": "smoke-client",
                "daily_quota": 1000,
                "rate_limit_per_min": 60,
                "max_concurrent": 10,
                "is_active": True,
            },
        )
        assert r_client.status_code == 201
        client_token = r_client.json()["token"]
        client_id = r_client.json()["client"]["id"]

        r_key = http.post(
            "/admin/keys",
            headers=_admin_headers(admin_token),
            json={
                "api_key": upstream_api_key,
                "client_id": client_id,
                "name": "smoke-key",
                "plan_type": "free",
                "daily_quota": 5,
                "max_concurrent": 2,
                "rate_limit_per_min": 10,
                "is_active": True,
            },
        )
        assert r_key.status_code == 201

        r_api_unauth = http.post("/api/scrape", json={"url": "https://example.com"})
        assert r_api_unauth.status_code == 401
        body = r_api_unauth.json()
        assert body["success"] is False
        assert body["error"] == "Missing or invalid client token"

        r_scrape = http.post(
            "/api/scrape",
            headers=_client_headers(client_token),
            json={"url": "https://example.com"},
        )
        assert r_scrape.status_code == 200
        assert r_scrape.headers.get("x-request-id")
        assert r_scrape.json() == {"ok": True, "method": "POST", "path": "/v1/scrape"}
