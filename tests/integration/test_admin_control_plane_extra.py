from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig, Secrets
from app.db.models import ApiKey, Base, RequestLog
from app.main import create_app

pytestmark = pytest.mark.integration


def _make_app(tmp_path, *, db_name: str, master_key: str | None = "master"):
    config = AppConfig()
    config.database.path = (tmp_path / db_name).as_posix()
    config.firecrawl.base_url = "http://firecrawl.test/v1"
    secrets = Secrets(admin_token="admin", master_key=master_key)
    app = create_app(config=config, secrets=secrets)
    Base.metadata.create_all(app.state.db_engine)
    return app


def _admin_headers():
    return {"Authorization": "Bearer admin"}


def _create_client(client: TestClient, *, name: str) -> int:
    r = client.post(
        "/admin/clients",
        headers=_admin_headers(),
        json={"name": name, "daily_quota": 1000, "rate_limit_per_min": 60, "max_concurrent": 10, "is_active": True},
    )
    assert r.status_code == 201
    return int(r.json()["client"]["id"])


def _create_key(client: TestClient, *, api_key: str, name: str, client_id: int | None = None) -> int:
    payload: dict[str, object] = {
        "api_key": api_key,
        "name": name,
        "plan_type": "free",
        "daily_quota": 5,
        "max_concurrent": 2,
        "rate_limit_per_min": 10,
        "is_active": True,
    }
    if client_id is not None:
        payload["client_id"] = client_id
    r = client.post("/admin/keys", headers=_admin_headers(), json=payload)
    assert r.status_code == 201
    return int(r.json()["id"])


def test_admin_logs_limit_validation_and_invalid_datetime_and_q_length(tmp_path):
    app = _make_app(tmp_path, db_name="logs_validation.db")
    with TestClient(app) as client:
        r0 = client.get("/admin/logs?limit=0", headers=_admin_headers())
        assert r0.status_code == 400
        assert r0.json()["error"]["code"] == "VALIDATION_ERROR"

        rbig = client.get("/admin/logs?limit=201", headers=_admin_headers())
        assert rbig.status_code == 400
        assert rbig.json()["error"]["code"] == "VALIDATION_ERROR"

        rbad_dt = client.get("/admin/logs?from=not-a-date", headers=_admin_headers())
        assert rbad_dt.status_code == 400
        assert rbad_dt.json()["error"]["code"] == "VALIDATION_ERROR"

        rqlong = client.get("/admin/logs", headers=_admin_headers(), params={"q": "a" * 201})
        assert rqlong.status_code == 400
        assert rqlong.json()["error"]["code"] == "VALIDATION_ERROR"


