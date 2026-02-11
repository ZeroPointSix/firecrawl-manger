from __future__ import annotations

import base64
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi.responses import Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import AppConfig
from app.db.models import IdempotencyRecord
from app.errors import FcamError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IdempotencyContext:
    record_id: int


def _hash_request(*, method: str, endpoint: str, payload: Any) -> str:
    doc = {"method": method.upper(), "endpoint": endpoint, "payload": payload}
    raw = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _serialize_response(*, response: Response, max_bytes: int) -> tuple[int, str]:
    body = getattr(response, "body", b"") or b""
    if len(body) > max_bytes:
        raise FcamError(status_code=413, code="REQUEST_TOO_LARGE", message="Response too large to store")

    headers = {k: v for k, v in (response.headers or {}).items()}
    blob = {
        "v": 1,
        "headers": headers,
        "body_b64": base64.b64encode(body).decode("ascii"),
    }
    return int(getattr(response, "status_code", 200)), json.dumps(blob, ensure_ascii=False)


def _deserialize_response(*, status_code: int, response_body: str) -> Response:
    try:
        data = json.loads(response_body)
        if not isinstance(data, dict) or data.get("v") != 1:
            raise ValueError("unsupported")
        headers = data.get("headers") or {}
        body_b64 = data.get("body_b64") or ""
        if not isinstance(headers, dict) or not isinstance(body_b64, str):
            raise ValueError("invalid")
        body = base64.b64decode(body_b64.encode("ascii"))
        safe_headers: dict[str, str] = {str(k): str(v) for k, v in headers.items()}
        return Response(content=body, status_code=int(status_code), headers=safe_headers)
    except Exception as exc:
        raise FcamError(status_code=503, code="DB_UNAVAILABLE", message="Invalid idempotency record") from exc


def _cleanup_expired(db: Session, *, client_id: int, now: datetime) -> None:
    try:
        db.query(IdempotencyRecord).filter(
            IdempotencyRecord.client_id == client_id,
            IdempotencyRecord.expires_at.is_not(None),
            IdempotencyRecord.expires_at < now,
        ).delete(synchronize_session=False)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("db.idempotency_cleanup_failed", extra={"fields": {"client_id": client_id}})


def start_or_replay(
    *,
    db: Session,
    config: AppConfig,
    client_id: int,
    idempotency_key: str | None,
    endpoint: str,
    method: str,
    payload: Any,
) -> tuple[IdempotencyContext | None, Response | None]:
    if not config.idempotency.enabled:
        return None, None

    required = endpoint in {e.strip() for e in config.idempotency.require_on if e and e.strip()}
    if required and not idempotency_key:
        raise FcamError(
            status_code=400,
            code="IDEMPOTENCY_KEY_REQUIRED",
            message="Idempotency key required",
        )

    if not idempotency_key:
        return None, None

    now = datetime.now(timezone.utc)
    _cleanup_expired(db, client_id=client_id, now=now)

    request_hash = _hash_request(method=method, endpoint=endpoint, payload=payload)
    expires_at = now + timedelta(seconds=max(int(config.idempotency.ttl_seconds), 1))

    record = IdempotencyRecord(
        client_id=client_id,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        status="in_progress",
        response_status_code=None,
        response_body=None,
        expires_at=expires_at,
    )

    try:
        db.add(record)
        db.commit()
        db.refresh(record)
        return IdempotencyContext(record_id=record.id), None
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(IdempotencyRecord)
            .filter(
                IdempotencyRecord.client_id == client_id,
                IdempotencyRecord.idempotency_key == idempotency_key,
            )
            .one_or_none()
        )
        if existing is None:
            raise FcamError(status_code=503, code="DB_UNAVAILABLE", message="Database unavailable")

        expires_at = existing.expires_at
        if expires_at and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at and expires_at < now:
            try:
                db.delete(existing)
                db.commit()
            except Exception as exc:
                db.rollback()
                raise FcamError(status_code=503, code="DB_UNAVAILABLE", message="Database unavailable") from exc
            return start_or_replay(
                db=db,
                config=config,
                client_id=client_id,
                idempotency_key=idempotency_key,
                endpoint=endpoint,
                method=method,
                payload=payload,
            )

        if existing.request_hash != request_hash:
            raise FcamError(
                status_code=409,
                code="IDEMPOTENCY_KEY_CONFLICT",
                message="Idempotency key conflict",
            )

        if existing.status == "completed" and existing.response_status_code is not None and existing.response_body:
            return None, _deserialize_response(
                status_code=int(existing.response_status_code),
                response_body=str(existing.response_body),
            )

        raise FcamError(
            status_code=409,
            code="IDEMPOTENCY_IN_PROGRESS",
            message="Idempotent request in progress",
            retry_after=1,
        )
    except FcamError:
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("db.idempotency_begin_failed", extra={"fields": {"client_id": client_id}})
        raise FcamError(status_code=503, code="DB_UNAVAILABLE", message="Database unavailable") from exc


def complete(
    *,
    db: Session,
    config: AppConfig,
    ctx: IdempotencyContext | None,
    response: Response,
) -> None:
    if ctx is None or not config.idempotency.enabled:
        return

    try:
        record = db.query(IdempotencyRecord).filter(IdempotencyRecord.id == ctx.record_id).one_or_none()
        if record is None:
            return

        status_code, body = _serialize_response(
            response=response,
            max_bytes=max(int(config.idempotency.max_response_bytes), 1),
        )
        record.status = "completed"
        record.response_status_code = status_code
        record.response_body = body
        db.commit()
    except Exception:
        db.rollback()
        logger.exception(
            "db.idempotency_complete_failed",
            extra={"fields": {"record_id": getattr(ctx, "record_id", None)}},
        )
