from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import ApiKey

logger = logging.getLogger(__name__)


CREDIT_COST_MAP: dict[str, int] = {
    # Firecrawl v1
    "/v1/scrape": 1,
    "/v1/crawl": 5,  # 启动成本（实际通常按页/结果计费，这里用于兜底）
    "/v1/search": 1,
    "/v1/map": 1,
    # Firecrawl v2
    "/v2/scrape": 1,
    "/v2/crawl": 5,
    "/v2/map": 1,
    "/v2/extract": 2,
    "/v2/batch/scrape": 1,  # 按条目计费
}


def estimate_credit_cost(endpoint: str, response_data: dict[str, Any] | None = None) -> int:
    """
    估算一次请求消耗的 credits。

    注意：本函数用于“本地估算”，并不保证与上游严格一致；真实值以快照为准。
    """
    base_cost = CREDIT_COST_MAP.get(endpoint, 1)

    if "/crawl" in endpoint and response_data:
        pages = response_data.get("data", {}).get("total", 1)
        try:
            pages_i = int(pages)
        except Exception:
            pages_i = 1
        return max(base_cost, max(pages_i, 1))

    if "/batch/" in endpoint and response_data:
        count = response_data.get("data", {}).get("count", 1)
        try:
            count_i = int(count)
        except Exception:
            count_i = 1
        return max(base_cost * max(count_i, 1), 1)

    return base_cost


def normalize_endpoint(path: str) -> str:
    """
    将实际请求路径规范化为可匹配 CREDIT_COST_MAP 的形式。

    例：
    - /v1/scrape?url=... -> /v1/scrape
    - /v1/crawl/abc123 -> /v1/crawl
    - /v2/batch/scrape/xyz789 -> /v2/batch/scrape
    """
    if not path:
        return path

    path_no_query = path.split("?", 1)[0]
    if path_no_query in {"", "/"}:
        return path_no_query

    parts = path_no_query.split("/")
    # parts: ["", "v1", "crawl", "<id>"]
    if len(parts) >= 4 and parts[2] == "crawl":
        return "/".join(parts[:3])

    # parts: ["", "v2", "batch", "scrape", "<id>"]
    if len(parts) >= 5 and parts[2] == "batch":
        return "/".join(parts[:4])

    return path_no_query


def update_local_credits(
    *,
    db: Session,
    key: ApiKey,
    delta: int,
    endpoint: str | None = None,
    request_id: str | None = None,
) -> None:
    """
    更新 key.cached_remaining_credits（delta<0 表示消耗，delta>0 表示增加）。

    - 缓存未初始化时跳过（不抛错）
    - 结果下限为 0
    """
    if key.cached_remaining_credits is None:
        logger.info(
            "credit.local_update_skipped",
            extra={
                "fields": {
                    "request_id": request_id,
                    "api_key_id": key.id,
                    "endpoint": endpoint,
                    "reason": "cached_credits_not_initialized",
                }
            },
        )
        return

    old = int(key.cached_remaining_credits)
    new = max(0, old + int(delta))
    if new == old:
        return
    key.cached_remaining_credits = new

    try:
        db.commit()
        logger.info(
            "credit.local_updated",
            extra={
                "fields": {
                    "request_id": request_id,
                    "api_key_id": key.id,
                    "endpoint": endpoint,
                    "delta": int(delta),
                    "old_remaining": old,
                    "new_remaining": new,
                }
            },
        )
    except Exception:
        db.rollback()
        logger.exception(
            "credit.local_update_failed",
            extra={
                "fields": {
                    "request_id": request_id,
                    "api_key_id": key.id,
                    "endpoint": endpoint,
                    "delta": int(delta),
                }
            },
        )

