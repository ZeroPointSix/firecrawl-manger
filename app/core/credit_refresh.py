from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import AppConfig
from app.db.models import ApiKey, CreditSnapshot

logger = logging.getLogger(__name__)


def calculate_next_refresh_time(key: ApiKey, config: AppConfig) -> datetime:
    """
    根据额度情况计算下次刷新时间。

    单元测试约束（见 tests/unit/test_credit_refresh.py）：
    - 使用率 > 90%（剩余 < 10%）：15 分钟
    - 使用率 > 70%（剩余 10%-30%）：30 分钟
    - 使用率 > 50%（剩余 30%-50%）：60 分钟
    - 其他（剩余 >= 50%）：120 分钟
    - plan=0：使用 fixed_refresh.interval_minutes
    - 缓存未初始化：立即刷新（now）
    - remaining=0 且 plan>0：等待到下个月 1 号
    """
    now = datetime.now(timezone.utc)

    remaining = key.cached_remaining_credits
    plan = key.cached_plan_credits
    if remaining is None or plan is None:
        return now

    plan_i = int(plan)
    remaining_i = int(remaining)

    if plan_i == 0:
        return now + timedelta(minutes=int(config.credit_monitoring.fixed_refresh.interval_minutes))

    if remaining_i == 0:
        next_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0) + timedelta(days=32)
        return next_month.replace(day=1)

    usage_ratio = 1 - (remaining_i / plan_i)

    if not config.credit_monitoring.smart_refresh.enabled:
        interval_minutes = int(config.credit_monitoring.fixed_refresh.interval_minutes)
    else:
        if usage_ratio > 0.9:
            interval_minutes = int(config.credit_monitoring.smart_refresh.high_usage_interval)
        elif usage_ratio > 0.7:
            interval_minutes = int(config.credit_monitoring.smart_refresh.medium_usage_interval)
        elif usage_ratio > 0.5:
            interval_minutes = int(config.credit_monitoring.smart_refresh.normal_usage_interval)
        else:
            interval_minutes = int(config.credit_monitoring.smart_refresh.low_usage_interval)

    return now + timedelta(minutes=interval_minutes)


async def credit_refresh_loop(
    *,
    db_factory: Callable[[], Session],
    master_key: bytes,
    config: AppConfig,
    stop_event: asyncio.Event,
) -> None:
    """
    后台额度刷新循环：查找到期 Key -> 调用上游 -> 更新缓存与 next_refresh_at -> 清理快照。
    """
    logger.info("credit.refresh_loop_started")

    while not stop_event.is_set():
        try:
            await _refresh_once(db_factory=db_factory, master_key=master_key, config=config)
        except Exception:
            logger.exception("credit.refresh_loop_failed")

        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=max(int(config.credit_monitoring.refresh_check_interval_seconds), 1),
            )
        except asyncio.TimeoutError:
            continue

    logger.info("credit.refresh_loop_stopped")


async def _refresh_once(*, db_factory: Callable[[], Session], master_key: bytes, config: AppConfig) -> None:
    from app.core.credit_fetcher import fetch_credit_from_firecrawl

    db = db_factory()
    try:
        now = datetime.now(timezone.utc)

        keys = (
            db.query(ApiKey)
            .filter(
                ApiKey.is_active.is_(True),
                ApiKey.status.in_(["active", "cooling"]),
                or_(ApiKey.next_refresh_at.is_(None), ApiKey.next_refresh_at <= now),
            )
            .order_by(ApiKey.id.asc())
            .all()
        )
        if not keys:
            return

        batch_size = max(int(config.credit_monitoring.batch_size), 1)
        batch_delay = max(int(config.credit_monitoring.batch_delay_seconds), 0)
        retry_delay_minutes = max(int(config.credit_monitoring.retry_delay_minutes), 1)

        for i in range(0, len(keys), batch_size):
            batch = keys[i : i + batch_size]
            for key in batch:
                request_id = f"credit-refresh-{key.id}-{int(now.timestamp())}"
                try:
                    await fetch_credit_from_firecrawl(
                        db=db,
                        key=key,
                        master_key=master_key,
                        config=config,
                        request_id=request_id,
                    )
                    db.refresh(key)
                    key.next_refresh_at = calculate_next_refresh_time(key, config)
                except Exception as exc:
                    logger.info(
                        "credit.refresh_key_failed",
                        extra={"fields": {"api_key_id": key.id, "error": str(exc)}},
                    )
                    key.next_refresh_at = now + timedelta(minutes=retry_delay_minutes)

            try:
                db.commit()
            except Exception:
                db.rollback()
                logger.exception("credit.refresh_batch_commit_failed")

            if batch_delay and i + batch_size < len(keys):
                await asyncio.sleep(batch_delay)

        await cleanup_old_snapshots(db=db, config=config)
    finally:
        db.close()


async def cleanup_old_snapshots(*, db: Session, config: AppConfig) -> None:
    retention_days = int(config.credit_monitoring.retention_days)
    if retention_days <= 0:
        return

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

    try:
        deleted = db.query(CreditSnapshot).filter(CreditSnapshot.snapshot_at < cutoff).delete()
        if deleted:
            db.commit()
            logger.info("credit.snapshots_cleaned", extra={"fields": {"deleted_count": int(deleted)}})
    except Exception:
        db.rollback()
        logger.exception("credit.snapshots_cleanup_failed")
