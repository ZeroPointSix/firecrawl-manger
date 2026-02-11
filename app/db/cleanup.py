from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.config import AppConfig
from app.db.models import AuditLog, IdempotencyRecord, RequestLog

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CleanupResult:
    request_logs_deleted: int
    audit_logs_deleted: int
    idempotency_records_deleted: int


def cleanup_retention(
    db: Session,
    *,
    config: AppConfig,
    now: datetime | None = None,
) -> CleanupResult:
    now = now or datetime.utcnow()
    request_logs_deleted = 0
    audit_logs_deleted = 0
    idempotency_deleted = 0

    try:
        request_days = int(config.observability.retention.request_logs_days)
        if request_days > 0:
            threshold = now - timedelta(days=request_days)
            request_logs_deleted = (
                db.query(RequestLog)
                .filter(RequestLog.created_at < threshold)
                .delete(synchronize_session=False)
            )

        audit_days = int(config.observability.retention.audit_logs_days)
        if audit_days > 0:
            threshold = now - timedelta(days=audit_days)
            audit_logs_deleted = (
                db.query(AuditLog)
                .filter(AuditLog.created_at < threshold)
                .delete(synchronize_session=False)
            )

        idempotency_deleted = (
            db.query(IdempotencyRecord)
            .filter(
                IdempotencyRecord.expires_at.is_not(None),
                IdempotencyRecord.expires_at < now,
            )
            .delete(synchronize_session=False)
        )

        db.commit()
        return CleanupResult(
            request_logs_deleted=int(request_logs_deleted or 0),
            audit_logs_deleted=int(audit_logs_deleted or 0),
            idempotency_records_deleted=int(idempotency_deleted or 0),
        )
    except Exception:
        db.rollback()
        logger.exception("db.cleanup_failed")
        raise
