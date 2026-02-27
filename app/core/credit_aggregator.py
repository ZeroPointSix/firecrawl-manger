from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import ApiKey, Client, CreditSnapshot


def _dt_to_rfc3339(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def aggregate_client_credits(db: Session, client_id: int) -> dict[str, Any]:
    client = db.query(Client).filter(Client.id == int(client_id)).one_or_none()
    if client is None:
        raise ValueError(f"Client {client_id} not found")

    keys = (
        db.query(ApiKey)
        .filter(ApiKey.client_id == int(client_id), ApiKey.is_active.is_(True))
        .order_by(ApiKey.id.asc())
        .all()
    )

    total_remaining = 0
    total_plan = 0
    total_credits_sum = 0
    key_details: list[dict[str, Any]] = []

    for key in keys:
        remaining = int(key.cached_remaining_credits or 0)
        plan = int(key.cached_plan_credits or 0)

        # 获取该 Key 的第一条快照作为初始总额度
        first_snapshot = (
            db.query(CreditSnapshot)
            .filter(CreditSnapshot.api_key_id == key.id, CreditSnapshot.fetch_success.is_(True))
            .order_by(CreditSnapshot.snapshot_at.asc())
            .first()
        )

        total_credits = None
        if first_snapshot is not None:
            total_credits = first_snapshot.remaining_credits
        elif remaining is not None:
            total_credits = remaining

        total_remaining += remaining
        total_plan += plan
        if total_credits is not None:
            total_credits_sum += total_credits

        usage_pct = 0.0 if total_credits is None or total_credits == 0 else ((total_credits - remaining) / total_credits) * 100
        key_details.append(
            {
                "api_key_id": key.id,
                "name": key.name,
                "remaining_credits": remaining,
                "plan_credits": plan,
                "total_credits": total_credits,
                "usage_percentage": round(float(usage_pct), 2),
                "last_updated_at": _dt_to_rfc3339(key.last_credit_check_at),
            }
        )

    total_usage_pct = 0.0 if total_credits_sum == 0 else ((total_credits_sum - total_remaining) / total_credits_sum) * 100

    return {
        "client_id": client.id,
        "client_name": client.name,
        "total_remaining_credits": total_remaining,
        "total_plan_credits": total_plan,
        "total_credits": total_credits_sum,
        "usage_percentage": round(float(total_usage_pct), 2),
        "keys": key_details,
    }


def get_key_credits(db: Session, key_id: int) -> dict[str, Any]:
    key = db.query(ApiKey).filter(ApiKey.id == int(key_id)).one_or_none()
    if key is None:
        raise ValueError(f"Key {key_id} not found")

    latest_snapshot: CreditSnapshot | None = None
    if key.last_credit_snapshot_id:
        latest_snapshot = (
            db.query(CreditSnapshot).filter(CreditSnapshot.id == int(key.last_credit_snapshot_id)).one_or_none()
        )
    if latest_snapshot is None:
        latest_snapshot = (
            db.query(CreditSnapshot)
            .filter(CreditSnapshot.api_key_id == int(key.id))
            .order_by(CreditSnapshot.snapshot_at.desc())
            .first()
        )

    # 获取第一条快照（初始额度）
    first_snapshot = (
        db.query(CreditSnapshot)
        .filter(CreditSnapshot.api_key_id == int(key.id), CreditSnapshot.fetch_success.is_(True))
        .order_by(CreditSnapshot.snapshot_at.asc())
        .first()
    )

    cached_remaining = key.cached_remaining_credits
    cached_plan = key.cached_plan_credits
    is_estimated = False
    if cached_remaining is not None and latest_snapshot is not None:
        is_estimated = int(cached_remaining) != int(latest_snapshot.remaining_credits)

    # 计算总额度：使用第一条快照的 remaining_credits 作为初始总额度
    # 如果没有历史快照，使用当前的 remaining_credits
    total_credits = None
    if first_snapshot is not None:
        total_credits = first_snapshot.remaining_credits
    elif cached_remaining is not None:
        total_credits = cached_remaining

    result: dict[str, Any] = {
        "api_key_id": key.id,
        "cached_credits": {
            "remaining_credits": cached_remaining,
            "plan_credits": cached_plan,
            "total_credits": total_credits,
            "last_updated_at": _dt_to_rfc3339(key.last_credit_check_at),
            "is_estimated": is_estimated,
        },
        "latest_snapshot": None,
        "next_refresh_at": _dt_to_rfc3339(key.next_refresh_at),
    }

    if latest_snapshot is not None:
        result["latest_snapshot"] = {
            "remaining_credits": latest_snapshot.remaining_credits,
            "plan_credits": latest_snapshot.plan_credits,
            "billing_period_start": _dt_to_rfc3339(latest_snapshot.billing_period_start),
            "billing_period_end": _dt_to_rfc3339(latest_snapshot.billing_period_end),
            "snapshot_at": _dt_to_rfc3339(latest_snapshot.snapshot_at),
            "fetch_success": latest_snapshot.fetch_success,
            "error_message": latest_snapshot.error_message,
        }

    return result

