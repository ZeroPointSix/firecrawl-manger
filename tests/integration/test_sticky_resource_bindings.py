from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig, Secrets
from app.core.concurrency import ConcurrencyManager
from app.core.forwarder import Forwarder
from app.core.key_pool import KeyPool
from app.core.security import derive_master_key_bytes, encrypt_api_key, hmac_sha256_hex
from app.core.time import today_in_timezone
from app.db.models import ApiKey, Base, Client, UpstreamResourceBinding
from app.main import create_app

pytestmark = pytest.mark.integration


def _make_app(tmp_path, *, handler):
    token = "fcam_client_token"

    config = AppConfig()
    config.database.path = (tmp_path / "api.db").as_posix()
    config.firecrawl.base_url = "http://firecrawl.test"
    config.firecrawl.max_retries = 0
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
        token_hash = hmac_sha256_hex(derive_master_key_bytes(secrets.master_key), token)
        c = Client(
            name="svc",
            token_hash=token_hash,
            is_active=True,
            daily_quota=10_000,
            daily_usage=0,
            quota_reset_at=today_in_timezone("UTC"),
            rate_limit_per_min=10_000,
            max_concurrent=10,
        )
        db.add(c)
        db.flush()

        key_bytes = derive_master_key_bytes(secrets.master_key)
        k1 = ApiKey(
            client_id=c.id,
            api_key_ciphertext=encrypt_api_key(key_bytes, "fc-key-1"),
            api_key_hash="h1",
            api_key_last4="0001",
            is_active=True,
            status="active",
            daily_quota=100_000,
            daily_usage=0,
            quota_reset_at=today_in_timezone("UTC"),
            max_concurrent=10,
            rate_limit_per_min=10_000,
        )
        k2 = ApiKey(
            client_id=c.id,
            api_key_ciphertext=encrypt_api_key(key_bytes, "fc-key-2"),
            api_key_hash="h2",
            api_key_last4="0002",
            is_active=True,
            status="active",
            daily_quota=100_000,
            daily_usage=0,
            quota_reset_at=today_in_timezone("UTC"),
            max_concurrent=10,
            rate_limit_per_min=10_000,
        )
        db.add_all([k1, k2])
        db.commit()
        db.refresh(c)
        db.refresh(k1)
        db.refresh(k2)

        client_id = int(c.id)

    return app, token, client_id


def test_v2_crawl_status_uses_same_key_as_create(tmp_path):
    auth_by_job_id: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization") or ""
        if request.method.upper() == "POST" and request.url.path == "/v2/crawl":
            job_id = "job_1"
            auth_by_job_id[job_id] = auth
            return httpx.Response(200, json={"success": True, "id": job_id, "url": "https://example.com"})

        if request.method.upper() == "GET" and request.url.path == "/v2/crawl/job_1":
            if auth == auth_by_job_id.get("job_1"):
                return httpx.Response(200, json={"success": True, "id": "job_1"})
            return httpx.Response(404, json={"error": "Crawl job not found."})

        return httpx.Response(500, json={"error": "unexpected"})

    app, token, client_id = _make_app(tmp_path, handler=handler)
    headers = {"Authorization": f"Bearer {token}"}

    with TestClient(app) as client:
        r_create = client.post("/v2/crawl", headers=headers, json={"url": "https://example.com", "limit": 1})
        assert r_create.status_code == 200
        assert r_create.json()["id"] == "job_1"

        SessionLocal = app.state.db_session_factory
        with SessionLocal() as db:
            binding = (
                db.query(UpstreamResourceBinding)
                .filter(
                    UpstreamResourceBinding.client_id == client_id,
                    UpstreamResourceBinding.resource_type == "crawl",
                    UpstreamResourceBinding.resource_id == "job_1",
                )
                .one_or_none()
            )
            assert binding is not None

        r_status = client.get("/v2/crawl/job_1", headers=headers)
        assert r_status.status_code == 200
        assert r_status.json()["id"] == "job_1"


def test_v2_browser_execute_uses_same_key_as_create(tmp_path):
    auth_by_session_id: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization") or ""
        if request.method.upper() == "POST" and request.url.path == "/v2/browser":
            session_id = "sess_1"
            auth_by_session_id[session_id] = auth
            return httpx.Response(200, json={"success": True, "id": session_id})

        if request.method.upper() == "POST" and request.url.path == "/v2/browser/sess_1/execute":
            if auth == auth_by_session_id.get("sess_1"):
                return httpx.Response(200, json={"success": True, "result": "ok"})
            return httpx.Response(404, json={"error": "Session not found."})

        return httpx.Response(500, json={"error": "unexpected"})

    app, token, client_id = _make_app(tmp_path, handler=handler)
    headers = {"Authorization": f"Bearer {token}"}

    with TestClient(app) as client:
        r_create = client.post("/v2/browser", headers=headers, json={"ttl": 60})
        assert r_create.status_code == 200
        assert r_create.json()["id"] == "sess_1"

        SessionLocal = app.state.db_session_factory
        with SessionLocal() as db:
            binding = (
                db.query(UpstreamResourceBinding)
                .filter(
                    UpstreamResourceBinding.client_id == client_id,
                    UpstreamResourceBinding.resource_type == "browser",
                    UpstreamResourceBinding.resource_id == "sess_1",
                )
                .one_or_none()
            )
            assert binding is not None

        r_exec = client.post(
            "/v2/browser/sess_1/execute",
            headers=headers,
            json={"code": "console.log('ok')"},
        )
        assert r_exec.status_code == 200
        assert r_exec.json()["success"] is True

