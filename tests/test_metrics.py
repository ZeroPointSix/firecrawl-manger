from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from app.config import AppConfig, Secrets
from app.core.concurrency import ConcurrencyManager
from app.core.forwarder import Forwarder
from app.core.key_pool import KeyPool
from app.core.security import derive_master_key_bytes, encrypt_api_key, hmac_sha256_hex
from app.core.time import today_in_timezone
from app.db.models import ApiKey, Base, Client
from app.main import create_app


def test_metrics_endpoint_disabled_by_default(tmp_path):
    config = AppConfig()
    config.database.path = (tmp_path / "api.db").as_posix()
    secrets = Secrets(admin_token="admin", master_key="master")
    app = create_app(config=config, secrets=secrets)

    with TestClient(app) as client:
        resp = client.get("/metrics")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_metrics_endpoint_exposes_prometheus_metrics(tmp_path):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"ok": True})

    token = "fcam_client_token"

    config = AppConfig()
    config.database.path = (tmp_path / "api.db").as_posix()
    config.firecrawl.base_url = "http://firecrawl.test/v1"
    config.observability.metrics_enabled = True
    secrets = Secrets(admin_token="admin", master_key="master")
    app = create_app(config=config, secrets=secrets)
    Base.metadata.create_all(app.state.db_engine)

    transport = httpx.MockTransport(handler)
    app.state.forwarder = Forwarder(
        config=config,
        secrets=secrets,
        key_pool=KeyPool(),
        key_concurrency=ConcurrencyManager(),
        metrics=app.state.metrics,
        transport=transport,
    )

    SessionLocal = app.state.db_session_factory
    with SessionLocal() as db:
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

        client_id = c.id
        key_id = k.id

    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app) as client:
        r = client.post("/api/scrape", headers=headers, json={"url": "https://example.com"})
        assert r.status_code == 200
        assert calls["n"] == 1

        m = client.get("/metrics")

    assert m.status_code == 200
    assert m.headers["content-type"].startswith("text/plain")

    body = m.text
    assert "fcam_requests_total" in body
    assert 'endpoint="scrape"' in body
    assert f'client_id="{client_id}"' in body

    assert "fcam_key_selected_total" in body
    assert f'key_id="{key_id}"' in body

    assert "fcam_quota_remaining" in body
    assert 'scope="client"' in body
