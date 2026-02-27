from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models import CreditSnapshot

pytestmark = pytest.mark.integration


def _make_app(tmp_path, *, make_app):
    # credit_fetcher 直接拼接 /v2/team/credit-usage，因此这里传 root base_url
    app, config, secrets = make_app(
        tmp_path,
        db_name="credits.db",
        firecrawl_base_url="http://firecrawl.test",
        admin_token="admin",
        master_key="master",
    )
    return app, config, secrets


def _open_db(app) -> Session:
    return app.state.db_session_factory()


def test_get_key_credits_success(tmp_path, make_app, admin_headers, seed_client, seed_api_key):
    app, _config, secrets = _make_app(tmp_path, make_app=make_app)

    db = _open_db(app)
    try:
        client, _token = seed_client(db, master_key=secrets.master_key or "master")
        key = seed_api_key(
            db,
            master_key=secrets.master_key or "master",
            api_key_plain="fc-xxxxxxxxxxxxxxxx0001",
            api_key_hash="h1",
            last4="0001",
            client_id=client.id,
            cached_remaining_credits=8500,
            cached_plan_credits=10000,
            last_credit_check_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
    finally:
        db.close()

    with TestClient(app) as client_http:
        resp = client_http.get(f"/admin/keys/{key.id}/credits", headers=admin_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["api_key_id"] == key.id
        assert data["cached_credits"]["remaining_credits"] == 8500
        assert data["cached_credits"]["plan_credits"] == 10000
        assert "last_updated_at" in data["cached_credits"]


def test_get_key_credits_not_found(tmp_path, make_app, admin_headers):
    app, _config, _secrets = _make_app(tmp_path, make_app=make_app)
    with TestClient(app) as client_http:
        resp = client_http.get("/admin/keys/99999/credits", headers=admin_headers())
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "KEY_NOT_FOUND"


def test_get_client_credits_success(tmp_path, make_app, admin_headers, seed_client, seed_api_key):
    app, _config, secrets = _make_app(tmp_path, make_app=make_app)

    db = _open_db(app)
    try:
        client, _token = seed_client(db, master_key=secrets.master_key or "master", name="agg")
        client_id = client.id
        _k1 = seed_api_key(
            db,
            master_key=secrets.master_key or "master",
            api_key_plain="fc-xxxxxxxxxxxxxxxx0001",
            api_key_hash="h1",
            last4="0001",
            client_id=client_id,
            name="k1",
            cached_remaining_credits=8500,
            cached_plan_credits=10000,
            last_credit_check_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        _k2 = seed_api_key(
            db,
            master_key=secrets.master_key or "master",
            api_key_plain="fc-xxxxxxxxxxxxxxxx0002",
            api_key_hash="h2",
            last4="0002",
            client_id=client_id,
            name="k2",
            cached_remaining_credits=9000,
            cached_plan_credits=10000,
            last_credit_check_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
    finally:
        db.close()

    with TestClient(app) as client_http:
        resp = client_http.get(f"/admin/clients/{client_id}/credits", headers=admin_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["client_id"] == client_id
        assert data["total_remaining_credits"] == 17500
        assert data["total_plan_credits"] == 20000
        assert len(data["keys"]) == 2


@patch("httpx.AsyncClient.get")
def test_refresh_key_credits_success(mock_get, tmp_path, make_app, admin_headers, seed_client, seed_api_key):
    app, _config, secrets = _make_app(tmp_path, make_app=make_app)

    db = _open_db(app)
    try:
        client, _token = seed_client(db, master_key=secrets.master_key or "master")
        key = seed_api_key(
            db,
            master_key=secrets.master_key or "master",
            api_key_plain="fc-xxxxxxxxxxxxxxxx0001",
            api_key_hash="h1",
            last4="0001",
            client_id=client.id,
            name="k1",
        )
    finally:
        db.close()

    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "success": True,
        "data": {
            "remainingCredits": 9500,
            "planCredits": 10000,
            "billingPeriodStart": "2026-02-01T00:00:00Z",
            "billingPeriodEnd": "2026-03-01T00:00:00Z",
        },
    }
    mock_get.return_value = mock_resp

    with TestClient(app) as client_http:
        resp = client_http.post(f"/admin/keys/{key.id}/credits/refresh", headers=admin_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["api_key_id"] == key.id
        assert data["snapshot"]["remaining_credits"] == 9500
        assert data["snapshot"]["fetch_success"] is True


def test_refresh_key_credits_too_frequent(tmp_path, make_app, admin_headers, seed_client, seed_api_key):
    app, _config, secrets = _make_app(tmp_path, make_app=make_app)

    db = _open_db(app)
    try:
        client, _token = seed_client(db, master_key=secrets.master_key or "master")
        key = seed_api_key(
            db,
            master_key=secrets.master_key or "master",
            api_key_plain="fc-xxxxxxxxxxxxxxxx0001",
            api_key_hash="h1",
            last4="0001",
            client_id=client.id,
            name="k1",
            last_credit_check_at=datetime.now(timezone.utc) - timedelta(minutes=2),
        )
    finally:
        db.close()

    with TestClient(app) as client_http:
        resp = client_http.post(f"/admin/keys/{key.id}/credits/refresh", headers=admin_headers())
        assert resp.status_code == 429
        assert resp.json()["error"]["code"] == "REFRESH_TOO_FREQUENT"


def test_get_credits_history_success(tmp_path, make_app, admin_headers, seed_client, seed_api_key):
    app, _config, secrets = _make_app(tmp_path, make_app=make_app)

    db = _open_db(app)
    try:
        client, _token = seed_client(db, master_key=secrets.master_key or "master")
        client_id = client.id
        key = seed_api_key(
            db,
            master_key=secrets.master_key or "master",
            api_key_plain="fc-xxxxxxxxxxxxxxxx0001",
            api_key_hash="h1",
            last4="0001",
            client_id=client_id,
            name="k1",
        )
        key_id = key.id
        now = datetime.now(timezone.utc)
        for i in range(5):
            db.add(
                CreditSnapshot(
                    api_key_id=key_id,
                    remaining_credits=10000 - i * 100,
                    plan_credits=10000,
                    snapshot_at=now - timedelta(hours=i),
                    fetch_success=True,
                )
            )
        db.commit()
    finally:
        db.close()

    with TestClient(app) as client_http:
        resp = client_http.get(
            f"/admin/keys/{key_id}/credits/history",
            headers=admin_headers(),
            params={"limit": 10},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["snapshots"]) == 5
        assert data["snapshots"][0]["remaining_credits"] == 10000


@patch("httpx.AsyncClient.get")
def test_refresh_all_credits_success(mock_get, tmp_path, make_app, admin_headers, seed_client, seed_api_key):
    app, _config, secrets = _make_app(tmp_path, make_app=make_app)

    db = _open_db(app)
    try:
        client, _token = seed_client(db, master_key=secrets.master_key or "master")
        keys = [
            seed_api_key(
                db,
                master_key=secrets.master_key or "master",
                api_key_plain=f"fc-xxxxxxxxxxxxxxxx000{i}",
                api_key_hash=f"h{i}",
                last4=f"000{i}",
                client_id=client.id,
                name=f"k{i}",
            )
            for i in range(1, 4)
        ]
        key_ids = [k.id for k in keys]
    finally:
        db.close()

    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"success": True, "data": {"remainingCredits": 9000, "planCredits": 10000}}
    mock_get.return_value = mock_resp

    with TestClient(app) as client_http:
        resp = client_http.post(
            "/admin/keys/credits/refresh-all",
            headers=admin_headers(),
            json={"key_ids": key_ids},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert data["success"] == 3
