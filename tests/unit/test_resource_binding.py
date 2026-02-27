from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.config import AppConfig
from app.core.resource_binding import bind_resource, lookup_bound_key_id
from app.db.models import ApiKey, Base, Client, UpstreamResourceBinding
from app.db.session import create_engine_from_config, create_session_factory

pytestmark = pytest.mark.unit


def _setup_db(tmp_path) -> Any:
    config = AppConfig()
    config.database.path = (tmp_path / "bindings.db").as_posix()
    engine = create_engine_from_config(config)
    Base.metadata.create_all(engine)
    return create_session_factory(engine)


def _make_client_and_key(db) -> tuple[int, int]:
    c = Client(name="svc", token_hash="token_hash_1")
    db.add(c)
    db.flush()

    k = ApiKey(
        client_id=c.id,
        api_key_ciphertext=b"ciphertext",
        api_key_hash="hash_1",
        api_key_last4="0001",
    )
    db.add(k)
    db.commit()
    db.refresh(c)
    db.refresh(k)
    return int(c.id), int(k.id)


def test_bind_resource_inserts_and_lookup_returns_key_id(tmp_path):
    SessionLocal = _setup_db(tmp_path)
    with SessionLocal() as db:
        client_id, api_key_id = _make_client_and_key(db)

        bind_resource(
            db,
            client_id=client_id,
            api_key_id=api_key_id,
            resource_type="crawl",
            resource_id="job_1",
            ttl_seconds=60,
        )

        assert (
            lookup_bound_key_id(
                db,
                client_id=client_id,
                resource_type="crawl",
                resource_id="job_1",
            )
            == api_key_id
        )


def test_bind_resource_ignores_blank_resource_id(tmp_path):
    SessionLocal = _setup_db(tmp_path)
    with SessionLocal() as db:
        client_id, api_key_id = _make_client_and_key(db)

        bind_resource(
            db,
            client_id=client_id,
            api_key_id=api_key_id,
            resource_type="crawl",
            resource_id="   ",
            ttl_seconds=60,
        )

        assert db.query(UpstreamResourceBinding).count() == 0


def test_bind_resource_conflict_different_key_keeps_existing_and_logs_warning(tmp_path, caplog):
    SessionLocal = _setup_db(tmp_path)
    with SessionLocal() as db:
        client_id, k1_id = _make_client_and_key(db)

        k2 = ApiKey(
            client_id=client_id,
            api_key_ciphertext=b"ciphertext2",
            api_key_hash="hash_2",
            api_key_last4="0002",
        )
        db.add(k2)
        db.commit()
        db.refresh(k2)

        bind_resource(
            db,
            client_id=client_id,
            api_key_id=k1_id,
            resource_type="crawl",
            resource_id="job_1",
            ttl_seconds=60,
        )

        caplog.set_level("WARNING", logger="app.core.resource_binding")
        bind_resource(
            db,
            client_id=client_id,
            api_key_id=int(k2.id),
            resource_type="crawl",
            resource_id="job_1",
            ttl_seconds=60,
        )

        record = (
            db.query(UpstreamResourceBinding)
            .filter(
                UpstreamResourceBinding.client_id == client_id,
                UpstreamResourceBinding.resource_type == "crawl",
                UpstreamResourceBinding.resource_id == "job_1",
            )
            .one()
        )
        assert int(record.api_key_id) == k1_id
        assert any(r.getMessage() == "resource_binding.conflict" for r in caplog.records)


def test_bind_resource_conflict_same_key_extends_expires_at(tmp_path):
    SessionLocal = _setup_db(tmp_path)
    with SessionLocal() as db:
        client_id, api_key_id = _make_client_and_key(db)

        bind_resource(
            db,
            client_id=client_id,
            api_key_id=api_key_id,
            resource_type="crawl",
            resource_id="job_1",
            ttl_seconds=1,
        )

        r1 = (
            db.query(UpstreamResourceBinding)
            .filter(
                UpstreamResourceBinding.client_id == client_id,
                UpstreamResourceBinding.resource_type == "crawl",
                UpstreamResourceBinding.resource_id == "job_1",
            )
            .one()
        )
        assert r1.expires_at is not None
        expires_1 = r1.expires_at

        bind_resource(
            db,
            client_id=client_id,
            api_key_id=api_key_id,
            resource_type="crawl",
            resource_id="job_1",
            ttl_seconds=10,
        )

        r2 = (
            db.query(UpstreamResourceBinding)
            .filter(
                UpstreamResourceBinding.client_id == client_id,
                UpstreamResourceBinding.resource_type == "crawl",
                UpstreamResourceBinding.resource_id == "job_1",
            )
            .one()
        )
        assert r2.expires_at is not None
        assert r2.expires_at > expires_1


def test_lookup_bound_key_id_deletes_expired_record(tmp_path):
    SessionLocal = _setup_db(tmp_path)
    with SessionLocal() as db:
        client_id, api_key_id = _make_client_and_key(db)

        now = datetime.now(timezone.utc)
        db.add(
            UpstreamResourceBinding(
                client_id=client_id,
                api_key_id=api_key_id,
                resource_type="crawl",
                resource_id="job_1",
                created_at=now,
                expires_at=now - timedelta(seconds=1),
            )
        )
        db.commit()

        assert (
            lookup_bound_key_id(
                db,
                client_id=client_id,
                resource_type="crawl",
                resource_id="job_1",
            )
            is None
        )
        assert db.query(UpstreamResourceBinding).count() == 0


def test_lookup_bound_key_id_returns_none_on_db_error():
    class _BrokenSession:
        def query(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise RuntimeError("boom")

    assert (
        lookup_bound_key_id(
            _BrokenSession(),
            client_id=1,
            resource_type="crawl",
            resource_id="job_1",
        )
        is None
    )
