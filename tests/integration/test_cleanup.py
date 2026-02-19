from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.config import AppConfig
from app.db.cleanup import cleanup_retention
from app.db.models import AuditLog, Base, Client, IdempotencyRecord, RequestLog
from app.db.session import create_engine_from_config, create_session_factory

pytestmark = pytest.mark.integration


def test_cleanup_retention_deletes_expired_records(tmp_path):
    config = AppConfig()
    config.database.path = (tmp_path / "cleanup.db").as_posix()
    config.observability.retention.request_logs_days = 30
    config.observability.retention.audit_logs_days = 90

    engine = create_engine_from_config(config)
    Base.metadata.create_all(engine)
    SessionLocal = create_session_factory(engine)

    now = datetime(2026, 1, 10, 0, 0, 0)

    with SessionLocal() as db:
        client = Client(name="svc", token_hash="t" * 64, is_active=True)
        db.add(client)
        db.commit()
        db.refresh(client)

        db.add(
            RequestLog(
                request_id="r1",
                endpoint="scrape",
                method="POST",
                created_at=now - timedelta(days=31),
            )
        )
        db.add(
            RequestLog(
                request_id="r2",
                endpoint="scrape",
                method="POST",
                created_at=now - timedelta(days=5),
            )
        )

        db.add(AuditLog(actor_type="admin", action="a1", created_at=now - timedelta(days=91)))
        db.add(AuditLog(actor_type="admin", action="a2", created_at=now - timedelta(days=1)))

        db.add(
            IdempotencyRecord(
                client_id=client.id,
                idempotency_key="idem-1",
                request_hash="h" * 64,
                status="in_progress",
                expires_at=now - timedelta(seconds=1),
            )
        )
        db.add(
            IdempotencyRecord(
                client_id=client.id,
                idempotency_key="idem-2",
                request_hash="h" * 64,
                status="in_progress",
                expires_at=now + timedelta(hours=1),
            )
        )
        db.commit()

        result = cleanup_retention(db, config=config, now=now)
        assert result.request_logs_deleted == 1
        assert result.audit_logs_deleted == 1
        assert result.idempotency_records_deleted == 1

    with SessionLocal() as db:
        assert db.query(RequestLog).count() == 1
        assert db.query(AuditLog).count() == 1
        assert db.query(IdempotencyRecord).count() == 1
