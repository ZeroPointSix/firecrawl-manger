from __future__ import annotations

import logging
import secrets as py_secrets
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_config, get_db, get_secrets, require_admin
from app.config import AppConfig, Secrets
from app.core.security import (
    derive_master_key_bytes,
    encrypt_api_key,
    hmac_sha256_hex,
    mask_api_key_last4,
)
from app.core.time import today_in_timezone
from app.db.models import ApiKey, AuditLog, Client, RequestLog
from app.errors import FcamError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["control-plane"], dependencies=[Depends(require_admin)])


def _dt_to_rfc3339(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _date_to_iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _limit(limit: int) -> int:
    if limit <= 0:
        raise FcamError(status_code=400, code="VALIDATION_ERROR", message="limit must be positive")
    if limit > 200:
        raise FcamError(status_code=400, code="VALIDATION_ERROR", message="limit too large")
    return limit


def _audit(
    db: Session,
    *,
    request: Request,
    action: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    actor_type: str = "admin",
    actor_id: str | None = "admin",
) -> None:
    ip = getattr(getattr(request, "client", None), "host", None)
    user_agent = request.headers.get("user-agent")
    db.add(
        AuditLog(
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            ip=ip,
            user_agent=user_agent,
        )
    )


def _key_item(key: ApiKey, *, request: Request) -> dict[str, Any]:
    current_concurrent = request.app.state.key_concurrency.current(str(key.id))
    return {
        "id": key.id,
        "name": key.name,
        "api_key_masked": mask_api_key_last4(key.api_key_last4),
        "plan_type": key.plan_type,
        "is_active": key.is_active,
        "status": key.status,
        "daily_quota": key.daily_quota,
        "daily_usage": key.daily_usage,
        "quota_reset_at": _date_to_iso(key.quota_reset_at),
        "max_concurrent": key.max_concurrent,
        "current_concurrent": current_concurrent,
        "rate_limit_per_min": key.rate_limit_per_min,
        "cooldown_until": _dt_to_rfc3339(key.cooldown_until),
        "total_requests": key.total_requests,
        "last_used_at": _dt_to_rfc3339(key.last_used_at),
        "created_at": _dt_to_rfc3339(key.created_at),
    }


def _client_item(client: Client) -> dict[str, Any]:
    return {
        "id": client.id,
        "name": client.name,
        "is_active": client.is_active,
        "daily_quota": client.daily_quota,
        "daily_usage": client.daily_usage,
        "quota_reset_at": _date_to_iso(client.quota_reset_at),
        "rate_limit_per_min": client.rate_limit_per_min,
        "max_concurrent": client.max_concurrent,
        "created_at": _dt_to_rfc3339(client.created_at),
        "last_used_at": _dt_to_rfc3339(client.last_used_at),
    }


class CreateKeyRequest(BaseModel):
    api_key: str = Field(..., min_length=8)
    name: str | None = Field(default=None, max_length=255)
    plan_type: str = Field(default="free", max_length=32)
    daily_quota: int = Field(default=5, ge=0)
    max_concurrent: int = Field(default=2, ge=0)
    rate_limit_per_min: int = Field(default=10, ge=0)
    is_active: bool = True


class UpdateKeyRequest(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    plan_type: str | None = Field(default=None, max_length=32)
    daily_quota: int | None = Field(default=None, ge=0)
    max_concurrent: int | None = Field(default=None, ge=0)
    rate_limit_per_min: int | None = Field(default=None, ge=0)
    is_active: bool | None = None


class TestKeyRequest(BaseModel):
    mode: str = "scrape"
    test_url: str = "https://example.com"


class CreateClientRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    daily_quota: int | None = Field(default=None, ge=0)
    rate_limit_per_min: int = Field(default=60, ge=0)
    max_concurrent: int = Field(default=10, ge=0)
    is_active: bool = True


class UpdateClientRequest(BaseModel):
    daily_quota: int | None = Field(default=None, ge=0)
    rate_limit_per_min: int | None = Field(default=None, ge=0)
    max_concurrent: int | None = Field(default=None, ge=0)
    is_active: bool | None = None


@router.get("/keys")
def list_keys(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        keys = db.query(ApiKey).order_by(ApiKey.id.desc()).all()
    except Exception as exc:
        logger.exception("db.keys_list_failed", extra={"fields": {"op": "keys_list"}})
        raise FcamError(status_code=503, code="DB_UNAVAILABLE", message="Database unavailable") from exc
    return {"items": [_key_item(k, request=request) for k in keys]}


@router.post("/keys", status_code=201)
def create_key(
    request: Request,
    payload: CreateKeyRequest = Body(...),
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
    secrets: Secrets = Depends(get_secrets),
) -> dict[str, Any]:
    if not secrets.master_key:
        raise FcamError(status_code=503, code="NOT_READY", message="Master key not configured")

    master_key_bytes = derive_master_key_bytes(secrets.master_key)
    api_key_hash = hmac_sha256_hex(master_key_bytes, payload.api_key)
    last4 = payload.api_key[-4:]
    ciphertext = encrypt_api_key(master_key_bytes, payload.api_key)

    today = today_in_timezone(config.quota.timezone)
    status = "active" if payload.is_active else "disabled"

    key = ApiKey(
        api_key_ciphertext=ciphertext,
        api_key_hash=api_key_hash,
        api_key_last4=last4,
        name=payload.name,
        plan_type=payload.plan_type,
        is_active=payload.is_active,
        status=status,
        daily_quota=payload.daily_quota,
        daily_usage=0,
        quota_reset_at=today,
        max_concurrent=payload.max_concurrent,
        rate_limit_per_min=payload.rate_limit_per_min,
    )

    try:
        db.add(key)
        db.flush()
        _audit(db, request=request, action="key.create", resource_type="api_key", resource_id=str(key.id))
        db.commit()
        db.refresh(key)
    except IntegrityError as exc:
        db.rollback()
        raise FcamError(status_code=409, code="API_KEY_DUPLICATE", message="Duplicate api key") from exc
    except Exception as exc:
        db.rollback()
        logger.exception("db.key_create_failed", extra={"fields": {"op": "key_create"}})
        raise FcamError(status_code=503, code="DB_UNAVAILABLE", message="Database unavailable") from exc

    return _key_item(key, request=request)


@router.put("/keys/{key_id}")
def update_key(
    request: Request,
    key_id: int,
    payload: UpdateKeyRequest = Body(...),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    key = db.query(ApiKey).filter(ApiKey.id == key_id).one_or_none()
    if key is None:
        raise FcamError(status_code=404, code="NOT_FOUND", message="Not found")

    if payload.name is not None:
        key.name = payload.name
    if payload.plan_type is not None:
        key.plan_type = payload.plan_type
    if payload.daily_quota is not None:
        key.daily_quota = payload.daily_quota
        if key.daily_quota is not None and key.daily_usage >= key.daily_quota:
            key.status = "quota_exceeded"
    if payload.max_concurrent is not None:
        key.max_concurrent = payload.max_concurrent
    if payload.rate_limit_per_min is not None:
        key.rate_limit_per_min = payload.rate_limit_per_min
    if payload.is_active is not None:
        key.is_active = payload.is_active
        if not payload.is_active:
            key.status = "disabled"
        elif key.status == "disabled":
            key.status = "active"

    try:
        _audit(db, request=request, action="key.update", resource_type="api_key", resource_id=str(key.id))
        db.commit()
        db.refresh(key)
    except Exception as exc:
        db.rollback()
        logger.exception("db.key_update_failed", extra={"fields": {"api_key_id": key_id}})
        raise FcamError(status_code=503, code="DB_UNAVAILABLE", message="Database unavailable") from exc

    return _key_item(key, request=request)


@router.delete("/keys/{key_id}", status_code=204)
def delete_key(request: Request, key_id: int, db: Session = Depends(get_db)) -> Response:
    key = db.query(ApiKey).filter(ApiKey.id == key_id).one_or_none()
    if key is None:
        raise FcamError(status_code=404, code="NOT_FOUND", message="Not found")

    key.is_active = False
    key.status = "disabled"

    try:
        _audit(db, request=request, action="key.delete", resource_type="api_key", resource_id=str(key.id))
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.exception("db.key_delete_failed", extra={"fields": {"api_key_id": key_id}})
        raise FcamError(status_code=503, code="DB_UNAVAILABLE", message="Database unavailable") from exc

    return Response(status_code=204)


@router.post("/keys/{key_id}/test")
def test_key(
    request: Request,
    key_id: int,
    payload: TestKeyRequest = Body(default_factory=TestKeyRequest),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    key = db.query(ApiKey).filter(ApiKey.id == key_id).one_or_none()
    if key is None:
        raise FcamError(status_code=404, code="NOT_FOUND", message="Not found")

    try:
        result = request.app.state.forwarder.test_key(
            db=db,
            request_id=getattr(request.state, "request_id", "-"),
            key=key,
            mode=payload.mode,
            test_url=payload.test_url,
        )
        _audit(db, request=request, action="key.test", resource_type="api_key", resource_id=str(key.id))
        db.commit()
        db.refresh(key)
    except FcamError:
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("admin.key_test_failed", extra={"fields": {"api_key_id": key_id}})
        raise FcamError(status_code=503, code="UPSTREAM_UNAVAILABLE", message="Upstream unavailable") from exc

    return {
        "key_id": key.id,
        "ok": result.ok,
        "upstream_status_code": result.upstream_status_code,
        "latency_ms": result.latency_ms,
        "observed": {
            "cooldown_until": _dt_to_rfc3339(result.observed_cooldown_until),
            "status": result.observed_status,
        },
    }


@router.post("/keys/reset-quota")
def reset_keys_quota(
    request: Request,
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
) -> dict[str, Any]:
    today = today_in_timezone(config.quota.timezone)
    try:
        keys = db.query(ApiKey).all()
        for key in keys:
            key.daily_usage = 0
            key.quota_reset_at = today
            if key.status == "quota_exceeded" and key.is_active:
                key.status = "active"
        _audit(db, request=request, action="quota.reset", resource_type="api_key", resource_id="all")
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.exception("db.keys_quota_reset_failed", extra={"fields": {"op": "keys_reset_quota"}})
        raise FcamError(status_code=503, code="DB_UNAVAILABLE", message="Database unavailable") from exc

    reset_at = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return {"ok": True, "reset_at": _dt_to_rfc3339(reset_at), "affected_keys": len(keys)}


@router.get("/clients")
def list_clients(db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        clients = db.query(Client).order_by(Client.id.desc()).all()
    except Exception as exc:
        logger.exception("db.clients_list_failed", extra={"fields": {"op": "clients_list"}})
        raise FcamError(status_code=503, code="DB_UNAVAILABLE", message="Database unavailable") from exc
    return {"items": [_client_item(c) for c in clients]}


@router.post("/clients", status_code=201)
def create_client(
    request: Request,
    payload: CreateClientRequest = Body(...),
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
    secrets: Secrets = Depends(get_secrets),
) -> dict[str, Any]:
    if not secrets.master_key:
        raise FcamError(status_code=503, code="NOT_READY", message="Master key not configured")

    token = f"fcam_client_{py_secrets.token_urlsafe(32)}"
    master_key_bytes = derive_master_key_bytes(secrets.master_key)
    token_hash = hmac_sha256_hex(master_key_bytes, token)

    today = today_in_timezone(config.quota.timezone)
    client = Client(
        name=payload.name,
        token_hash=token_hash,
        is_active=payload.is_active,
        daily_quota=payload.daily_quota,
        daily_usage=0,
        quota_reset_at=today,
        rate_limit_per_min=payload.rate_limit_per_min,
        max_concurrent=payload.max_concurrent,
    )

    try:
        db.add(client)
        db.flush()
        _audit(db, request=request, action="client.create", resource_type="client", resource_id=str(client.id))
        db.commit()
        db.refresh(client)
    except IntegrityError as exc:
        db.rollback()
        raise FcamError(status_code=400, code="VALIDATION_ERROR", message="Client already exists") from exc
    except Exception as exc:
        db.rollback()
        logger.exception("db.client_create_failed", extra={"fields": {"op": "client_create"}})
        raise FcamError(status_code=503, code="DB_UNAVAILABLE", message="Database unavailable") from exc

    return {"client": _client_item(client), "token": token}


@router.put("/clients/{client_id}")
def update_client(
    request: Request,
    client_id: int,
    payload: UpdateClientRequest = Body(...),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    client = db.query(Client).filter(Client.id == client_id).one_or_none()
    if client is None:
        raise FcamError(status_code=404, code="NOT_FOUND", message="Not found")

    data = payload.model_dump(exclude_unset=True)
    if "daily_quota" in data:
        client.daily_quota = data["daily_quota"]
    if "rate_limit_per_min" in data:
        client.rate_limit_per_min = data["rate_limit_per_min"]
    if "max_concurrent" in data:
        client.max_concurrent = data["max_concurrent"]
    if "is_active" in data:
        client.is_active = data["is_active"]

    try:
        _audit(db, request=request, action="client.update", resource_type="client", resource_id=str(client.id))
        db.commit()
        db.refresh(client)
    except Exception as exc:
        db.rollback()
        logger.exception("db.client_update_failed", extra={"fields": {"client_id": client_id}})
        raise FcamError(status_code=503, code="DB_UNAVAILABLE", message="Database unavailable") from exc

    return _client_item(client)


@router.delete("/clients/{client_id}", status_code=204)
def delete_client(request: Request, client_id: int, db: Session = Depends(get_db)) -> Response:
    client = db.query(Client).filter(Client.id == client_id).one_or_none()
    if client is None:
        raise FcamError(status_code=404, code="NOT_FOUND", message="Not found")

    client.is_active = False
    try:
        _audit(db, request=request, action="client.delete", resource_type="client", resource_id=str(client.id))
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.exception("db.client_delete_failed", extra={"fields": {"client_id": client_id}})
        raise FcamError(status_code=503, code="DB_UNAVAILABLE", message="Database unavailable") from exc

    return Response(status_code=204)


@router.post("/clients/{client_id}/rotate")
def rotate_client_token(
    request: Request,
    client_id: int,
    db: Session = Depends(get_db),
    secrets: Secrets = Depends(get_secrets),
) -> dict[str, Any]:
    if not secrets.master_key:
        raise FcamError(status_code=503, code="NOT_READY", message="Master key not configured")

    client = db.query(Client).filter(Client.id == client_id).one_or_none()
    if client is None:
        raise FcamError(status_code=404, code="NOT_FOUND", message="Not found")

    token = f"fcam_client_{py_secrets.token_urlsafe(32)}"
    master_key_bytes = derive_master_key_bytes(secrets.master_key)
    client.token_hash = hmac_sha256_hex(master_key_bytes, token)

    try:
        _audit(db, request=request, action="client.rotate", resource_type="client", resource_id=str(client.id))
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.exception("db.client_rotate_failed", extra={"fields": {"client_id": client_id}})
        raise FcamError(status_code=503, code="DB_UNAVAILABLE", message="Database unavailable") from exc

    return {"client_id": client.id, "token": token}


@router.get("/stats")
def stats(db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        keys_total = db.query(ApiKey).count()
        clients_total = db.query(Client).count()

        keys_active = db.query(ApiKey).filter(ApiKey.is_active.is_(True), ApiKey.status == "active").count()
        keys_cooling = db.query(ApiKey).filter(ApiKey.status == "cooling").count()
        keys_quota_exceeded = db.query(ApiKey).filter(ApiKey.status == "quota_exceeded").count()
        keys_disabled = db.query(ApiKey).filter(ApiKey.is_active.is_(False) | (ApiKey.status == "disabled")).count()
        keys_failed = db.query(ApiKey).filter(ApiKey.status == "failed").count()

        clients_active = db.query(Client).filter(Client.is_active.is_(True)).count()
        clients_disabled = db.query(Client).filter(Client.is_active.is_(False)).count()
    except Exception as exc:
        logger.exception("db.stats_failed", extra={"fields": {"op": "stats"}})
        raise FcamError(status_code=503, code="DB_UNAVAILABLE", message="Database unavailable") from exc

    return {
        "keys": {
            "total": keys_total,
            "active": keys_active,
            "cooling": keys_cooling,
            "quota_exceeded": keys_quota_exceeded,
            "disabled": keys_disabled,
            "failed": keys_failed,
        },
        "clients": {"total": clients_total, "active": clients_active, "disabled": clients_disabled},
    }


@router.get("/stats/quota")
def quota_stats(
    db: Session = Depends(get_db),
    include_keys: bool = Query(default=True),
    include_clients: bool = Query(default=False),
) -> dict[str, Any]:
    try:
        keys = db.query(ApiKey).all()
        schedulable = [k for k in keys if k.is_active and k.status != "disabled"]

        total_quota = sum(int(k.daily_quota or 0) for k in schedulable)
        used_today = sum(int(k.daily_usage or 0) for k in schedulable)
        remaining = max(total_quota - used_today, 0)

        keys_exhausted = sum(1 for k in schedulable if k.status == "quota_exceeded")
        keys_available = sum(1 for k in schedulable if k.status == "active")

        body: dict[str, Any] = {
            "summary": {
                "total_quota": total_quota,
                "used_today": used_today,
                "remaining": remaining,
                "keys_exhausted": keys_exhausted,
                "keys_available": keys_available,
            }
        }

        if include_keys:
            body["keys"] = [
                {
                    "id": k.id,
                    "api_key_masked": mask_api_key_last4(k.api_key_last4),
                    "status": k.status,
                    "daily_quota": k.daily_quota,
                    "daily_usage": k.daily_usage,
                    "quota_reset_at": _date_to_iso(k.quota_reset_at),
                    "cooldown_until": _dt_to_rfc3339(k.cooldown_until),
                }
                for k in keys
            ]

        if include_clients:
            clients = db.query(Client).all()
            body["clients"] = [_client_item(c) for c in clients]

    except Exception as exc:
        logger.exception("db.quota_stats_failed", extra={"fields": {"op": "quota_stats"}})
        raise FcamError(status_code=503, code="DB_UNAVAILABLE", message="Database unavailable") from exc

    return body


def _parse_rfc3339(value: str) -> datetime:
    try:
        raw = value.replace("Z", "+00:00")
        return datetime.fromisoformat(raw)
    except Exception as exc:
        raise FcamError(status_code=400, code="VALIDATION_ERROR", message="Invalid datetime") from exc


@router.get("/logs")
def query_logs(
    db: Session = Depends(get_db),
    limit: int = Query(default=50),
    cursor: int | None = Query(default=None),
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    client_id: int | None = Query(default=None),
    api_key_id: int | None = Query(default=None),
    endpoint: str | None = Query(default=None),
    status_code: int | None = Query(default=None),
    success: bool | None = Query(default=None),
    request_id: str | None = Query(default=None),
    idempotency_key: str | None = Query(default=None),
) -> dict[str, Any]:
    limit_n = _limit(limit)
    q = db.query(RequestLog, ApiKey.api_key_last4).outerjoin(ApiKey, ApiKey.id == RequestLog.api_key_id)

    if cursor is not None:
        q = q.filter(RequestLog.id < cursor)
    if from_ is not None:
        q = q.filter(RequestLog.created_at >= _parse_rfc3339(from_))
    if to is not None:
        q = q.filter(RequestLog.created_at <= _parse_rfc3339(to))
    if client_id is not None:
        q = q.filter(RequestLog.client_id == client_id)
    if api_key_id is not None:
        q = q.filter(RequestLog.api_key_id == api_key_id)
    if endpoint is not None:
        q = q.filter(RequestLog.endpoint == endpoint)
    if status_code is not None:
        q = q.filter(RequestLog.status_code == status_code)
    if success is not None:
        q = q.filter(RequestLog.success == success)
    if request_id is not None:
        q = q.filter(RequestLog.request_id == request_id)
    if idempotency_key is not None:
        q = q.filter(RequestLog.idempotency_key == idempotency_key)

    q = q.order_by(RequestLog.id.desc())

    rows = q.limit(limit_n + 1).all()
    has_more = len(rows) > limit_n
    rows = rows[:limit_n]

    items: list[dict[str, Any]] = []
    for log, api_key_last4 in rows:
        items.append(
            {
                "id": log.id,
                "created_at": _dt_to_rfc3339(log.created_at),
                "request_id": log.request_id,
                "client_id": log.client_id,
                "api_key_id": log.api_key_id,
                "api_key_masked": mask_api_key_last4(api_key_last4 or "") if log.api_key_id else None,
                "method": log.method,
                "endpoint": log.endpoint,
                "status_code": log.status_code,
                "response_time_ms": log.response_time_ms,
                "success": log.success,
                "retry_count": log.retry_count,
                "error_message": log.error_message,
                "idempotency_key": log.idempotency_key,
            }
        )

    next_cursor = items[-1]["id"] if (has_more and items) else None
    return {"items": items, "next_cursor": next_cursor, "has_more": has_more}


@router.get("/audit-logs")
def query_audit_logs(
    db: Session = Depends(get_db),
    limit: int = Query(default=50),
    cursor: int | None = Query(default=None),
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    actor_type: str | None = Query(default=None),
    action: str | None = Query(default=None),
    resource_type: str | None = Query(default=None),
    resource_id: str | None = Query(default=None),
) -> dict[str, Any]:
    limit_n = _limit(limit)
    q = db.query(AuditLog)

    if cursor is not None:
        q = q.filter(AuditLog.id < cursor)
    if from_ is not None:
        q = q.filter(AuditLog.created_at >= _parse_rfc3339(from_))
    if to is not None:
        q = q.filter(AuditLog.created_at <= _parse_rfc3339(to))
    if actor_type is not None:
        q = q.filter(AuditLog.actor_type == actor_type)
    if action is not None:
        q = q.filter(AuditLog.action == action)
    if resource_type is not None:
        q = q.filter(AuditLog.resource_type == resource_type)
    if resource_id is not None:
        q = q.filter(AuditLog.resource_id == resource_id)

    q = q.order_by(AuditLog.id.desc())

    logs = q.limit(limit_n + 1).all()
    has_more = len(logs) > limit_n
    logs = logs[:limit_n]

    items = [
        {
            "id": a.id,
            "created_at": _dt_to_rfc3339(a.created_at),
            "actor_type": a.actor_type,
            "actor_id": a.actor_id,
            "action": a.action,
            "resource_type": a.resource_type,
            "resource_id": a.resource_id,
            "ip": a.ip,
            "user_agent": a.user_agent,
        }
        for a in logs
    ]
    next_cursor = items[-1]["id"] if (has_more and items) else None
    return {"items": items, "next_cursor": next_cursor, "has_more": has_more}
