from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.responses import Response
from sqlalchemy.exc import IntegrityError

from app.config import AppConfig
from app.core.idempotency import (
    IdempotencyContext,
    _cleanup_expired,
    _deserialize_response,
    _serialize_response,
    complete,
    start_or_replay,
)
from app.db.models import Base, Client, IdempotencyRecord
from app.db.session import create_engine_from_config, create_session_factory
from app.errors import FcamError

pytestmark = pytest.mark.unit


def _setup_db(tmp_path) -> Any:
    config = AppConfig()
    config.database.path = (tmp_path / "idem.db").as_posix()
    engine = create_engine_from_config(config)
    Base.metadata.create_all(engine)
    return create_session_factory(engine)


def test_serialize_response_rejects_large_body():
    resp = Response(content=b"x" * 10, status_code=200, headers={"content-type": "application/json"})
    with pytest.raises(FcamError) as exc:
        _serialize_response(response=resp, max_bytes=5)
    assert exc.value.status_code == 413
    assert exc.value.code == "REQUEST_TOO_LARGE"


@pytest.mark.parametrize(
    ("status_code", "body"),
    [
        (200, "{}"),  # missing version marker
        (200, '{"v":1,"headers":"oops","body_b64":"AA=="}'),  # invalid headers type
        (200, "not-json"),
    ],
)
def test_deserialize_response_rejects_invalid_record(status_code: int, body: str):
    with pytest.raises(FcamError) as exc:
        _deserialize_response(status_code=status_code, response_body=body)
    assert exc.value.status_code == 503
    assert exc.value.code == "DB_UNAVAILABLE"


def test_start_or_replay_returns_none_when_idempotency_disabled():
    config = AppConfig()
    config.idempotency.enabled = False

    ctx, resp = start_or_replay(
        db=object(),  # not used when disabled
        config=config,
        client_id=1,
        idempotency_key="idem_1",
        endpoint="scrape",
        method="POST",
        payload={"url": "https://example.com"},
    )
    assert ctx is None
    assert resp is None


def test_cleanup_expired_swallows_db_errors():
    class _BrokenSession:
        rolled_back = False

        def query(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return self

        def filter(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return self

        def delete(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return 1

        def commit(self) -> None:
            raise RuntimeError("boom")

        def rollback(self) -> None:
            self.rolled_back = True

    db = _BrokenSession()
    _cleanup_expired(db, client_id=1, now=datetime.now(timezone.utc))
    assert db.rolled_back is True


def test_start_or_replay_raises_db_unavailable_when_integrity_error_without_existing_record(tmp_path):
    class _BrokenSession:
        def add(self, record: object) -> None:
            return

        def commit(self) -> None:
            raise IntegrityError("stmt", {}, Exception("orig"))

        def refresh(self, record: object) -> None:
            return

        def rollback(self) -> None:
            return

        def query(self, model: object):  # noqa: ANN001
            class _Q:
                def filter(self, *args, **kwargs):  # noqa: ANN002, ANN003
                    return self

                def one_or_none(self):  # noqa: ANN001
                    return None

            return _Q()

    config = AppConfig()
    config.idempotency.ttl_seconds = 1

    with pytest.raises(FcamError) as exc:
        start_or_replay(
            db=_BrokenSession(),
            config=config,
            client_id=1,
            idempotency_key="idem_1",
            endpoint="scrape",
            method="POST",
            payload={"url": "https://example.com"},
        )
    assert exc.value.status_code == 503
    assert exc.value.code == "DB_UNAVAILABLE"


def test_complete_noops_when_record_missing(tmp_path):
    SessionLocal = _setup_db(tmp_path)
    config = AppConfig()
    with SessionLocal() as db:
        complete(
            db=db,
            config=config,
            ctx=IdempotencyContext(record_id=9999),
            response=Response(content=b"ok", status_code=200),
        )


def test_complete_swallows_serialize_errors_and_keeps_record_in_progress(tmp_path):
    SessionLocal = _setup_db(tmp_path)
    config = AppConfig()
    config.idempotency.max_response_bytes = 1

    with SessionLocal() as db:
        c = Client(name="svc", token_hash="h" * 64, is_active=True, daily_quota=10, daily_usage=0)
        db.add(c)
        db.commit()
        db.refresh(c)

        rec = IdempotencyRecord(
            client_id=c.id,
            idempotency_key="idem_1",
            request_hash="h" * 64,
            status="in_progress",
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
        )
        db.add(rec)
        db.commit()
        db.refresh(rec)

        complete(
            db=db,
            config=config,
            ctx=IdempotencyContext(record_id=rec.id),
            response=Response(content=b"too-big", status_code=200),
        )

        db.refresh(rec)
        assert rec.status == "in_progress"
        assert rec.response_body is None

