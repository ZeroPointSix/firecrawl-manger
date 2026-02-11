from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
from fastapi.testclient import TestClient

from app.config import AppConfig, Secrets
from app.core.concurrency import ConcurrencyManager
from app.core.forwarder import Forwarder
from app.core.key_pool import KeyPool
from app.core.security import derive_master_key_bytes, encrypt_api_key, hmac_sha256_hex
from app.core.time import today_in_timezone
from app.db.models import ApiKey, Base, Client, RequestLog
from app.main import create_app


def _make_app(tmp_path, *, handler=None):
    config = AppConfig()
    config.database.path = (tmp_path / "admin.db").as_posix()
    config.firecrawl.base_url = "http://firecrawl.test/v1"
    secrets = Secrets(admin_token="admin", master_key="master")
    app = create_app(config=config, secrets=secrets)
    Base.metadata.create_all(app.state.db_engine)

    if handler is not None:
        app.state.forwarder = Forwarder(
            config=config,
            secrets=secrets,
            key_pool=KeyPool(),
            key_concurrency=ConcurrencyManager(),
            transport=httpx.MockTransport(handler),
        )

    return app, secrets


def _admin_headers():
    return {"Authorization": "Bearer admin"}


def test_admin_requires_auth(tmp_path):
    app, _ = _make_app(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/admin/keys")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "ADMIN_UNAUTHORIZED"
    assert "X-Request-Id" in resp.headers


def test_admin_keys_crud_and_audit(tmp_path):
    app, secrets = _make_app(tmp_path)

    with TestClient(app) as client:
        r1 = client.post(
            "/admin/keys",
            headers=_admin_headers(),
            json={
                "api_key": "fc-xxxxxxxxxxxxxxxx0001",
                "name": "free-01",
                "plan_type": "free",
                "daily_quota": 5,
                "max_concurrent": 2,
                "rate_limit_per_min": 10,
                "is_active": True,
            },
        )
        assert r1.status_code == 201
        key_id = r1.json()["id"]
        assert r1.json()["api_key_masked"] == "fc-****0001"

        rdup = client.post(
            "/admin/keys",
            headers=_admin_headers(),
            json={
                "api_key": "fc-xxxxxxxxxxxxxxxx0001",
                "name": "dup",
                "plan_type": "free",
                "daily_quota": 5,
                "max_concurrent": 2,
                "rate_limit_per_min": 10,
                "is_active": True,
            },
        )
        assert rdup.status_code == 409
        assert rdup.json()["error"]["code"] == "API_KEY_DUPLICATE"

        rlist = client.get("/admin/keys", headers=_admin_headers())
        assert rlist.status_code == 200
        assert any(item["id"] == key_id for item in rlist.json()["items"])

        rup = client.put(
            f"/admin/keys/{key_id}",
            headers=_admin_headers(),
            json={"daily_quota": 10, "is_active": True},
        )
        assert rup.status_code == 200
        assert rup.json()["daily_quota"] == 10

        rdel = client.delete(f"/admin/keys/{key_id}", headers=_admin_headers())
        assert rdel.status_code == 204

        rlist2 = client.get("/admin/keys", headers=_admin_headers())
        key2 = next(item for item in rlist2.json()["items"] if item["id"] == key_id)
        assert key2["is_active"] is False
        assert key2["status"] == "disabled"

        raudit = client.get("/admin/audit-logs", headers=_admin_headers())
        assert raudit.status_code == 200
        actions = [a["action"] for a in raudit.json()["items"]]
        assert "key.create" in actions
        assert "key.update" in actions
        assert "key.delete" in actions


def test_admin_clients_create_rotate_disable(tmp_path):
    app, _ = _make_app(tmp_path)

    with TestClient(app) as client:
        r1 = client.post(
            "/admin/clients",
            headers=_admin_headers(),
            json={
                "name": "service-a",
                "daily_quota": 1000,
                "rate_limit_per_min": 60,
                "max_concurrent": 10,
                "is_active": True,
            },
        )
        assert r1.status_code == 201
        body = r1.json()
        assert body["client"]["name"] == "service-a"
        assert body["token"].startswith("fcam_client_")
        client_id = body["client"]["id"]

        rlist = client.get("/admin/clients", headers=_admin_headers())
        assert rlist.status_code == 200
        assert any(item["id"] == client_id for item in rlist.json()["items"])

        rrot = client.post(f"/admin/clients/{client_id}/rotate", headers=_admin_headers())
        assert rrot.status_code == 200
        assert rrot.json()["client_id"] == client_id
        assert rrot.json()["token"].startswith("fcam_client_")

        rdel = client.delete(f"/admin/clients/{client_id}", headers=_admin_headers())
        assert rdel.status_code == 204

        rlist2 = client.get("/admin/clients", headers=_admin_headers())
        c2 = next(item for item in rlist2.json()["items"] if item["id"] == client_id)
        assert c2["is_active"] is False

        raudit = client.get("/admin/audit-logs", headers=_admin_headers())
        assert raudit.status_code == 200
        actions = [a["action"] for a in raudit.json()["items"]]
        assert "client.create" in actions
        assert "client.rotate" in actions
        assert "client.delete" in actions


def test_admin_stats_and_quota_stats(tmp_path):
    app, secrets = _make_app(tmp_path)
    SessionLocal = app.state.db_session_factory
    with SessionLocal() as db:
        key_bytes = derive_master_key_bytes(secrets.master_key)
        today = today_in_timezone("UTC")
        db.add(
            ApiKey(
                api_key_ciphertext=encrypt_api_key(key_bytes, "fc-key-1"),
                api_key_hash=hmac_sha256_hex(key_bytes, "fc-key-1"),
                api_key_last4="0001",
                name="k1",
                plan_type="free",
                is_active=True,
                status="active",
                daily_quota=5,
                daily_usage=2,
                quota_reset_at=today,
                max_concurrent=2,
                rate_limit_per_min=10,
            )
        )
        db.add(
            ApiKey(
                api_key_ciphertext=encrypt_api_key(key_bytes, "fc-key-2"),
                api_key_hash=hmac_sha256_hex(key_bytes, "fc-key-2"),
                api_key_last4="0002",
                name="k2",
                plan_type="free",
                is_active=False,
                status="disabled",
                daily_quota=5,
                daily_usage=0,
                quota_reset_at=today,
                max_concurrent=2,
                rate_limit_per_min=10,
            )
        )
        db.add(
            Client(
                name="svc",
                token_hash=hmac_sha256_hex(key_bytes, "tok"),
                is_active=True,
                daily_quota=10,
                daily_usage=1,
                quota_reset_at=today,
                rate_limit_per_min=60,
                max_concurrent=10,
            )
        )
        db.commit()

    with TestClient(app) as client:
        rs = client.get("/admin/stats", headers=_admin_headers())
        assert rs.status_code == 200
        assert rs.json()["keys"]["total"] == 2
        assert rs.json()["clients"]["total"] == 1

        rq = client.get("/admin/stats/quota", headers=_admin_headers())
        assert rq.status_code == 200
        summary = rq.json()["summary"]
        assert summary["total_quota"] == 5
        assert summary["used_today"] == 2
        assert summary["remaining"] == 3


def test_admin_logs_query_pagination_and_filters(tmp_path):
    app, secrets = _make_app(tmp_path)
    SessionLocal = app.state.db_session_factory

    with SessionLocal() as db:
        key_bytes = derive_master_key_bytes(secrets.master_key)
        today = today_in_timezone("UTC")
        k = ApiKey(
            api_key_ciphertext=encrypt_api_key(key_bytes, "fc-key-1"),
            api_key_hash=hmac_sha256_hex(key_bytes, "fc-key-1"),
            api_key_last4="0001",
            name="k1",
            plan_type="free",
            is_active=True,
            status="active",
            daily_quota=5,
            daily_usage=0,
            quota_reset_at=today,
            max_concurrent=2,
            rate_limit_per_min=10,
        )
        c = Client(
            name="svc",
            token_hash=hmac_sha256_hex(key_bytes, "tok"),
            is_active=True,
            daily_quota=10,
            daily_usage=0,
            quota_reset_at=today,
            rate_limit_per_min=60,
            max_concurrent=10,
        )
        db.add(k)
        db.add(c)
        db.commit()
        db.refresh(k)
        db.refresh(c)

        now = datetime.now(timezone.utc)
        db.add(
            RequestLog(
                request_id="req_a",
                client_id=c.id,
                api_key_id=k.id,
                endpoint="scrape",
                method="POST",
                status_code=200,
                response_time_ms=10,
                success=True,
                retry_count=0,
                error_message=None,
                idempotency_key=None,
                created_at=now - timedelta(seconds=10),
            )
        )
        db.add(
            RequestLog(
                request_id="req_b",
                client_id=c.id,
                api_key_id=k.id,
                endpoint="scrape",
                method="POST",
                status_code=429,
                response_time_ms=20,
                success=False,
                retry_count=1,
                error_message="CLIENT_RATE_LIMITED",
                idempotency_key=None,
                created_at=now,
            )
        )
        db.commit()

    with TestClient(app) as client:
        r1 = client.get("/admin/logs?limit=1", headers=_admin_headers())
        assert r1.status_code == 200
        assert r1.json()["has_more"] is True
        assert len(r1.json()["items"]) == 1
        assert r1.json()["items"][0]["request_id"] == "req_b"
        assert r1.json()["items"][0]["api_key_masked"] == "fc-****0001"

        cursor = r1.json()["next_cursor"]
        r2 = client.get(f"/admin/logs?limit=10&cursor={cursor}", headers=_admin_headers())
        assert r2.status_code == 200
        assert [i["request_id"] for i in r2.json()["items"]] == ["req_a"]

        r3 = client.get("/admin/logs?request_id=req_a", headers=_admin_headers())
        assert r3.status_code == 200
        assert len(r3.json()["items"]) == 1
        assert r3.json()["items"][0]["request_id"] == "req_a"


def test_admin_key_test_marks_cooling_on_429(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "2"}, json={"error": "rate"})

    app, secrets = _make_app(tmp_path, handler=handler)
    SessionLocal = app.state.db_session_factory

    with SessionLocal() as db:
        key_bytes = derive_master_key_bytes(secrets.master_key)
        today = today_in_timezone("UTC")
        k = ApiKey(
            api_key_ciphertext=encrypt_api_key(key_bytes, "fc-key-1"),
            api_key_hash=hmac_sha256_hex(key_bytes, "fc-key-1"),
            api_key_last4="0001",
            name="k1",
            plan_type="free",
            is_active=True,
            status="active",
            daily_quota=5,
            daily_usage=0,
            quota_reset_at=today,
            max_concurrent=2,
            rate_limit_per_min=10,
        )
        db.add(k)
        db.commit()
        db.refresh(k)
        key_id = k.id

    with TestClient(app) as client:
        resp = client.post(
            f"/admin/keys/{key_id}/test",
            headers=_admin_headers(),
            json={"mode": "scrape", "test_url": "https://example.com"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["key_id"] == key_id
        assert body["ok"] is False
        assert body["upstream_status_code"] == 429
        assert body["observed"]["status"] == "cooling"
        assert body["observed"]["cooldown_until"] is not None

        raudit = client.get("/admin/audit-logs", headers=_admin_headers())
        assert raudit.status_code == 200
        actions = [a["action"] for a in raudit.json()["items"]]
        assert "key.test" in actions

    with SessionLocal() as db:
        k2 = db.query(ApiKey).filter(ApiKey.id == key_id).one()
        assert k2.status == "cooling"
        assert k2.cooldown_until is not None


def test_admin_keys_reset_quota_resets_usage_and_writes_audit(tmp_path):
    app, secrets = _make_app(tmp_path)
    SessionLocal = app.state.db_session_factory

    with SessionLocal() as db:
        key_bytes = derive_master_key_bytes(secrets.master_key)
        today = today_in_timezone("UTC")
        db.add(
            ApiKey(
                api_key_ciphertext=encrypt_api_key(key_bytes, "fc-key-1"),
                api_key_hash=hmac_sha256_hex(key_bytes, "fc-key-1"),
                api_key_last4="0001",
                name="k1",
                plan_type="free",
                is_active=True,
                status="quota_exceeded",
                daily_quota=5,
                daily_usage=5,
                quota_reset_at=today,
                max_concurrent=2,
                rate_limit_per_min=10,
            )
        )
        db.commit()

    with TestClient(app) as client:
        resp = client.post("/admin/keys/reset-quota", headers=_admin_headers())
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["affected_keys"] == 1

        raudit = client.get("/admin/audit-logs?action=quota.reset", headers=_admin_headers())
        assert raudit.status_code == 200
        assert len(raudit.json()["items"]) == 1
        assert raudit.json()["items"][0]["action"] == "quota.reset"
        assert raudit.json()["items"][0]["resource_type"] == "api_key"

    with SessionLocal() as db:
        k = db.query(ApiKey).one()
        assert k.daily_usage == 0
        assert k.status == "active"


def test_admin_clients_update_writes_audit(tmp_path):
    app, _ = _make_app(tmp_path)

    with TestClient(app) as client:
        r1 = client.post(
            "/admin/clients",
            headers=_admin_headers(),
            json={
                "name": "service-a",
                "daily_quota": 1000,
                "rate_limit_per_min": 60,
                "max_concurrent": 10,
                "is_active": True,
            },
        )
        assert r1.status_code == 201
        client_id = r1.json()["client"]["id"]

        rup = client.put(
            f"/admin/clients/{client_id}",
            headers=_admin_headers(),
            json={"max_concurrent": 20, "is_active": False},
        )
        assert rup.status_code == 200
        assert rup.json()["max_concurrent"] == 20
        assert rup.json()["is_active"] is False

        raudit = client.get("/admin/audit-logs?action=client.update", headers=_admin_headers())
        assert raudit.status_code == 200
        assert len(raudit.json()["items"]) == 1
        assert raudit.json()["items"][0]["action"] == "client.update"
        assert raudit.json()["items"][0]["resource_type"] == "client"


def test_admin_audit_logs_pagination_and_filters(tmp_path):
    app, _ = _make_app(tmp_path)

    with TestClient(app) as client:
        c = client.post(
            "/admin/clients",
            headers=_admin_headers(),
            json={
                "name": "service-a",
                "daily_quota": 1000,
                "rate_limit_per_min": 60,
                "max_concurrent": 10,
                "is_active": True,
            },
        )
        assert c.status_code == 201
        client_id = c.json()["client"]["id"]

        rrot = client.post(f"/admin/clients/{client_id}/rotate", headers=_admin_headers())
        assert rrot.status_code == 200

        page1 = client.get("/admin/audit-logs?limit=1", headers=_admin_headers())
        assert page1.status_code == 200
        assert len(page1.json()["items"]) == 1
        assert page1.json()["has_more"] is True

        cursor = page1.json()["next_cursor"]
        assert cursor is not None

        page2 = client.get(f"/admin/audit-logs?limit=50&cursor={cursor}", headers=_admin_headers())
        assert page2.status_code == 200
        assert len(page2.json()["items"]) >= 1

        filtered = client.get("/admin/audit-logs?action=client.rotate", headers=_admin_headers())
        assert filtered.status_code == 200
        assert len(filtered.json()["items"]) == 1
        assert filtered.json()["items"][0]["action"] == "client.rotate"