def test_admin_logs_filters_include_from_to_and_level_error(tmp_path):
    app = _make_app(tmp_path, db_name="logs_filters.db")
    SessionLocal = app.state.db_session_factory

    with TestClient(app) as client:
        client_id = _create_client(client, name="svc-logs")
        key_id = _create_key(
            client,
            api_key="fc-xxxxxxxxxxxxxxxx0001",
            name="k1",
            client_id=client_id,
        )

    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        db.add(
            RequestLog(
                request_id="req_err",
                client_id=client_id,
                api_key_id=key_id,
                endpoint="scrape",
                method="POST",
                status_code=200,
                response_time_ms=1,
                success=False,
                retry_count=0,
                error_message="UPSTREAM_UNAVAILABLE",
                error_details="{}",
                idempotency_key="idem_1",
                created_at=now,
            )
        )
        db.commit()

    from_s = (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    to_s = (now + timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    with TestClient(app) as client:
        r = client.get(
            "/admin/logs",
            headers=_admin_headers(),
            params={
                "from": from_s,
                "to": to_s,
                "client_id": str(client_id),
                "api_key_id": str(key_id),
                "endpoint": "scrape",
                "status_code": "200",
                "success": "false",
                "request_id": "req_err",
                "idempotency_key": "idem_1",
                "level": "error",
            },
        )
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) == 1
        assert items[0]["level"] == "error"


def test_admin_keys_list_filters_by_client_id_and_rejects_long_q(tmp_path):
    app = _make_app(tmp_path, db_name="keys_list.db")
    with TestClient(app) as client:
        client_id = _create_client(client, name="svc-keys")

        unassigned_id = _create_key(client, api_key="fc-xxxxxxxxxxxxxxxx0001", name="u1", client_id=0)
        assigned_id = _create_key(client, api_key="fc-xxxxxxxxxxxxxxxx0002", name="a1", client_id=client_id)

        runassigned = client.get("/admin/keys?client_id=0", headers=_admin_headers())
        assert runassigned.status_code == 200
        ids = {i["id"] for i in runassigned.json()["items"]}
        assert unassigned_id in ids
        assert assigned_id not in ids

        rbound = client.get(f"/admin/keys?client_id={client_id}", headers=_admin_headers())
        assert rbound.status_code == 200
        ids2 = {i["id"] for i in rbound.json()["items"]}
        assert assigned_id in ids2
        assert unassigned_id not in ids2

        rqlong = client.get("/admin/keys", headers=_admin_headers(), params={"q": "a" * 201})
        assert rqlong.status_code == 400
        assert rqlong.json()["error"]["code"] == "VALIDATION_ERROR"


def test_admin_create_key_not_ready_without_master_key_and_404_when_client_missing(tmp_path):
    app_not_ready = _make_app(tmp_path, db_name="create_key_not_ready.db", master_key=None)
    with TestClient(app_not_ready) as client:
        r = client.post(
            "/admin/keys",
            headers=_admin_headers(),
            json={"api_key": "fc-xxxxxxxxxxxxxxxx0001"},
        )
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "NOT_READY"

    app = _make_app(tmp_path, db_name="create_key_client_missing.db", master_key="master")
    with TestClient(app) as client:
        r = client.post(
            "/admin/keys",
            headers=_admin_headers(),
            json={"api_key": "fc-xxxxxxxxxxxxxxxx0001", "client_id": 999999},
        )
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "NOT_FOUND"


def test_admin_import_text_not_ready_client_not_found_and_no_valid_lines(tmp_path):
    app_not_ready = _make_app(tmp_path, db_name="import_text_not_ready.db", master_key=None)
    with TestClient(app_not_ready) as client:
        r = client.post("/admin/keys/import-text", headers=_admin_headers(), json={"text": "fc-12345678"})
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "NOT_READY"

    app = _make_app(tmp_path, db_name="import_text_errors.db", master_key="master")
    with TestClient(app) as client:
        rbad = client.post("/admin/keys/import-text", headers=_admin_headers(), json={"text": "short"})
        assert rbad.status_code == 400
        assert rbad.json()["error"]["code"] == "VALIDATION_ERROR"

        rmissing = client.post(
            "/admin/keys/import-text",
            headers=_admin_headers(),
            json={"client_id": 999999, "text": "fc-xxxxxxxxxxxxxxxx0001"},
        )
        assert rmissing.status_code == 404
        assert rmissing.json()["error"]["code"] == "NOT_FOUND"


def test_admin_import_text_conflict_with_other_client_and_skipped_when_no_changes(tmp_path):
    app = _make_app(tmp_path, db_name="import_text_conflict.db", master_key="master")
    with TestClient(app) as client:
        c1 = _create_client(client, name="svc-a")
        c2 = _create_client(client, name="svc-b")
        _create_key(client, api_key="fc-xxxxxxxxxxxxxxxx0001", name="k1", client_id=c1)

        rconflict = client.post(
            "/admin/keys/import-text",
            headers=_admin_headers(),
            json={"client_id": c2, "text": "fc-xxxxxxxxxxxxxxxx0001"},
        )
        assert rconflict.status_code == 200
        body = rconflict.json()
        assert body["failed"] == 1
        assert body["failures"][0]["message"] == "api key already bound to a different client"

        rskip = client.post(
            "/admin/keys/import-text",
            headers=_admin_headers(),
            json={"text": "fc-xxxxxxxxxxxxxxxx0001"},
        )
        assert rskip.status_code == 200
        body2 = rskip.json()
        assert body2["skipped"] == 1
        assert body2["failed"] == 0


def test_admin_update_key_rotate_unbind_quota_and_active_transitions(tmp_path):
    app = _make_app(tmp_path, db_name="update_key.db", master_key="master")
    SessionLocal = app.state.db_session_factory

    with TestClient(app) as client:
        client_id = _create_client(client, name="svc-update-key")
        key_id = _create_key(client, api_key="fc-xxxxxxxxxxxxxxxx0001", name="k1", client_id=client_id)

        rnot_found = client.put("/admin/keys/999999", headers=_admin_headers(), json={"daily_quota": 1})
        assert rnot_found.status_code == 404
        assert rnot_found.json()["error"]["code"] == "NOT_FOUND"

        runbind = client.put(
            f"/admin/keys/{key_id}",
            headers=_admin_headers(),
            json={"client_id": 0},
        )
        assert runbind.status_code == 200
        assert runbind.json()["client_id"] is None

        rbad_client = client.put(
            f"/admin/keys/{key_id}",
            headers=_admin_headers(),
            json={"client_id": 999999},
        )
        assert rbad_client.status_code == 404
        assert rbad_client.json()["error"]["code"] == "NOT_FOUND"

        rrotate = client.put(
            f"/admin/keys/{key_id}",
            headers=_admin_headers(),
            json={"api_key": "fc-xxxxxxxxxxxxxxxx9999"},
        )
        assert rrotate.status_code == 200
        assert rrotate.json()["api_key_masked"] == "fc-****9999"

        with SessionLocal() as db:
            key = db.query(ApiKey).filter(ApiKey.id == key_id).one()
            key.daily_usage = 5
            db.commit()

        rquota = client.put(
            f"/admin/keys/{key_id}",
            headers=_admin_headers(),
            json={"daily_quota": 1},
        )
        assert rquota.status_code == 200
        assert rquota.json()["status"] == "quota_exceeded"

        rdisable = client.put(
            f"/admin/keys/{key_id}",
            headers=_admin_headers(),
            json={"is_active": False},
        )
        assert rdisable.status_code == 200
        assert rdisable.json()["status"] == "disabled"

        renable = client.put(
            f"/admin/keys/{key_id}",
            headers=_admin_headers(),
            json={"is_active": True},
        )
        assert renable.status_code == 200
        assert renable.json()["status"] == "active"

        raudit = client.get("/admin/audit-logs?action=key.rotate", headers=_admin_headers())
        assert raudit.status_code == 200
        assert len(raudit.json()["items"]) >= 1


def test_admin_key_routes_return_not_found(tmp_path):
    app = _make_app(tmp_path, db_name="key_not_found.db", master_key="master")
    with TestClient(app) as client:
        rdel = client.delete("/admin/keys/999999", headers=_admin_headers())
        assert rdel.status_code == 404

        rpurge = client.delete("/admin/keys/999999/purge", headers=_admin_headers())
        assert rpurge.status_code == 404

        rtest = client.post("/admin/keys/999999/test", headers=_admin_headers())
        assert rtest.status_code == 404


def test_admin_key_test_propagates_fcam_error(tmp_path):
    app = _make_app(tmp_path, db_name="key_test_fcam_error.db", master_key="master")
    with TestClient(app) as client:
        key_id = _create_key(client, api_key="fc-xxxxxxxxxxxxxxxx0001", name="k1", client_id=0)

        r = client.post(
            f"/admin/keys/{key_id}/test",
            headers=_admin_headers(),
            json={"mode": "crawl"},
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_admin_batch_keys_patch_updates_fields_and_soft_delete_and_invalid_test_mode(tmp_path):
    app = _make_app(tmp_path, db_name="batch_keys.db", master_key="master")
    SessionLocal = app.state.db_session_factory

    with TestClient(app) as client:
        client_id = _create_client(client, name="svc-batch")
        k1_id = _create_key(client, api_key="fc-xxxxxxxxxxxxxxxx0001", name="k1", client_id=client_id)
        k2_id = _create_key(client, api_key="fc-xxxxxxxxxxxxxxxx0002", name="k2", client_id=client_id)

        with SessionLocal() as db:
            k1 = db.query(ApiKey).filter(ApiKey.id == k1_id).one()
            k1.is_active = False
            k1.status = "disabled"
            k1.daily_usage = 0

            k2 = db.query(ApiKey).filter(ApiKey.id == k2_id).one()
            k2.is_active = True
            k2.status = "active"
            k2.daily_usage = 5
            db.commit()

        rpatch = client.post(
            "/admin/keys/batch",
            headers=_admin_headers(),
            json={
                "ids": [k1_id, k2_id],
                "patch": {
                    "name": "patched",
                    "plan_type": "pro",
                    "daily_quota": 1,
                    "max_concurrent": 7,
                    "rate_limit_per_min": 99,
                    "is_active": True,
                },
                "reset_cooldown": False,
                "soft_delete": False,
                "test": None,
            },
        )
        assert rpatch.status_code == 200
        results = {r["id"]: r for r in rpatch.json()["results"]}
        assert results[k1_id]["ok"] is True
        assert results[k1_id]["key"]["status"] == "active"
        assert results[k2_id]["ok"] is True
        assert results[k2_id]["key"]["status"] == "quota_exceeded"

        rsoft = client.post(
            "/admin/keys/batch",
            headers=_admin_headers(),
            json={"ids": [k1_id], "patch": None, "reset_cooldown": False, "soft_delete": True, "test": None},
        )
        assert rsoft.status_code == 200
        assert rsoft.json()["results"][0]["ok"] is True

        rbad_test = client.post(
            "/admin/keys/batch",
            headers=_admin_headers(),
            json={"ids": [k1_id], "patch": None, "reset_cooldown": False, "soft_delete": False, "test": {"mode": "crawl"}},
        )
        assert rbad_test.status_code == 400
        assert rbad_test.json()["error"]["code"] == "VALIDATION_ERROR"


def test_admin_client_routes_not_ready_and_not_found(tmp_path):
    app_not_ready = _make_app(tmp_path, db_name="clients_not_ready.db", master_key=None)
    with TestClient(app_not_ready) as client:
        rcreate = client.post(
            "/admin/clients",
            headers=_admin_headers(),
            json={"name": "svc", "daily_quota": 1, "rate_limit_per_min": 1, "max_concurrent": 1, "is_active": True},
        )
        assert rcreate.status_code == 503
        assert rcreate.json()["error"]["code"] == "NOT_READY"

        rrotate = client.post("/admin/clients/1/rotate", headers=_admin_headers())
        assert rrotate.status_code == 503
        assert rrotate.json()["error"]["code"] == "NOT_READY"

    app = _make_app(tmp_path, db_name="clients_not_found.db", master_key="master")
    with TestClient(app) as client:
        rup = client.put("/admin/clients/999999", headers=_admin_headers(), json={"rate_limit_per_min": 10})
        assert rup.status_code == 404

        rdel = client.delete("/admin/clients/999999", headers=_admin_headers())
        assert rdel.status_code == 404

        rpurge = client.delete("/admin/clients/999999/purge", headers=_admin_headers())
        assert rpurge.status_code == 404

        rrot_nf = client.post("/admin/clients/999999/rotate", headers=_admin_headers())
        assert rrot_nf.status_code == 404


def test_admin_encryption_status_reports_master_key_and_decrypt_failures(tmp_path):
    app_not_ready = _make_app(tmp_path, db_name="enc_status_not_ready.db", master_key=None)
    with TestClient(app_not_ready) as client:
        r = client.get("/admin/encryption-status", headers=_admin_headers())
        assert r.status_code == 200
        assert r.json()["master_key_configured"] is False

    app = _make_app(tmp_path, db_name="enc_status_fail.db", master_key="master")
    SessionLocal = app.state.db_session_factory
    with TestClient(app) as client:
        key_id = _create_key(client, api_key="fc-xxxxxxxxxxxxxxxx0001", name="k1", client_id=0)

    with SessionLocal() as db:
        key = db.query(ApiKey).filter(ApiKey.id == key_id).one()
        key.api_key_ciphertext = b"not-a-valid-aesgcm-blob"
        db.commit()

    with TestClient(app) as client:
        r2 = client.get("/admin/encryption-status", headers=_admin_headers())
        assert r2.status_code == 200
        assert r2.json()["master_key_configured"] is True
        assert r2.json()["has_decrypt_failures"] is True
        assert r2.json()["suggestion"]


def test_admin_dashboard_chart_validates_params_and_stats_support_client_filter(tmp_path):
    app = _make_app(tmp_path, db_name="dashboard_filters.db", master_key="master")
    SessionLocal = app.state.db_session_factory

    with TestClient(app) as client:
        client_id = _create_client(client, name="svc-dash")
        _create_key(client, api_key="fc-xxxxxxxxxxxxxxxx0001", name="k1", client_id=client_id)

    with SessionLocal() as db:
        db.add(
            RequestLog(
                request_id="req_ok",
                client_id=client_id,
                api_key_id=None,
                endpoint="scrape",
                method="POST",
                status_code=200,
                response_time_ms=1,
                success=True,
                retry_count=0,
                created_at=datetime.now(timezone.utc),
            )
        )
        db.commit()

    with TestClient(app) as client:
        rstats = client.get(f"/admin/dashboard/stats?client_id={client_id}", headers=_admin_headers())
        assert rstats.status_code == 200
        assert rstats.json()["requests_24h"]["total"] == 1

        rchart = client.get(
            "/admin/dashboard/chart",
            headers=_admin_headers(),
            params={"tz": "UTC", "range": "24h", "bucket": "hour", "client_id": str(client_id)},
        )
        assert rchart.status_code == 200

        rbad_range = client.get(
            "/admin/dashboard/chart",
            headers=_admin_headers(),
            params={"tz": "UTC", "range": "7d", "bucket": "hour"},
        )
        assert rbad_range.status_code == 400

        rbad_bucket = client.get(
            "/admin/dashboard/chart",
            headers=_admin_headers(),
            params={"tz": "UTC", "range": "24h", "bucket": "day"},
        )
        assert rbad_bucket.status_code == 400

        rbad_tz = client.get(
            "/admin/dashboard/chart",
            headers=_admin_headers(),
            params={"tz": "Not/AZone", "range": "24h", "bucket": "hour"},
        )
        assert rbad_tz.status_code == 400


def test_admin_quota_stats_include_clients_and_audit_log_filters(tmp_path):
    app = _make_app(tmp_path, db_name="quota_and_audit.db", master_key="master")
    with TestClient(app) as client:
        client_id = _create_client(client, name="svc-quota")
        _create_key(client, api_key="fc-xxxxxxxxxxxxxxxx0001", name="k1", client_id=client_id)

        rquota = client.get("/admin/stats/quota?include_clients=true&include_keys=false", headers=_admin_headers())
        assert rquota.status_code == 200
        assert "clients" in rquota.json()

        now = datetime.now(timezone.utc)
        from_s = (now - timedelta(days=1)).isoformat().replace("+00:00", "Z")
        to_s = (now + timedelta(days=1)).isoformat().replace("+00:00", "Z")
        raudit = client.get(
            "/admin/audit-logs",
            headers=_admin_headers(),
            params={
                "from": from_s,
                "to": to_s,
                "actor_type": "admin",
                "resource_type": "client",
                "resource_id": str(client_id),
            },
        )
        assert raudit.status_code == 200
