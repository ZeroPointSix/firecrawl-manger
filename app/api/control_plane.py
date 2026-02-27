from __future__ import annotations

import logging
import secrets as py_secrets
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo

from cryptography.exceptions import InvalidTag
from fastapi import APIRouter, Body, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_config, get_db, get_secrets, require_admin
from app.config import AppConfig, Secrets
from app.core.batch_clients import apply_batch_action_to_client, deduplicate_client_ids
from app.core.key_import import parse_keys_text
from app.core.security import (
    decrypt_api_key,
    derive_master_key_bytes,
    encrypt_account_password,
    encrypt_api_key,
    hmac_sha256_hex,
    mask_api_key_last4,
)
from app.core.time import today_in_timezone
from app.db.models import ApiKey, AuditLog, Client, CreditSnapshot, IdempotencyRecord, RequestLog
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


def _request_log_level(*, status_code: int | None, success: bool | None) -> str:
    if success is True and status_code is not None and status_code < 400:
        return "info"
    if status_code is not None and 400 <= status_code < 500:
        return "warn"
    return "error"


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

    # 计算总额度：从第一条快照获取
    cached_total_credits = None
    try:
        db = request.app.state.db_session_factory()
        first_snapshot = (
            db.query(CreditSnapshot)
            .filter(CreditSnapshot.api_key_id == key.id, CreditSnapshot.fetch_success.is_(True))
            .order_by(CreditSnapshot.snapshot_at.asc())
            .first()
        )
        if first_snapshot is not None:
            cached_total_credits = first_snapshot.remaining_credits
        elif key.cached_remaining_credits is not None:
            cached_total_credits = key.cached_remaining_credits
        db.close()
    except Exception:
        pass

    return {
        "id": key.id,
        "client_id": key.client_id,
        "name": key.name,
        "api_key_masked": mask_api_key_last4(key.api_key_last4),
        "account_username": key.account_username,
        "account_verified_at": _dt_to_rfc3339(key.account_verified_at),
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
        "last_credit_snapshot_id": key.last_credit_snapshot_id,
        "last_credit_check_at": _dt_to_rfc3339(key.last_credit_check_at),
        "cached_remaining_credits": key.cached_remaining_credits,
        "cached_plan_credits": key.cached_plan_credits,
        "cached_total_credits": cached_total_credits,
        "next_refresh_at": _dt_to_rfc3339(key.next_refresh_at),
    }


