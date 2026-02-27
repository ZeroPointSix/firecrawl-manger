from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import UpstreamResourceBinding

logger = logging.getLogger(__name__)


def bind_resource(
    db: Session,
    *,
    client_id: int,
    api_key_id: int,
    resource_type: str,
    resource_id: str,
    ttl_seconds: int | None = None,
) -> None:
    rid = (resource_id or "").strip()
    if not rid:
        return

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=int(ttl_seconds)) if ttl_seconds else None

    record = UpstreamResourceBinding(
        client_id=int(client_id),
        api_key_id=int(api_key_id),
        resource_type=str(resource_type)[:32],
        resource_id=rid[:128],
        created_at=now,
        expires_at=expires_at,
    )

    try:
        db.add(record)
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(UpstreamResourceBinding)
            .filter(
                UpstreamResourceBinding.client_id == int(client_id),
                UpstreamResourceBinding.resource_type == str(resource_type)[:32],
                UpstreamResourceBinding.resource_id == rid[:128],
            )
            .one_or_none()
        )
        if existing is None:
            return
        if existing.api_key_id != int(api_key_id):
            logger.warning(
                "resource_binding.conflict",
                extra={
                    "fields": {
                        "client_id": client_id,
                        "resource_type": resource_type,
                        "resource_id": rid,
                        "existing_api_key_id": existing.api_key_id,
                        "new_api_key_id": api_key_id,
                    }
                },
            )
            return

        existing_expires_at = existing.expires_at
        if existing_expires_at is not None and existing_expires_at.tzinfo is None:
            existing_expires_at = existing_expires_at.replace(tzinfo=timezone.utc)

        if expires_at is not None and (
            existing_expires_at is None or existing_expires_at < expires_at
        ):
            existing.expires_at = expires_at
            try:
                db.commit()
            except Exception:
                db.rollback()
    except Exception:
        db.rollback()
        logger.exception(
            "db.resource_binding_write_failed",
            extra={
                "fields": {
                    "client_id": client_id,
                    "resource_type": resource_type,
                    "resource_id": rid,
                    "api_key_id": api_key_id,
                }
            },
        )


def lookup_bound_key_id(
    db: Session,
    *,
    client_id: int,
    resource_type: str,
    resource_id: str,
) -> int | None:
    rid = (resource_id or "").strip()
    if not rid:
        return None

    try:
        record = (
            db.query(UpstreamResourceBinding)
            .filter(
                UpstreamResourceBinding.client_id == int(client_id),
                UpstreamResourceBinding.resource_type == str(resource_type)[:32],
                UpstreamResourceBinding.resource_id == rid[:128],
            )
            .one_or_none()
        )
    except Exception:
        logger.exception(
            "db.resource_binding_lookup_failed",
            extra={"fields": {"client_id": client_id, "resource_type": resource_type, "resource_id": rid}},
        )
        return None

    if record is None:
        return None

    expires_at = record.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at is not None and expires_at < datetime.now(timezone.utc):
        try:
            db.delete(record)
            db.commit()
        except Exception:
            db.rollback()
        return None

    return int(record.api_key_id)
