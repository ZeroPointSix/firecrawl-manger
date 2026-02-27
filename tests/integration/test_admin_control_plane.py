from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.security import derive_master_key_bytes, encrypt_api_key, hmac_sha256_hex
from app.core.time import today_in_timezone
from app.db.models import ApiKey, Client, IdempotencyRecord, RequestLog

pytestmark = pytest.mark.integration


def _make_app(tmp_path, *, make_app, handler=None):
    app, _, secrets = make_app(tmp_path, db_name="admin.db", handler=handler)
    return app, secrets


def test_admin_requires_auth(tmp_path, make_app):
    app, _ = _make_app(tmp_path, make_app=make_app)
    with TestClient(app) as client:
        resp = client.get("/admin/keys")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "ADMIN_UNAUTHORIZED"
    assert "X-Request-Id" in resp.headers


def test_admin_keys_crud_and_audit(tmp_path, make_app, admin_headers):
    app, secrets = _make_app(tmp_path, make_app=make_app)

    with TestClient(app) as client:
        r1 = client.post(
            "/admin/keys",
            headers=admin_headers(),
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
            headers=admin_headers(),
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

        rlist = client.get("/admin/keys", headers=admin_headers())
        assert rlist.status_code == 200
        assert any(item["id"] == key_id for item in rlist.json()["items"])

        rup = client.put(
            f"/admin/keys/{key_id}",
            headers=admin_headers(),
            json={"daily_quota": 10, "is_active": True},
        )
        assert rup.status_code == 200
        assert rup.json()["daily_quota"] == 10

        rdel = client.delete(f"/admin/keys/{key_id}", headers=admin_headers())
        assert rdel.status_code == 204

        rlist2 = client.get("/admin/keys", headers=admin_headers())
        key2 = next(item for item in rlist2.json()["items"] if item["id"] == key_id)
        assert key2["is_active"] is False
        assert key2["status"] == "disabled"

        raudit = client.get("/admin/audit-logs", headers=admin_headers())
        assert raudit.status_code == 200
        actions = [a["action"] for a in raudit.json()["items"]]
        assert "key.create" in actions
        assert "key.update" in actions
        assert "key.delete" in actions


def test_admin_keys_import_text_creates_and_updates(tmp_path, make_app, admin_headers):
    app, _ = _make_app(tmp_path, make_app=make_app)

    with TestClient(app) as client:
        r = client.post(
            "/admin/keys/import-text",
            headers=admin_headers(),
            json={
                "text": "\n".join(
                    [
                        "fc-xxxxxxxxxxxxxxxx0001",
                        "user@example.com|p@ssw0rd|fc-xxxxxxxxxxxxxxxx0001|2026-02-12T10:00:00Z",
                        "u2,p2,fc-yyyyyyyyyyyyyyyy0002,2026-02-12",
                        "u3,p3,fc-zzzzzzzzzzzzzzzz0003,2026-02-10 12:36:35",
                        "# comment",
                        "",
                    ]
                )
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["created"] == 3
        assert body["updated"] == 1
        assert body["failed"] == 0

        rlist = client.get("/admin/keys", headers=admin_headers())
        assert rlist.status_code == 200
        items = rlist.json()["items"]
        k1 = next(i for i in items if i["api_key_masked"] == "fc-****0001")
        assert k1["account_username"] == "user@example.com"
        assert k1["account_verified_at"] == "2026-02-12T10:00:00Z"

        k2 = next(i for i in items if i["api_key_masked"] == "fc-****0002")
        assert k2["account_username"] == "u2"
        assert k2["account_verified_at"] == "2026-02-12T00:00:00Z"

        k3 = next(i for i in items if i["api_key_masked"] == "fc-****0003")
        assert k3["account_username"] == "u3"
        assert k3["account_verified_at"] == "2026-02-10T12:36:35Z"


def test_admin_keys_list_supports_pagination_and_name_search(tmp_path, make_app, admin_headers):
    app, _ = _make_app(tmp_path, make_app=make_app)

    with TestClient(app) as client:
        for i, name in enumerate(["alpha", "beta", "alpha-2", None], start=1):
            r = client.post(
                "/admin/keys",
                headers=admin_headers(),
                json={
                    "api_key": f"fc-xxxxxxxxxxxxxxxx00{i:02d}",
                    "name": name,
                    "plan_type": "free",
                    "daily_quota": 5,
                    "max_concurrent": 2,
                    "rate_limit_per_min": 10,
                    "is_active": True,
                },
            )
            assert r.status_code == 201

        rpage1 = client.get("/admin/keys?page=1&page_size=2", headers=admin_headers())
        assert rpage1.status_code == 200
        body1 = rpage1.json()
        assert "pagination" in body1
        assert body1["pagination"]["page"] == 1
        assert body1["pagination"]["page_size"] == 2
        assert body1["pagination"]["total_items"] == 4
        assert body1["pagination"]["total_pages"] == 2
        assert len(body1["items"]) == 2

        rpage2 = client.get("/admin/keys?page=2&page_size=2", headers=admin_headers())
        assert rpage2.status_code == 200
        body2 = rpage2.json()
        assert body2["pagination"]["page"] == 2
        assert len(body2["items"]) == 2

        rsearch = client.get("/admin/keys?page=1&page_size=50&q=ALPHA", headers=admin_headers())
        assert rsearch.status_code == 200
        items = rsearch.json()["items"]
        assert len(items) == 2
        assert all("alpha" in (i["name"] or "").lower() for i in items)


def test_admin_keys_batch_best_effort_patch_reset_and_test(tmp_path, make_app, admin_headers):
    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization")
        if auth == "Bearer fc-xxxxxxxxxxxxxxxx0001":
            return httpx.Response(200, json={"ok": True})
        if auth == "Bearer fc-xxxxxxxxxxxxxxxx0002":
            return httpx.Response(429, headers={"Retry-After": "1"}, json={"error": "rate"})
        return httpx.Response(500, json={"error": "unexpected"})

    app, _ = _make_app(tmp_path, make_app=make_app, handler=handler)

    with TestClient(app) as client:
        r1 = client.post(
            "/admin/keys",
            headers=admin_headers(),
            json={
                "api_key": "fc-xxxxxxxxxxxxxxxx0001",
                "name": "k1",
                "plan_type": "free",
                "daily_quota": 5,
                "max_concurrent": 2,
                "rate_limit_per_min": 10,
                "is_active": True,
            },
        )
        assert r1.status_code == 201
        k1_id = r1.json()["id"]

        r2 = client.post(
            "/admin/keys",
            headers=admin_headers(),
            json={
                "api_key": "fc-xxxxxxxxxxxxxxxx0002",
                "name": "k2",
                "plan_type": "free",
                "daily_quota": 5,
                "max_concurrent": 2,
                "rate_limit_per_min": 10,
                "is_active": True,
            },
        )
        assert r2.status_code == 201
        k2_id = r2.json()["id"]

        # Pre-condition: put k2 into cooling via test (429)
        rt0 = client.post(
            f"/admin/keys/{k2_id}/test",
            headers=admin_headers(),
            json={"mode": "scrape", "test_url": "https://example.com"},
        )
        assert rt0.status_code == 200
        assert rt0.json()["ok"] is False

        rbatch = client.post(
            "/admin/keys/batch",
            headers=admin_headers(),
            json={
                "ids": [k1_id, k2_id, 999999],
                "patch": {"daily_quota": 10, "is_active": False},
                "reset_cooldown": True,
                "soft_delete": False,
                "test": {"mode": "scrape", "test_url": "https://example.com"},
            },
        )
        assert rbatch.status_code == 200
        body = rbatch.json()
        assert body["requested"] == 3
        assert body["succeeded"] == 2
        assert body["failed"] == 1

        results_by_id = {r["id"]: r for r in body["results"]}
        assert results_by_id[k1_id]["ok"] is True
        assert results_by_id[k2_id]["ok"] is True
        assert results_by_id[999999]["ok"] is False
        assert results_by_id[999999]["error"]["code"] == "NOT_FOUND"

        rlist = client.get("/admin/keys", headers=admin_headers())
        assert rlist.status_code == 200
        items = {i["id"]: i for i in rlist.json()["items"]}
        assert items[k1_id]["daily_quota"] == 10
        assert items[k1_id]["is_active"] is False
        assert items[k1_id]["status"] == "disabled"
        assert items[k2_id]["daily_quota"] == 10
        assert items[k2_id]["is_active"] is False
        assert items[k2_id]["status"] == "disabled"
        assert items[k2_id]["cooldown_until"] is None


def test_admin_keys_batch_test_runs_concurrently(tmp_path, make_app, admin_headers):
    barrier = threading.Barrier(2)
    seen_threads: set[int] = set()
    lock = threading.Lock()

    def handler(request: httpx.Request) -> httpx.Response:
        with lock:
            seen_threads.add(threading.get_ident())
        barrier.wait(timeout=5)
        return httpx.Response(200, json={"ok": True})

    app, _ = _make_app(tmp_path, make_app=make_app, handler=handler)

    with TestClient(app) as client:
        r1 = client.post(
            "/admin/keys",
            headers=admin_headers(),
            json={
                "api_key": "fc-xxxxxxxxxxxxxxxx0001",
                "name": "k1",
                "plan_type": "free",
                "daily_quota": 5,
                "max_concurrent": 2,
                "rate_limit_per_min": 10,
                "is_active": True,
            },
        )
        assert r1.status_code == 201
        k1_id = r1.json()["id"]

        r2 = client.post(
            "/admin/keys",
            headers=admin_headers(),
            json={
                "api_key": "fc-xxxxxxxxxxxxxxxx0002",
                "name": "k2",
                "plan_type": "free",
                "daily_quota": 5,
                "max_concurrent": 2,
                "rate_limit_per_min": 10,
                "is_active": True,
            },
        )
        assert r2.status_code == 201
        k2_id = r2.json()["id"]

        rbatch = client.post(
            "/admin/keys/batch",
            headers=admin_headers(),
            json={
                "ids": [k1_id, k2_id],
                "test": {"mode": "scrape", "test_url": "https://example.com"},
            },
        )
        assert rbatch.status_code == 200
        body = rbatch.json()
        assert body["requested"] == 2
        assert body["succeeded"] == 2
        assert body["failed"] == 0
        assert all(item["ok"] is True for item in body["results"])
        assert all(item["test"] is not None for item in body["results"])

    assert len(seen_threads) >= 2


def test_admin_clients_create_rotate_disable(tmp_path, make_app, admin_headers):
    app, _ = _make_app(tmp_path, make_app=make_app)

    with TestClient(app) as client:
        r1 = client.post(
            "/admin/clients",
            headers=admin_headers(),
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
        assert body["client"]["status"] == "active"
        client_id = body["client"]["id"]

        rlist = client.get("/admin/clients", headers=admin_headers())
        assert rlist.status_code == 200
        assert any(item["id"] == client_id for item in rlist.json()["items"])

        rrot = client.post(f"/admin/clients/{client_id}/rotate", headers=admin_headers())
        assert rrot.status_code == 200
        assert rrot.json()["client_id"] == client_id
        assert rrot.json()["token"].startswith("fcam_client_")

        rdel = client.delete(f"/admin/clients/{client_id}", headers=admin_headers())
        assert rdel.status_code == 204

        rlist2 = client.get("/admin/clients", headers=admin_headers())
        assert all(item["id"] != client_id for item in rlist2.json()["items"])

        SessionLocal = app.state.db_session_factory
        with SessionLocal() as db:
            c2 = db.query(Client).filter(Client.id == client_id).one()
            assert c2.is_active is False
            assert c2.status == "deleted"

        raudit = client.get("/admin/audit-logs", headers=admin_headers())
        assert raudit.status_code == 200
        actions = [a["action"] for a in raudit.json()["items"]]
        assert "client.create" in actions
        assert "client.rotate" in actions
        assert "client.delete" in actions


def test_admin_clients_create_disabled_visible_and_status_disabled(
    tmp_path, make_app, admin_headers
):
    app, _ = _make_app(tmp_path, make_app=make_app)

    with TestClient(app) as client:
        r1 = client.post(
            "/admin/clients",
            headers=admin_headers(),
            json={
                "name": "service-disabled",
                "daily_quota": 1000,
                "rate_limit_per_min": 60,
                "max_concurrent": 10,
                "is_active": False,
            },
        )
        assert r1.status_code == 201
        body = r1.json()
        assert body["client"]["is_active"] is False
        assert body["client"]["status"] == "disabled"
        client_id = body["client"]["id"]

        rlist = client.get("/admin/clients", headers=admin_headers())
        assert rlist.status_code == 200
        item = next(i for i in rlist.json()["items"] if i["id"] == client_id)
        assert item["is_active"] is False
        assert item["status"] == "disabled"


def test_admin_stats_and_quota_stats(tmp_path, make_app, admin_headers):
    app, secrets = _make_app(tmp_path, make_app=make_app)
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
        rs = client.get("/admin/stats", headers=admin_headers())
        assert rs.status_code == 200
        assert rs.json()["keys"]["total"] == 2
        assert rs.json()["clients"]["total"] == 1

        rq = client.get("/admin/stats/quota", headers=admin_headers())
        assert rq.status_code == 200
        summary = rq.json()["summary"]
        assert summary["total_quota"] == 5
        assert summary["used_today"] == 2
        assert summary["remaining"] == 3


def test_admin_logs_query_pagination_and_filters(tmp_path, make_app, admin_headers):
    app, secrets = _make_app(tmp_path, make_app=make_app)
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
        r1 = client.get("/admin/logs?limit=1", headers=admin_headers())
        assert r1.status_code == 200
        assert r1.json()["has_more"] is True
        assert len(r1.json()["items"]) == 1
        assert r1.json()["items"][0]["request_id"] == "req_b"
        assert r1.json()["items"][0]["level"] == "warn"
        assert r1.json()["items"][0]["api_key_masked"] == "fc-****0001"

        cursor = r1.json()["next_cursor"]
        r2 = client.get(f"/admin/logs?limit=10&cursor={cursor}", headers=admin_headers())
        assert r2.status_code == 200
        assert [i["request_id"] for i in r2.json()["items"]] == ["req_a"]
        assert r2.json()["items"][0]["level"] == "info"

        r3 = client.get("/admin/logs?request_id=req_a", headers=admin_headers())
        assert r3.status_code == 200
        assert len(r3.json()["items"]) == 1
        assert r3.json()["items"][0]["request_id"] == "req_a"

        r4 = client.get("/admin/logs?level=warn", headers=admin_headers())
        assert r4.status_code == 200
        assert [i["request_id"] for i in r4.json()["items"]] == ["req_b"]

        r5 = client.get("/admin/logs?level=info", headers=admin_headers())
        assert r5.status_code == 200
        assert [i["request_id"] for i in r5.json()["items"]] == ["req_a"]

        r6 = client.get("/admin/logs?q=rate", headers=admin_headers())
        assert r6.status_code == 200
        assert [i["request_id"] for i in r6.json()["items"]] == ["req_b"]

        r7 = client.get("/admin/logs?level=wat", headers=admin_headers())
        assert r7.status_code == 400
        assert r7.json()["error"]["code"] == "VALIDATION_ERROR"


def test_admin_key_test_marks_cooling_on_429(tmp_path, make_app, admin_headers):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "2"}, json={"error": "rate"})

    app, secrets = _make_app(tmp_path, make_app=make_app, handler=handler)
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
            headers=admin_headers(),
            json={"mode": "scrape", "test_url": "https://example.com"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["key_id"] == key_id
        assert body["ok"] is False
        assert body["upstream_status_code"] == 429
        assert body["observed"]["status"] == "cooling"
        assert body["observed"]["cooldown_until"] is not None

        raudit = client.get("/admin/audit-logs", headers=admin_headers())
        assert raudit.status_code == 200
        actions = [a["action"] for a in raudit.json()["items"]]
        assert "key.test" in actions

    with SessionLocal() as db:
        k2 = db.query(ApiKey).filter(ApiKey.id == key_id).one()
        assert k2.status == "cooling"
        assert k2.cooldown_until is not None


def test_admin_keys_reset_quota_resets_usage_and_writes_audit(tmp_path, make_app, admin_headers):
    app, secrets = _make_app(tmp_path, make_app=make_app)
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
        resp = client.post("/admin/keys/reset-quota", headers=admin_headers())
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["affected_keys"] == 1

        raudit = client.get("/admin/audit-logs?action=quota.reset", headers=admin_headers())
        assert raudit.status_code == 200
        assert len(raudit.json()["items"]) == 1
        assert raudit.json()["items"][0]["action"] == "quota.reset"
        assert raudit.json()["items"][0]["resource_type"] == "api_key"

    with SessionLocal() as db:
        k = db.query(ApiKey).one()
        assert k.daily_usage == 0
        assert k.status == "active"


def test_admin_clients_update_writes_audit(tmp_path, make_app, admin_headers):
    app, _ = _make_app(tmp_path, make_app=make_app)

    with TestClient(app) as client:
        r1 = client.post(
            "/admin/clients",
            headers=admin_headers(),
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
            headers=admin_headers(),
            json={"max_concurrent": 20, "is_active": False},
        )
        assert rup.status_code == 200
        assert rup.json()["max_concurrent"] == 20
        assert rup.json()["is_active"] is False

        raudit = client.get("/admin/audit-logs?action=client.update", headers=admin_headers())
        assert raudit.status_code == 200
        assert len(raudit.json()["items"]) == 1
        assert raudit.json()["items"][0]["action"] == "client.update"
        assert raudit.json()["items"][0]["resource_type"] == "client"


def test_admin_audit_logs_pagination_and_filters(tmp_path, make_app, admin_headers):
    app, _ = _make_app(tmp_path, make_app=make_app)

    with TestClient(app) as client:
        c = client.post(
            "/admin/clients",
            headers=admin_headers(),
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

        rrot = client.post(f"/admin/clients/{client_id}/rotate", headers=admin_headers())
        assert rrot.status_code == 200

        page1 = client.get("/admin/audit-logs?limit=1", headers=admin_headers())
        assert page1.status_code == 200
        assert len(page1.json()["items"]) == 1
        assert page1.json()["has_more"] is True

        cursor = page1.json()["next_cursor"]
        assert cursor is not None

        page2 = client.get(f"/admin/audit-logs?limit=50&cursor={cursor}", headers=admin_headers())
        assert page2.status_code == 200
        assert len(page2.json()["items"]) >= 1

        filtered = client.get("/admin/audit-logs?action=client.rotate", headers=admin_headers())
        assert filtered.status_code == 200
        assert len(filtered.json()["items"]) == 1
        assert filtered.json()["items"][0]["action"] == "client.rotate"


def test_admin_keys_purge_deletes_key_and_nulls_request_logs(tmp_path, make_app, admin_headers):
    app, _ = _make_app(tmp_path, make_app=make_app)

    with TestClient(app) as client:
        r1 = client.post(
            "/admin/keys",
            headers=admin_headers(),
            json={
                "api_key": "fc-xxxxxxxxxxxxxxxx0001",
                "name": "k1",
                "plan_type": "free",
                "daily_quota": 5,
                "max_concurrent": 2,
                "rate_limit_per_min": 10,
                "is_active": True,
            },
        )
        assert r1.status_code == 201
        key_id = r1.json()["id"]

        SessionLocal = app.state.db_session_factory
        with SessionLocal() as db:
            db.add(
                RequestLog(
                    request_id="req_purge_key",
                    api_key_id=key_id,
                    endpoint="scrape",
                    method="POST",
                    status_code=200,
                    response_time_ms=1,
                    success=True,
                    retry_count=0,
                )
            )
            db.commit()

        rpurge = client.delete(f"/admin/keys/{key_id}/purge", headers=admin_headers())
        assert rpurge.status_code == 204

        with SessionLocal() as db:
            assert db.query(ApiKey).filter(ApiKey.id == key_id).one_or_none() is None
            log = db.query(RequestLog).filter(RequestLog.request_id == "req_purge_key").one()
            assert log.api_key_id is None

        raudit = client.get("/admin/audit-logs?action=key.purge", headers=admin_headers())
        assert raudit.status_code == 200
        assert len(raudit.json()["items"]) == 1


def test_admin_clients_purge_deletes_client_and_clears_related_rows(
    tmp_path, make_app, admin_headers
):
    app, _ = _make_app(tmp_path, make_app=make_app)

    with TestClient(app) as client:
        r1 = client.post(
            "/admin/clients",
            headers=admin_headers(),
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

        SessionLocal = app.state.db_session_factory
        with SessionLocal() as db:
            db.add(
                RequestLog(
                    request_id="req_purge_client",
                    client_id=client_id,
                    endpoint="scrape",
                    method="POST",
                    status_code=200,
                    response_time_ms=1,
                    success=True,
                    retry_count=0,
                )
            )
            db.add(
                IdempotencyRecord(
                    client_id=client_id,
                    idempotency_key="idem_1",
                    request_hash="h" * 64,
                    status="completed",
                )
            )
            db.commit()

        rpurge = client.delete(f"/admin/clients/{client_id}/purge", headers=admin_headers())
        assert rpurge.status_code == 204

        with SessionLocal() as db:
            assert db.query(Client).filter(Client.id == client_id).one_or_none() is None
            log = db.query(RequestLog).filter(RequestLog.request_id == "req_purge_client").one()
            assert log.client_id is None
            assert (
                db.query(IdempotencyRecord).filter(IdempotencyRecord.client_id == client_id).count()
                == 0
            )

        raudit = client.get("/admin/audit-logs?action=client.purge", headers=admin_headers())
        assert raudit.status_code == 200
        assert len(raudit.json()["items"]) == 1


def test_admin_dashboard_chart_returns_200(tmp_path, make_app, admin_headers):
    app, _ = _make_app(tmp_path, make_app=make_app)
    with TestClient(app) as client:
        r = client.get(
            "/admin/dashboard/chart",
            headers=admin_headers(),
            params={"tz": "UTC", "range": "24h", "bucket": "hour"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["range"] == "24h"
    assert body["bucket"] == "hour"
    assert body["tz"] == "UTC"
    assert len(body["labels"]) == 24
    assert len(body["datasets"]) == 2
    assert all(len(ds["data"]) == 24 for ds in body["datasets"])


def test_admin_dashboard_stats_ignores_logs_without_client_id_by_default(
    tmp_path, make_app, admin_headers
):
    app, secrets = _make_app(tmp_path, make_app=make_app)
    SessionLocal = app.state.db_session_factory

    with SessionLocal() as db:
        key_bytes = derive_master_key_bytes(secrets.master_key)
        today = today_in_timezone("UTC")
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
        db.add(c)
        db.commit()
        db.refresh(c)

        now = datetime.now(timezone.utc)
        db.add(
            RequestLog(
                request_id="req_unauth",
                client_id=None,
                api_key_id=None,
                endpoint="crawl_status",
                method="GET",
                status_code=401,
                response_time_ms=1,
                success=False,
                retry_count=0,
                error_message="CLIENT_UNAUTHORIZED",
                idempotency_key=None,
                created_at=now,
            )
        )
        db.add(
            RequestLog(
                request_id="req_ok",
                client_id=c.id,
                api_key_id=None,
                endpoint="scrape",
                method="POST",
                status_code=200,
                response_time_ms=2,
                success=True,
                retry_count=0,
                error_message=None,
                idempotency_key=None,
                created_at=now,
            )
        )
        db.commit()

    with TestClient(app) as client:
        rstats = client.get("/admin/dashboard/stats", headers=admin_headers())
        assert rstats.status_code == 200
        assert rstats.json()["requests_24h"]["total"] == 1
        assert rstats.json()["requests_24h"]["failed"] == 0

        rchart = client.get(
            "/admin/dashboard/chart",
            headers=admin_headers(),
            params={"tz": "UTC", "range": "24h", "bucket": "hour"},
        )
        assert rchart.status_code == 200
        total_in_chart = sum(sum(ds["data"]) for ds in rchart.json()["datasets"])
        assert total_in_chart == 1