def _client_item(client: Client) -> dict[str, Any]:
    return {
        "id": client.id,
        "name": client.name,
        "is_active": client.is_active,
        "status": client.status,
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
    client_id: int | None = Field(default=None, ge=0, description="0 means unassigned")
    name: str | None = Field(default=None, max_length=255)
    plan_type: str = Field(default="free", max_length=32)
    daily_quota: int = Field(default=5, ge=0)
    max_concurrent: int = Field(default=2, ge=0)
    rate_limit_per_min: int = Field(default=10, ge=0)
    is_active: bool = True


class UpdateKeyRequest(BaseModel):
    client_id: int | None = Field(default=None, ge=0, description="0 means unassigned")
    name: str | None = Field(default=None, max_length=255)
    plan_type: str | None = Field(default=None, max_length=32)
    daily_quota: int | None = Field(default=None, ge=0)
    max_concurrent: int | None = Field(default=None, ge=0)
    rate_limit_per_min: int | None = Field(default=None, ge=0)
    is_active: bool | None = None
    api_key: str | None = Field(default=None, min_length=8, description="Rotate/replace the upstream API key")


class TestKeyRequest(BaseModel):
    mode: str = "scrape"
    test_url: str = "https://example.com"


class RefreshAllCreditsRequest(BaseModel):
    key_ids: list[int] | None = Field(default=None, description="可选，不传则刷新所有活跃 Key")
    force: bool = False


class BatchKeyPatch(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    plan_type: str | None = Field(default=None, max_length=32)
    daily_quota: int | None = Field(default=None, ge=0)
    max_concurrent: int | None = Field(default=None, ge=0)
    rate_limit_per_min: int | None = Field(default=None, ge=0)
    is_active: bool | None = None


class BatchKeyTest(BaseModel):
    mode: str = "scrape"
    test_url: str = "https://example.com"


class BatchKeysRequest(BaseModel):
    ids: list[int] = Field(..., min_length=1, max_length=200)
    patch: BatchKeyPatch | None = None
    reset_cooldown: bool = False
    soft_delete: bool = False
    test: BatchKeyTest | None = None


class ImportKeysTextRequest(BaseModel):
    client_id: int | None = Field(default=None, ge=0, description="0 means unassigned")
    text: str = Field(..., min_length=1, description="One key per line. Format: user|pass|api_key|verified_at")
    plan_type: str = Field(default="free", max_length=32)
    daily_quota: int = Field(default=5, ge=0)
    max_concurrent: int = Field(default=2, ge=0)
    rate_limit_per_min: int = Field(default=10, ge=0)
    is_active: bool = True


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


class BatchAction(str, Enum):
    ENABLE = "enable"
    DISABLE = "disable"
    DELETE = "delete"


class BatchClientRequest(BaseModel):
    client_ids: list[int] = Field(..., min_length=1, max_length=100)
    action: BatchAction


class BatchClientResponse(BaseModel):
    success_count: int
    failed_count: int
    failed_items: list[dict[str, Any]]


@router.get("/keys")
def list_keys(
    request: Request,
    db: Session = Depends(get_db),
    client_id: int | None = Query(default=None),
    page: int | None = Query(default=None, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=200),
    q_: str | None = Query(default=None, alias="q"),
) -> dict[str, Any]:
    try:
        q = db.query(ApiKey).order_by(ApiKey.id.desc())
        if client_id is not None:
            if client_id == 0:
                q = q.filter(ApiKey.client_id.is_(None))
            else:
                q = q.filter(ApiKey.client_id == client_id)
        if q_ is not None and q_.strip():
            q_raw = q_.strip()
            if len(q_raw) > 200:
                raise FcamError(status_code=400, code="VALIDATION_ERROR", message="q too long")
            pattern = f"%{q_raw.lower()}%"
            q = q.filter(func.lower(func.coalesce(ApiKey.name, "")).like(pattern))

        use_pagination = page is not None or page_size is not None or (q_ is not None and q_.strip())
        if not use_pagination:
            keys = q.all()
            snapshot_ids = [int(k.last_credit_snapshot_id) for k in keys if k.last_credit_snapshot_id]
            snapshot_by_id: dict[int, Any] = {}
            if snapshot_ids:
                rows = (
                    db.query(
                        CreditSnapshot.id,
                        CreditSnapshot.remaining_credits,
                        CreditSnapshot.billing_period_start,
                        CreditSnapshot.billing_period_end,
                    )
                    .filter(CreditSnapshot.id.in_(snapshot_ids))
                    .all()
                )
                snapshot_by_id = {int(r.id): r for r in rows}

            items: list[dict[str, Any]] = []
            for k in keys:
                item = _key_item(k, request=request)
                item["cached_is_estimated"] = False
                item["billing_period_start"] = None
                item["billing_period_end"] = None

                sid = k.last_credit_snapshot_id
                if sid is not None:
                    snap = snapshot_by_id.get(int(sid))
                    if snap is not None:
                        item["billing_period_start"] = _dt_to_rfc3339(snap.billing_period_start)
                        item["billing_period_end"] = _dt_to_rfc3339(snap.billing_period_end)
                        if k.cached_remaining_credits is not None:
                            item["cached_is_estimated"] = int(k.cached_remaining_credits) != int(
                                snap.remaining_credits
                            )

                items.append(item)
            return {"items": items}

        page_n = int(page or 1)
        page_size_n = int(page_size or 20)
        total_items = q.count()
        total_pages = (total_items + page_size_n - 1) // page_size_n if page_size_n else 0
        keys = q.offset((page_n - 1) * page_size_n).limit(page_size_n).all()
    except FcamError:
        raise
    except Exception as exc:
        logger.exception("db.keys_list_failed", extra={"fields": {"op": "keys_list"}})
        raise FcamError(status_code=503, code="DB_UNAVAILABLE", message="Database unavailable") from exc

    snapshot_ids = [int(k.last_credit_snapshot_id) for k in keys if k.last_credit_snapshot_id]
    snapshot_by_id: dict[int, Any] = {}
    if snapshot_ids:
        rows = (
            db.query(
                CreditSnapshot.id,
                CreditSnapshot.remaining_credits,
                CreditSnapshot.billing_period_start,
                CreditSnapshot.billing_period_end,
            )
            .filter(CreditSnapshot.id.in_(snapshot_ids))
            .all()
        )
        snapshot_by_id = {int(r.id): r for r in rows}

    items: list[dict[str, Any]] = []
    for k in keys:
        item = _key_item(k, request=request)
        item["cached_is_estimated"] = False
        item["billing_period_start"] = None
        item["billing_period_end"] = None

        sid = k.last_credit_snapshot_id
        if sid is not None:
            snap = snapshot_by_id.get(int(sid))
            if snap is not None:
                item["billing_period_start"] = _dt_to_rfc3339(snap.billing_period_start)
                item["billing_period_end"] = _dt_to_rfc3339(snap.billing_period_end)
                if k.cached_remaining_credits is not None:
                    item["cached_is_estimated"] = int(k.cached_remaining_credits) != int(snap.remaining_credits)

        items.append(item)

    return {
        "items": items,
        "pagination": {
            "page": page_n,
            "page_size": page_size_n,
            "total_items": total_items,
            "total_pages": total_pages,
        },
    }


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

    client_id: int | None = None
    if payload.client_id is not None:
        client_id = None if payload.client_id == 0 else int(payload.client_id)
        if client_id is not None:
            exists = db.query(Client.id).filter(Client.id == client_id).one_or_none()
            if exists is None:
                raise FcamError(status_code=404, code="NOT_FOUND", message="Client not found")

    master_key_bytes = derive_master_key_bytes(secrets.master_key)
    api_key_hash = hmac_sha256_hex(master_key_bytes, payload.api_key)
    last4 = payload.api_key[-4:]
    ciphertext = encrypt_api_key(master_key_bytes, payload.api_key)

    today = today_in_timezone(config.quota.timezone)
    status = "active" if payload.is_active else "disabled"

    key = ApiKey(
        client_id=client_id,
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


@router.post("/keys/import-text")
def import_keys_text(
    request: Request,
    payload: ImportKeysTextRequest = Body(...),
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
    secrets: Secrets = Depends(get_secrets),
) -> dict[str, Any]:
    if not secrets.master_key:
        raise FcamError(status_code=503, code="NOT_READY", message="Master key not configured")

    client_id: int | None = None
    if payload.client_id is not None:
        client_id = None if payload.client_id == 0 else int(payload.client_id)
        if client_id is not None:
            exists = db.query(Client.id).filter(Client.id == client_id).one_or_none()
            if exists is None:
                raise FcamError(status_code=404, code="NOT_FOUND", message="Client not found")

    items, parse_failures = parse_keys_text(payload.text)
    failures: list[dict[str, Any]] = [
        {"line_no": f.line_no, "raw": f.raw, "message": f.message} for f in parse_failures
    ]
    if not items and failures:
        raise FcamError(status_code=400, code="VALIDATION_ERROR", message="No valid lines to import")

    master_key_bytes = derive_master_key_bytes(secrets.master_key)
    today = today_in_timezone(config.quota.timezone)
    status = "active" if payload.is_active else "disabled"

    created = 0
    updated = 0
    skipped = 0

    for item in items:
        api_key_hash = hmac_sha256_hex(master_key_bytes, item.api_key)
        last4 = item.api_key[-4:]
        ciphertext = encrypt_api_key(master_key_bytes, item.api_key)
        pwd_cipher = (
            encrypt_account_password(master_key_bytes, item.account_password) if item.account_password else None
        )

        try:
            with db.begin_nested():
                key = ApiKey(
                    client_id=client_id,
                    api_key_ciphertext=ciphertext,
                    api_key_hash=api_key_hash,
                    api_key_last4=last4,
                    account_username=item.account_username,
                    account_password_ciphertext=pwd_cipher,
                    account_verified_at=item.account_verified_at,
                    name=None,
                    plan_type=payload.plan_type,
                    is_active=payload.is_active,
                    status=status,
                    daily_quota=payload.daily_quota,
                    daily_usage=0,
                    quota_reset_at=today,
                    max_concurrent=payload.max_concurrent,
                    rate_limit_per_min=payload.rate_limit_per_min,
                )
                db.add(key)
                db.flush()
                _audit(
                    db,
                    request=request,
                    action="key.import_text.create",
                    resource_type="api_key",
                    resource_id=str(key.id),
                )
                created += 1
        except IntegrityError:
            try:
                with db.begin_nested():
                    existing = db.query(ApiKey).filter(ApiKey.api_key_hash == api_key_hash).one_or_none()
                    if existing is None:
                        failures.append(
                            {
                                "line_no": item.line_no,
                                "raw": item.raw,
                                "message": "duplicate api key but existing record missing",
                            }
                        )
                        continue

                    changed = False

                    if client_id is not None:
                        if existing.client_id is None:
                            existing.client_id = client_id
                            changed = True
                        elif existing.client_id != client_id:
                            failures.append(
                                {
                                    "line_no": item.line_no,
                                    "raw": item.raw,
                                    "message": "api key already bound to a different client",
                                }
                            )
                            continue

                    if item.account_username and not existing.account_username:
                        existing.account_username = item.account_username
                        changed = True
                    if pwd_cipher is not None and existing.account_password_ciphertext is None:
                        existing.account_password_ciphertext = pwd_cipher
                        changed = True
                    if item.account_verified_at and existing.account_verified_at is None:
                        existing.account_verified_at = item.account_verified_at
                        changed = True

                    if changed:
                        _audit(
                            db,
                            request=request,
                            action="key.import_text.update",
                            resource_type="api_key",
                            resource_id=str(existing.id),
                        )
                        updated += 1
                    else:
                        skipped += 1
            except Exception as exc:
                failures.append({"line_no": item.line_no, "raw": item.raw, "message": str(exc)})
        except Exception as exc:
            failures.append({"line_no": item.line_no, "raw": item.raw, "message": str(exc)})

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise FcamError(status_code=503, code="DB_UNAVAILABLE", message="Database unavailable") from exc

    return {
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "failed": len(failures),
        "failures": failures,
    }


@router.put("/keys/{key_id}")
def update_key(
    request: Request,
    key_id: int,
    payload: UpdateKeyRequest = Body(...),
    db: Session = Depends(get_db),
    secrets: Secrets = Depends(get_secrets),
) -> dict[str, Any]:
    key = db.query(ApiKey).filter(ApiKey.id == key_id).one_or_none()
    if key is None:
        raise FcamError(status_code=404, code="NOT_FOUND", message="Not found")

    if payload.client_id is not None:
        if payload.client_id == 0:
            key.client_id = None
        else:
            exists = db.query(Client.id).filter(Client.id == payload.client_id).one_or_none()
            if exists is None:
                raise FcamError(status_code=404, code="NOT_FOUND", message="Client not found")
            key.client_id = payload.client_id

    if payload.api_key is not None:
        if not secrets.master_key:
            raise FcamError(status_code=503, code="NOT_READY", message="Master key not configured")
        master_key_bytes = derive_master_key_bytes(secrets.master_key)
        api_key_hash = hmac_sha256_hex(master_key_bytes, payload.api_key)
        last4 = payload.api_key[-4:]
        ciphertext = encrypt_api_key(master_key_bytes, payload.api_key)
        key.api_key_ciphertext = ciphertext
        key.api_key_hash = api_key_hash
        key.api_key_last4 = last4
        _audit(db, request=request, action="key.rotate", resource_type="api_key", resource_id=str(key.id))

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
    except IntegrityError as exc:
        db.rollback()
        raise FcamError(status_code=409, code="API_KEY_DUPLICATE", message="Duplicate api key") from exc
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


@router.delete("/keys/{key_id}/purge", status_code=204)
def purge_key(request: Request, key_id: int, db: Session = Depends(get_db)) -> Response:
    key = db.query(ApiKey).filter(ApiKey.id == key_id).one_or_none()
    if key is None:
        raise FcamError(status_code=404, code="NOT_FOUND", message="Not found")

    try:
        db.query(RequestLog).filter(RequestLog.api_key_id == key.id).update(
            {RequestLog.api_key_id: None},
            synchronize_session=False,
        )
        _audit(db, request=request, action="key.purge", resource_type="api_key", resource_id=str(key.id))
        db.delete(key)
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.exception("db.key_purge_failed", extra={"fields": {"api_key_id": key_id}})
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


@router.get("/keys/{key_id}/credits")
def get_key_credits_api(
    key_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    from app.core.credit_aggregator import get_key_credits

    try:
        result = get_key_credits(db, key_id)
    except ValueError:
        raise FcamError(status_code=404, code="KEY_NOT_FOUND", message="API Key not found") from None

    # 兼容：确保 key 列表字段一致（便于前端 table 复用）
    try:
        key = db.query(ApiKey).filter(ApiKey.id == key_id).one_or_none()
        if key is not None:
            result["key"] = _key_item(key, request=request)
    except Exception:
        pass

    return result


@router.get("/clients/{client_id}/credits")
def get_client_credits_api(client_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.core.credit_aggregator import aggregate_client_credits

    try:
        return aggregate_client_credits(db, client_id)
    except ValueError as exc:
        raise FcamError(status_code=404, code="CLIENT_NOT_FOUND", message=str(exc)) from exc


@router.get("/keys/{key_id}/credits/history")
def get_key_credits_history_api(
    key_id: int,
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
    limit: int = Query(default=100, ge=1),
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
) -> dict[str, Any]:
    key = db.query(ApiKey.id).filter(ApiKey.id == int(key_id)).one_or_none()
    if key is None:
        raise FcamError(status_code=404, code="KEY_NOT_FOUND", message="API Key not found")

    limit_n = min(int(limit), int(config.credit_monitoring.history_max_limit or 500))

    q = db.query(CreditSnapshot).filter(CreditSnapshot.api_key_id == int(key_id))
    if since is not None and since.strip():
        q = q.filter(CreditSnapshot.snapshot_at >= _parse_rfc3339(since.strip()))
    if until is not None and until.strip():
        q = q.filter(CreditSnapshot.snapshot_at <= _parse_rfc3339(until.strip()))

    total_count = q.count()
    snapshots = q.order_by(CreditSnapshot.snapshot_at.desc()).limit(limit_n).all()

    return {
        "api_key_id": int(key_id),
        "snapshots": [
            {
                "remaining_credits": s.remaining_credits,
                "plan_credits": s.plan_credits,
                "billing_period_start": _dt_to_rfc3339(s.billing_period_start),
                "billing_period_end": _dt_to_rfc3339(s.billing_period_end),
                "snapshot_at": _dt_to_rfc3339(s.snapshot_at),
                "fetch_success": s.fetch_success,
                "error_message": s.error_message,
            }
            for s in snapshots
        ],
        "total_count": int(total_count),
    }


@router.post("/keys/{key_id}/credits/refresh")
async def refresh_key_credits_api(
    key_id: int,
    request: Request,
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
    secrets: Secrets = Depends(get_secrets),
) -> dict[str, Any]:
    if not secrets.master_key:
        raise FcamError(status_code=503, code="NOT_READY", message="Master key not configured")

    key = db.query(ApiKey).filter(ApiKey.id == int(key_id)).one_or_none()
    if key is None:
        raise FcamError(status_code=404, code="KEY_NOT_FOUND", message="API Key not found")

    min_interval = int(config.credit_monitoring.min_manual_refresh_interval_seconds)
    if not min_interval:
        min_interval = 300

    now = datetime.now(timezone.utc)
    if key.last_credit_check_at is not None:
        last = key.last_credit_check_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        elapsed = (now - last).total_seconds()
        if elapsed < min_interval:
            raise FcamError(
                status_code=429,
                code="REFRESH_TOO_FREQUENT",
                message=f"Please wait {int(min_interval - elapsed)} seconds before refreshing again",
            )

    from app.core.credit_fetcher import fetch_credit_from_firecrawl

    master_key_bytes = derive_master_key_bytes(secrets.master_key)
    rid = f"manual-credit-refresh-{key_id}-{int(now.timestamp())}"
    snapshot = await fetch_credit_from_firecrawl(
        db=db,
        key=key,
        master_key=master_key_bytes,
        config=config,
        request_id=rid,
    )

    return {
        "api_key_id": key.id,
        "snapshot": {
            "remaining_credits": snapshot.remaining_credits,
            "plan_credits": snapshot.plan_credits,
            "snapshot_at": _dt_to_rfc3339(snapshot.snapshot_at),
            "fetch_success": snapshot.fetch_success,
        },
    }


@router.post("/keys/credits/refresh-all")
async def refresh_all_credits_api(
    request: Request,
    payload: RefreshAllCreditsRequest = Body(default_factory=RefreshAllCreditsRequest),
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
    secrets: Secrets = Depends(get_secrets),
) -> dict[str, Any]:
    if not secrets.master_key:
        raise FcamError(status_code=503, code="NOT_READY", message="Master key not configured")

    ids = [int(x) for x in (payload.key_ids or []) if int(x) > 0]

    q = db.query(ApiKey).filter(ApiKey.is_active.is_(True))
    if ids:
        q = q.filter(ApiKey.id.in_(ids))
    keys = q.order_by(ApiKey.id.asc()).all()

    from app.core.credit_fetcher import fetch_credit_from_firecrawl

    master_key_bytes = derive_master_key_bytes(secrets.master_key)
    now = datetime.now(timezone.utc)
    min_interval = int(config.credit_monitoring.min_manual_refresh_interval_seconds or 300)

    results: list[dict[str, Any]] = []
    success = 0
    failed = 0

    for key in keys:
        if not payload.force and key.last_credit_check_at is not None:
            last = key.last_credit_check_at
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if (now - last).total_seconds() < min_interval:
                failed += 1
                results.append(
                    {
                        "api_key_id": key.id,
                        "success": False,
                        "error": "REFRESH_TOO_FREQUENT",
                    }
                )
                continue

        rid = f"batch-credit-refresh-{key.id}-{int(now.timestamp())}"
        try:
            snapshot = await fetch_credit_from_firecrawl(
                db=db,
                key=key,
                master_key=master_key_bytes,
                config=config,
                request_id=rid,
            )
            success += 1
            results.append(
                {
                    "api_key_id": key.id,
                    "success": True,
                    "remaining_credits": snapshot.remaining_credits,
                    "plan_credits": snapshot.plan_credits,
                }
            )
        except FcamError as exc:
            failed += 1
            results.append(
                {
                    "api_key_id": key.id,
                    "success": False,
                    "error": f"{exc.code}: {exc.message}",
                }
            )
        except Exception as exc:
            failed += 1
            results.append(
                {
                    "api_key_id": key.id,
                    "success": False,
                    "error": str(exc),
                }
            )

    return {
        "total": len(keys),
        "success": int(success),
        "failed": int(failed),
        "results": results,
    }


@router.post("/keys/batch")
def batch_keys(
    request: Request,
    payload: BatchKeysRequest = Body(...),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    request_id = getattr(request.state, "request_id", "-")
    ids = [int(x) for x in payload.ids]

    results: list[dict[str, Any]] = []
    test_tasks: list[tuple[int, int]] = []

    patch_data: dict[str, Any] = {}
    if payload.patch is not None:
        patch_data = payload.patch.model_dump(exclude_unset=True)

    if payload.test is not None and payload.test.mode != "scrape":
        raise FcamError(status_code=400, code="VALIDATION_ERROR", message="Unsupported test mode")

    for key_id in ids:
        try:
            key = db.query(ApiKey).filter(ApiKey.id == key_id).one_or_none()
            if key is None:
                raise FcamError(status_code=404, code="NOT_FOUND", message="Not found")

            changed = False

            if payload.reset_cooldown:
                key.cooldown_until = None
                if key.status in {"cooling", "failed"} and key.is_active:
                    key.status = "active"
                changed = True

            if patch_data:
                if "name" in patch_data:
                    key.name = patch_data["name"]
                    changed = True
                if "plan_type" in patch_data and patch_data["plan_type"] is not None:
                    key.plan_type = patch_data["plan_type"]
                    changed = True
                if "daily_quota" in patch_data and patch_data["daily_quota"] is not None:
                    key.daily_quota = int(patch_data["daily_quota"])
                    if key.daily_quota is not None and key.daily_usage >= key.daily_quota:
                        key.status = "quota_exceeded"
                    changed = True
                if "max_concurrent" in patch_data and patch_data["max_concurrent"] is not None:
                    key.max_concurrent = int(patch_data["max_concurrent"])
                    changed = True
                if "rate_limit_per_min" in patch_data and patch_data["rate_limit_per_min"] is not None:
                    key.rate_limit_per_min = int(patch_data["rate_limit_per_min"])
                    changed = True
                if "is_active" in patch_data and patch_data["is_active"] is not None:
                    key.is_active = bool(patch_data["is_active"])
                    if not key.is_active:
                        key.status = "disabled"
                    elif key.status == "disabled":
                        key.status = "active"
                    changed = True

            if payload.soft_delete:
                key.is_active = False
                key.status = "disabled"
                changed = True

            if changed:
                _audit(
                    db,
                    request=request,
                    action="key.batch",
                    resource_type="api_key",
                    resource_id=str(key.id),
                )
                db.commit()
                db.refresh(key)

            results.append(
                {
                    "id": key_id,
                    "ok": True,
                    "key": _key_item(key, request=request),
                    "test": None,
                }
            )
            if payload.test is not None:
                test_tasks.append((key_id, len(results) - 1))
        except FcamError as exc:
            db.rollback()
            results.append({"id": key_id, "ok": False, "error": {"code": exc.code, "message": exc.message}})
        except Exception:
            db.rollback()
            logger.exception("admin.keys_batch_failed", extra={"fields": {"api_key_id": key_id}})
            results.append(
                {"id": key_id, "ok": False, "error": {"code": "INTERNAL_ERROR", "message": "Internal error"}}
            )

    if payload.test is not None and test_tasks:
        SessionLocal = request.app.state.db_session_factory
        forwarder = request.app.state.forwarder
        test_mode = payload.test.mode
        test_url = payload.test.test_url

        def _test_one(key_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
            with SessionLocal() as test_db:
                key = test_db.query(ApiKey).filter(ApiKey.id == key_id).one_or_none()
                if key is None:
                    raise FcamError(status_code=404, code="NOT_FOUND", message="Not found")

                try:
                    test_result = forwarder.test_key(
                        db=test_db,
                        request_id=request_id,
                        key=key,
                        mode=test_mode,
                        test_url=test_url,
                    )
                    _audit(
                        test_db,
                        request=request,
                        action="key.test",
                        resource_type="api_key",
                        resource_id=str(key.id),
                    )
                    test_db.commit()
                    test_db.refresh(key)
                except Exception:
                    test_db.rollback()
                    raise

                return (
                    {
                        "ok": test_result.ok,
                        "upstream_status_code": test_result.upstream_status_code,
                        "latency_ms": test_result.latency_ms,
                    },
                    _key_item(key, request=request),
                )

        max_workers = request.app.state.config.control_plane.batch_key_test_max_workers
        workers = max(1, min(int(max_workers), len(test_tasks)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_test_one, key_id): (key_id, idx) for key_id, idx in test_tasks}
            for future in as_completed(futures):
                key_id, idx = futures[future]
                try:
                    test_out, key_out = future.result()
                    results[idx]["test"] = test_out
                    results[idx]["key"] = key_out
                except FcamError as exc:
                    results[idx] = {
                        "id": key_id,
                        "ok": False,
                        "error": {"code": exc.code, "message": exc.message},
                    }
                except Exception:
                    logger.exception("admin.keys_batch_test_failed", extra={"fields": {"api_key_id": key_id}})
                    results[idx] = {
                        "id": key_id,
                        "ok": False,
                        "error": {"code": "INTERNAL_ERROR", "message": "Internal error"},
                    }

    succeeded = sum(1 for r in results if r.get("ok") is True)
    failed = len(results) - succeeded
    return {"requested": len(ids), "succeeded": succeeded, "failed": failed, "results": results}


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
        # 默认过滤掉 status="deleted" 的记录
        clients = db.query(Client).filter(Client.status != "deleted").order_by(Client.id.desc()).all()
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
        status="active" if payload.is_active else "disabled",
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
    if client is None or client.status == "deleted":
        raise FcamError(status_code=404, code="NOT_FOUND", message="Not found")

    data = payload.model_dump(exclude_unset=True)
    if "daily_quota" in data:
        client.daily_quota = data["daily_quota"]
    if "rate_limit_per_min" in data:
        client.rate_limit_per_min = data["rate_limit_per_min"]
    if "max_concurrent" in data:
        client.max_concurrent = data["max_concurrent"]
    if "is_active" in data:
        desired_active = bool(data["is_active"])
        client.is_active = desired_active
        client.status = "active" if desired_active else "disabled"

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
    if client.status != "deleted":
        client.status = "deleted"
    try:
        _audit(db, request=request, action="client.delete", resource_type="client", resource_id=str(client.id))
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.exception("db.client_delete_failed", extra={"fields": {"client_id": client_id}})
        raise FcamError(status_code=503, code="DB_UNAVAILABLE", message="Database unavailable") from exc

    return Response(status_code=204)


@router.delete("/clients/{client_id}/purge", status_code=204)
def purge_client(request: Request, client_id: int, db: Session = Depends(get_db)) -> Response:
    client = db.query(Client).filter(Client.id == client_id).one_or_none()
    if client is None:
        raise FcamError(status_code=404, code="NOT_FOUND", message="Not found")

    try:
        db.query(ApiKey).filter(ApiKey.client_id == client.id).update(
            {ApiKey.client_id: None},
            synchronize_session=False,
        )
        db.query(RequestLog).filter(RequestLog.client_id == client.id).update(
            {RequestLog.client_id: None},
            synchronize_session=False,
        )
        db.query(IdempotencyRecord).filter(IdempotencyRecord.client_id == client.id).delete(
            synchronize_session=False
        )
        _audit(db, request=request, action="client.purge", resource_type="client", resource_id=str(client.id))
        db.delete(client)
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.exception("db.client_purge_failed", extra={"fields": {"client_id": client_id}})
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


@router.patch("/clients/batch", dependencies=[Depends(require_admin)])
def batch_update_clients(
    request: Request,
    payload: BatchClientRequest,
    db: Session = Depends(get_db),
) -> BatchClientResponse:
    """批量操作 Client（启用、禁用、删除）"""

    # 去重 client_ids
    unique_client_ids = deduplicate_client_ids(payload.client_ids)

    success_count = 0
    failed_count = 0
    failed_items: list[dict[str, Any]] = []

    # 查询所有目标 Client
    clients = db.query(Client).filter(Client.id.in_(unique_client_ids)).all()
    client_map = {c.id: c for c in clients}

    # 检查不存在的 Client
    for client_id in unique_client_ids:
        if client_id not in client_map:
            failed_count += 1
            failed_items.append({
                "client_id": client_id,
                "error": "Client not found"
            })

    # 执行批量操作
    try:
        for client_id in unique_client_ids:
            if client_id not in client_map:
                continue

            client = client_map[client_id]

            try:
                apply_batch_action_to_client(client, action=payload.action.value)

                success_count += 1

                # 记录每个 Client 的审计日志
                _audit(
                    db,
                    request=request,
                    action=f"client.batch.{payload.action.value}",
                    resource_type="client",
                    resource_id=str(client.id),
                )

            except Exception as exc:
                failed_count += 1
                failed_items.append({"client_id": client_id, "error": str(exc)})

        # 提交事务
        db.commit()

    except Exception as exc:
        db.rollback()
        logger.exception("db.clients_batch_failed", extra={"fields": {"op": "clients_batch"}})
        raise FcamError(status_code=503, code="DB_UNAVAILABLE", message="Database unavailable") from exc

    return BatchClientResponse(
        success_count=success_count,
        failed_count=failed_count,
        failed_items=failed_items
    )


@router.get("/encryption-status")
def encryption_status(
    db: Session = Depends(get_db),
    secrets: Secrets = Depends(get_secrets),
) -> dict[str, Any]:
    if not secrets.master_key:
        return {
            "master_key_configured": False,
            "has_decrypt_failures": False,
            "suggestion": "Set FCAM_MASTER_KEY (must stay stable across restarts)",
        }

    master_key_bytes = derive_master_key_bytes(secrets.master_key)

    try:
        keys = db.query(ApiKey).order_by(ApiKey.id.asc()).all()
    except Exception as exc:
        logger.exception("db.keys_list_failed", extra={"fields": {"op": "keys_list"}})
        raise FcamError(status_code=503, code="DB_UNAVAILABLE", message="Database unavailable") from exc

    has_decrypt_failures = any(k.status == "decrypt_failed" for k in keys)
    if not has_decrypt_failures:
        for key in keys[:200]:
            try:
                decrypt_api_key(master_key_bytes, key.api_key_ciphertext)
            except (InvalidTag, ValueError):
                has_decrypt_failures = True
                break

    return {
        "master_key_configured": True,
        "has_decrypt_failures": has_decrypt_failures,
        "suggestion": (
            "Use the same FCAM_MASTER_KEY that was used to encrypt keys, or rotate/re-import affected keys"
            if has_decrypt_failures
            else ""
        ),
    }


@router.get("/dashboard/stats")
def dashboard_stats(
    db: Session = Depends(get_db),
    client_id: int | None = Query(default=None),
) -> dict[str, Any]:
    cutoff = datetime.utcnow() - timedelta(hours=24)

    try:
        keys_q = db.query(ApiKey)
        if client_id is not None:
            keys_q = (
                keys_q.join(Client, Client.id == ApiKey.client_id)
                .filter(ApiKey.client_id == client_id, Client.is_active.is_(True))
            )
        else:
            keys_q = (
                keys_q.outerjoin(Client, Client.id == ApiKey.client_id)
                .filter(or_(ApiKey.client_id.is_(None), Client.is_active.is_(True)))
            )

        keys_total = keys_q.count()
        keys_failed = keys_q.filter(ApiKey.status.in_(["failed", "decrypt_failed"])).count()

        clients_q = db.query(Client).filter(Client.is_active.is_(True))
        if client_id is not None:
            clients_q = clients_q.filter(Client.id == client_id)
        clients_total = clients_q.count()

        logs_q = (
            db.query(RequestLog)
            .join(Client, Client.id == RequestLog.client_id)
            .filter(RequestLog.created_at >= cutoff, Client.is_active.is_(True))
        )
        if client_id is not None:
            logs_q = logs_q.filter(RequestLog.client_id == client_id)
        # 不将未认证/无 client_id 的请求计入“业务侧请求量”

        requests_total = logs_q.count()
        requests_failed = logs_q.filter(
            (RequestLog.success.is_(False)) | (RequestLog.success.is_(None))
        ).count()

    except Exception as exc:
        logger.exception("db.dashboard_stats_failed", extra={"fields": {"op": "dashboard_stats"}})
        raise FcamError(status_code=503, code="DB_UNAVAILABLE", message="Database unavailable") from exc

    error_rate = (requests_failed / requests_total * 100.0) if requests_total else 0.0

    return {
        "keys": {"total": keys_total, "failed": keys_failed},
        "clients": {"total": clients_total},
        "requests_24h": {
            "total": requests_total,
            "failed": requests_failed,
            "error_rate": round(error_rate, 3),
        },
    }


@router.get("/dashboard/chart")
def dashboard_chart(
    db: Session = Depends(get_db),
    range_: str = Query(default="24h", alias="range"),
    bucket: str = Query(default="hour"),
    client_id: int | None = Query(default=None),
    tz: str = Query(default="UTC"),
) -> dict[str, Any]:
    if range_ != "24h":
        raise FcamError(status_code=400, code="VALIDATION_ERROR", message="Unsupported range")
    if bucket != "hour":
        raise FcamError(status_code=400, code="VALIDATION_ERROR", message="Unsupported bucket")

    try:
        tzinfo = ZoneInfo(tz)
    except Exception as exc:
        raise FcamError(status_code=400, code="VALIDATION_ERROR", message="Invalid tz") from exc

    now_tz = datetime.now(tzinfo)
    end_hour = now_tz.replace(minute=0, second=0, microsecond=0)
    start_hour = end_hour - timedelta(hours=23)

    start_utc = start_hour.astimezone(timezone.utc).replace(tzinfo=None)
    end_exclusive_utc = (end_hour + timedelta(hours=1)).astimezone(timezone.utc).replace(tzinfo=None)

    try:
        logs_q = (
            db.query(RequestLog.created_at, RequestLog.success)
            .join(Client, Client.id == RequestLog.client_id)
            .filter(
                RequestLog.created_at >= start_utc,
                RequestLog.created_at < end_exclusive_utc,
                Client.is_active.is_(True),
            )
        )
        if client_id is not None:
            logs_q = logs_q.filter(RequestLog.client_id == client_id)
        rows = logs_q.all()
    except Exception as exc:
        logger.exception("db.dashboard_chart_failed", extra={"fields": {"op": "dashboard_chart"}})
        raise FcamError(status_code=503, code="DB_UNAVAILABLE", message="Database unavailable") from exc

    success_counts = [0] * 24
    failed_counts = [0] * 24

    for created_at, success in rows:
        if created_at is None:
            continue
        dt = created_at if created_at.tzinfo is not None else created_at.replace(tzinfo=timezone.utc)
        dt_tz = dt.astimezone(tzinfo)
        dt_bucket = dt_tz.replace(minute=0, second=0, microsecond=0)
        idx = int((dt_bucket - start_hour).total_seconds() // 3600)
        if idx < 0 or idx >= 24:
            continue
        if success is True:
            success_counts[idx] += 1
        else:
            failed_counts[idx] += 1

    labels = [
        (start_hour + timedelta(hours=i)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        for i in range(24)
    ]

    return {
        "range": "24h",
        "bucket": "hour",
        "tz": tz,
        "labels": labels,
        "datasets": [
            {"label": "success", "color": "#43e97b", "data": success_counts},
            {"label": "failed", "color": "#f5576c", "data": failed_counts},
        ],
    }


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
    level: str | None = Query(default=None),
    q_: str | None = Query(default=None, alias="q"),
    request_id: str | None = Query(default=None),
    idempotency_key: str | None = Query(default=None),
) -> dict[str, Any]:
    limit_n = _limit(limit)
    q = db.query(RequestLog, ApiKey.api_key_last4).outerjoin(ApiKey, ApiKey.id == RequestLog.api_key_id)

    level_v: str | None = None
    if level is not None and level.strip():
        level_v = level.strip().lower()
        if level_v not in {"info", "warn", "error"}:
            raise FcamError(
                status_code=400, code="VALIDATION_ERROR", message="level must be one of info|warn|error"
            )

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
    if q_ is not None and q_.strip():
        q_raw = q_.strip()
        if len(q_raw) > 200:
            raise FcamError(status_code=400, code="VALIDATION_ERROR", message="q too long")

        q_text = q_raw.lower()
        pattern = f"%{q_text}%"
        q = q.filter(
            or_(
                func.lower(RequestLog.request_id).like(pattern),
                func.lower(RequestLog.endpoint).like(pattern),
                func.lower(func.coalesce(RequestLog.error_message, "")).like(pattern),
                func.lower(func.coalesce(RequestLog.error_details, "")).like(pattern),
            )
        )
    if request_id is not None:
        q = q.filter(RequestLog.request_id == request_id)
    if idempotency_key is not None:
        q = q.filter(RequestLog.idempotency_key == idempotency_key)

    if level_v == "info":
        q = q.filter(RequestLog.status_code.isnot(None), RequestLog.status_code < 400, RequestLog.success.is_(True))
    elif level_v == "warn":
        q = q.filter(RequestLog.status_code.isnot(None), RequestLog.status_code >= 400, RequestLog.status_code < 500)
    elif level_v == "error":
        q = q.filter(
            or_(
                RequestLog.status_code.is_(None),
                RequestLog.status_code >= 500,
                (RequestLog.status_code < 400) & (func.coalesce(RequestLog.success, False).is_(False)),
            )
        )

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
                "level": _request_log_level(status_code=log.status_code, success=log.success),
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
                "error_details": log.error_details,
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
