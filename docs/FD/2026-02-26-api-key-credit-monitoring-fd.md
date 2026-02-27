# FD: API Key 额度监控与展示 - 功能设计文档

## 文档信息

| 项目 | 内容 |
|-----|------|
| 文档版本 | v1.0 |
| 创建日期 | 2026-02-26 |
| 最后更新 | 2026-02-26 |
| 作者 | 开发者何夕2077 |
| 关联 PRD | `docs/PRD/2026-02-26-api-key-credit-monitoring.md` |
| 状态 | 草稿 |

## 1. 概述

### 1.1 文档目的

本文档详细描述 API Key 额度监控功能的技术实现方案，包括系统架构、数据模型、核心模块设计、接口规范、数据流等技术细节，为开发团队提供实施指导。

### 1.2 功能范围

本功能实现以下核心能力：

1. **额度数据采集**：调用 Firecrawl API 获取真实额度
2. **本地额度计算**：基于请求消耗实时估算额度
3. **智能刷新策略**：根据额度情况动态调整刷新频率
4. **Group 级别聚合**：按 Client 分组展示总额度
5. **历史追踪与趋势分析**：记录额度变化，可视化展示

### 1.3 术语定义

| 术语 | 定义 |
|-----|------|
| Credit | Firecrawl API 的额度单位，每次请求消耗一定数量的 credits |
| Snapshot | 额度快照，记录某个时间点的真实额度信息 |
| Cached Credits | 本地缓存的额度，基于真实快照 + 本地计算 |
| Smart Refresh | 智能刷新策略，根据额度情况动态调整刷新频率 |
| Group Aggregation | 按 Client 分组聚合多个 Key 的额度 |
| Estimated Value | 估算值，基于本地计算的额度（非真实值） |

### 1.4 技术栈

| 层级 | 技术 |
|-----|------|
| 后端语言 | Python 3.11+ |
| Web 框架 | FastAPI |
| ORM | SQLAlchemy |
| 数据库 | SQLite (开发) / Postgres (生产) |
| 异步任务 | asyncio |
| 前端框架 | Vue 3 + TypeScript |
| UI 组件库 | Naive UI |
| 图表库 | ECharts (待引入) |

---

## 2. 系统架构设计

### 2.1 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                         前端层 (Vue 3)                        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ Key 列表视图  │  │ Client 视图  │  │ 趋势图组件   │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
└─────────────────────────────────────────────────────────────┘
                            │ HTTP API
┌─────────────────────────────────────────────────────────────┐
│                      控制面 API 层 (FastAPI)                  │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  GET /admin/keys/{id}/credits                        │   │
│  │  GET /admin/clients/{id}/credits                     │   │
│  │  GET /admin/keys/{id}/credits/history                │   │
│  │  POST /admin/keys/{id}/credits/refresh               │   │
│  │  POST /admin/keys/credits/refresh-all                │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                            │
┌─────────────────────────────────────────────────────────────┐
│                       核心业务层                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ 额度采集模块  │  │ 本地计算模块  │  │ 智能刷新模块  │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
│  ┌─────────┐  ┌──────────────┐                        │
│  │ Group聚合模块 │  │ 历史查询模块  │                        │
│  └──────────────┘  └──────────────┘                        │
└─────────────────────────────────────────────────────────────┘
                            │
┌─────────────────────────────────────────────────────────────┐
│                      数据访问层 (SQLAlchemy)                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │  ApiKey 表   │  │ CreditSnapshot│  │ RequestLog   │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
└─────────────────────────────────────────────────────────────┘
                            │
┌─────────────────────────────────────────────────────────────┐
│                    数据库 (SQLite/Postgres)                   │
└─────────────────────────────────────────────────────────────┘
                            │
┌─────────────────────────────────────────────────────────────┐
│                   后台任务 (asyncio)                          │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  credit_refresh_loop()  - 智能刷新循环                │   │
│  │  cleanup_snapshots() - 清理过期快照               │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                            │
┌─────────────────────────────────────────────────────────────┐
│                  外部服务 (Firecrawl API)                     │
│  GET /v2/team/credit-usage                                  │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 模块职责

| 模块 | 职责 | 输入 | 输出 |
|-----|------|------|------|
| 额度采集模块 | 调用 Firecrawl API 获取真实额度 | ApiKey 对象 | CreditSnapshot |
| 本地计算模块 | 基于请求消耗更新本、响应数据 | 更新后的 cached_credits |
| 智能刷新模块 | 计算下次刷新时间，触发刷新任务 | ApiKey 对象 | next_refresh_at |
| Group 聚合模块 | 聚合 Client 下所有 Key 的额度 | Client ID | 聚合后的额度信息 |
| 历史查询模块 | 查询额度历史快照 | Key ID, 时间范围 | CreditSnapshot 列表 |

### 2.3 数据流设计

#### 2.3.1 额度刷新流程

```
┌─────────────┐
│ 后台任务启动 │
└──────┬──────┘
       │
       ▼
┌─────────────────────┐
│ 查询需要刷新的 Key   │ (next_refresh_at <= now)
└──────┬──────────────┘
       │
       ▼
┌─────────────────────┐
│ 分批处理 (batch_size)│
└──────┬──────────────┘
       │
       ▼
┌─────────────────────────────────┐
│ 调用 Firecrawl API 获取真实额度  │
└──────┬───────────────┘
       │
       ├─ 成功 ──────────────────────┐
       │                             ▼
       │                    ┌──────────────────┐
       │                    │ 创建 Snapshot    │
       │                    │ 更新 cached_*    │
       │                    │ 计算 next_refresh│
       │                    └──────────────────┘
       │
       └─ 失败 ──────────────────────┐
                                     ▼
                            ┌──────────────────┐
                            │ 记录失败 Snapshot│
                            │ 延迟重试         │
                            └──────────────────┘
```

#### 2.3.2 请求处理流程（含本地计算）

```
┌─────────────┐
│ 客户端请求   │
└──────┬──────┘
       │
       ▼
┌─────────────────────┐
│ forwarder 转发请求  │
└──────┬──────────────┘
       │
       ▼
┌─────────────────────┐
│ 上游 API 返回响应   │
└──────┬──────────────┘
       │
       ├─ 成功 (2xx) ────────────────┐
       │                             ▼
       │                    ┌──────────────────────┐
       │                    │ 估算 credits 消耗    │
       │                    │ (estimate_credit_cost)│
       │                    └──────┬───────────────┘
       │                           │
       │                           ▼
       │                    ┌──────────────────────┐
       │                    │ 更新本地缓存额度      │
       │                    │ cached_credits -= cost│
       │                    └──────────────────────┘
       │
       └─ 失败 (4xx/5xx) ────────────┐
                                     ▼
                            ┌──────────────────┐
                            │ 不更新本地额度    │
                            └──────────────────┘
```

---

## 3. 数据模型设计

### 3.1 数据库 Schema

#### 3.1.1 credit_snapshots 表

**用途**：存储额度快照历史

```sql
CREATE TABLE credit_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    api_key_id INTEGER NOT NULL,

    -- Firecrawl 返回的额度信息
    remaining_credits INTEGER NOT NULL,
    plan_credits INTEGER NOT NULL,
    billing_period_start TIMESTAMP WITH TIME ZONE,
    billing_period_end TIMESTAMP WITH TIME ZONE,

    -- 元数据
    snapshot_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    fetch_success BOOLEAN NOT NULL DEFAULT TRUE,
    error_message TEXT,

    FOREIGN KEY (api_key_id) REFERENCES api_keys(id) ON DELETE CASCADE
);

CREATE INDEX idx_credit_snapshots_api_key_id ON credit_snapshots(api_key_id);
CREATE INDEX idx_credit_snapshots_snapshot_at ON credit_snapshots(snapshot_at);
CREATE INDEX idx_credit_snapshots_fetch_success ON credit_snapshots(fetch_success);
```

**字段说明**：

| 字段 | 类型 | 说明 | 约束 |
|-----|------|------|------|
| id | INTEGER | 主键 | PRIMARY KEY |
| api_key_id | INTEGER | 关联的 API Key | NOT NULL, FK |
| remaining_credits | INTEGER | 剩余额度 | NOT NULL |
| plan_credits | INTEGER | 计划总额度 | NOT NULL |
| billing_period_start | TIMESTAMP | 账期开始时间 | NULLABLE |
| billing_period_end | TIMESTAMP | 账期结束时间 | NULLABLE |
| snapshot_at | TIMESTAMP | 快照时间 | NOT NULL, DEFAULT NOW |
| fetch_success | BOOLEAN | 是否成功获取 | NOT NULL, DEFAULT TRUE |
| error_message | TEXT | 错误信息 | NULLABLE |

#### 3.1.2 api_keys 表扩展

**新增字段**：

```sql
ALTER TABLE api_keys ADD COLUMN last_credit_snapshot_id INTEGER;
ALTER TABLE api_keys ADD COLUMN last_credit_check_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE api_keys ADD COLUMN cached_remaining_credits INTEGER;
ALTER TABLE api_keys ADD COLUMN cached_plan_credits INTEGER;
ALTER TABLE api_keys ADD COLUMN next_refresh_at TIMESTAMP WITH TIME ZONE;

CREATE INDEX idx_api_keys_next_refresh_at ON api_keys(next_refresh_at);
```

**字段说明**：

| 字段 | 类型 | 说明 | 用途 |
|-----|------|------|------|
| last_credit_snapshot_id | INTEGER | 最新快照 ID | 快速查询最新快照 |
| last_credit_check_at | TIMESTAMP | 最后真实检查时间 | 判断数据新鲜度 |
| cached_remaining_credits | INTEGER | 缓存的剩余额度 | 本地计算后的值 |
| cached_plan_credits | INTEGER | 缓存的计划额度 | 本地缓存 |
| next_refresh_at | TIMESTAMP | 下次刷新时间 | 智能刷新调度 |

### 3.2 SQLAlchemy 模型定义

```python
# app/db/models.py

class CreditSnapshot(Base):
    __tablename__ = "credit_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    api_key_id: Mapped[int] = mapped_column(ForeignKey("api_keys.id"), nullable=False)

    remaining_credits: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_credits: Mapped[int] = mapped_column(Integer, nullable=False)
    billing_period_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    billing_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    fetch_success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    api_key: Mapped["ApiKey"] = relationship(back_populates="credit_snapshots")


# 扩展 ApiKey 模型
class ApiKey(Base):
    # ... 现有字段 ...

    last_credit_snapshot_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_credit_check_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cached_remaining_credits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cached_plan_credits: Mapped[int | None] = mapped_column(Integer, nullable=True)
  resh_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    credit_snapshots: Mapped[list["CreditSnapshot"]] = relationship(
        back_populates="api_key", cascade="all, delete-orphan"
    )
```

---

## 4. 核心模块设计

### 4.1 额度采集模块

**模块路径**：`app/core/credit_fetcher.py`

**职责**：调用 Firecrawl API 获取真实额度信息

#### 4.1.1 核心函数

```python
# app/core/credit_fetcher.py

import httpx
from datetime import datetime
from sqlalchemy.orm import Session
from cryptography.exceptions import InvalidTag

from app.db.models import ApiKey, CreditSnapshot
from app.core.security import decrypt_api_key
from app.errors import FcamError
from app.config import AppConfig

async def fetch_credit_from_firecrawl(
    *,
    db: Session,
    key: ApiKey,
    master_key: bytes,
    config: AppConfig,
    request_id: str,
) -> CreditSnapshot:
    """
    从 Firecrawl API 获取额度信息

    Args:
        db: 数据库会话
        key: API Key 对象
        master_key: 主密钥（用于解密）
        config: 应用配置
        request_id: 请求 ID

    Returns:
        CreditSnapshot: 额度快照对象

    Raises:
        FcamError: 解密失败、API 调用失败等
    """
    # 1. 解密 API Key
    try:
        plaintext_api_key = decrypt_api_key(master_key, key.api_key_ciphertext)
    except (InvalidTag, ValueError) as e:
        # 解密失败，记录失败快照
        snapshot = CreditSnapshot(
            api_key_id=key.id,
            remaining_credits=0,
            plan_credits=0,
            fetch_success=False,
            error_message=f"Decryption failed: {str(e)}",
        )
        db.add(snapshot)
        db.commit()
        raise FcamError(
            status_code=500,
            code="DECRYPTION_FAILED",
            message="Failed to decrypt API key",
        )

    # 2. 调用 Firecrawl API
    url = f"{config.firecrawl.base_url}/v2/team/credit-usage"
    headers = {
        "Authorization": f"Bearer {plaintext_api_key}",
        "X-Request-Id": request_id,
    }
    timeout = httpx.Timeout(config.firecrawl.timeout)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, headers=headers)

        # 3. 处理响应
        if response.status_code == 200:
            data = response.json()
            if not data.get("success"):
                raise FcamError(
                    status_code=500,
                    code="UPSTREAM_ERROR",
                    message=data.get("error", "Unknown error"),
                )

            credit_data = data.get("data", {})
            snapshot = CreditSnapshot(
                api_key_id=ke           remaining_credits=credit_data.get("remainingCredits", 0),
                plan_credits=credit_data.get("planCredits", 0),
                billing_period_start=_parse_datetime(credit_data.get("billingPeriodStart")),
                billing_period_end=_parse_datetime(credit_data.get("billingPeriodEnd")),
                fetch_success=True,
            )
            db.add(snapshot)
            db.commit()
            db.refresh(snapshot)
            return snapshot

        elif response.status_code in (401, 403):
            # Key 无效，标记 Key 状态
            key.status = "failed"
            snapshot = CreditSnapshot(
                api_key_id=key.id,
                remaining_credits=0,
                plan_credits=0,
                fetch_success=False,
                error_message=f"{response.status_code} {response.text}",
            )
            db.add(snapshot)
            db.commit()
            raise FcamError(
                status_code=response.status_code,
                code="INVALID_API_KEY",
                message="API key is invalid or unauthorized",
            )

        elif response.status_code == 429:
            # 触发冷却
            snapshot = CreditSnapshot(
                api_key_id=key.id,
                remaining_credits=0,
                plan_credits=0,
                fetch_success=False,
                error_message="429 Rate Limited",
            )
            db.add(snapshot)
            db.commit()
            raise FcamError(
                status_code=429,
                code="RATE_LIMITED",
                message="Firecrawl API rate limited",
            )

        else:
            # 其他错误
            snapshot = CreditSnapshot(
                api_key_id=key.id,
                remaining_credits=0,
                plan_credits=0,
                fetch_success=False,
                error_message=f"{response.status_code} {response.text}",
            )
            db.add(snapshot)
            db.commit()
            raise FcamError(
                status_code=response.status_code,
                code="UPSTREAM_ERROR",
                message=f"Firecrawl API error: {response.status_code}",
            )

    except httpx.TimeoutException:
        snapshot = CreditSnapshot(
            api_key_id=key.id,
            remaining_credits=0,
            plan_credits=0,
            fetch_success=False,
            error_message="Request timeout",
        )
        db.add(snapshot)
        db.commit()
        raise FcamError(
            status_code=504,
            code="TIMEOUT",
            message="Firecrawl API request timeout",
        )


def _parse_datetime(dt_str: str | None) -> datetime | None:
    """解析 ISO8601 时间字符串"""
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
```

#### 4.1.2 错误处理策略

| 错误类型 | HTTP 状态码 | 处理策略 |
|---------|-----------|---------|
| 解密失败 | 500 | 记录失败快照，抛出异常 |
| 401/403 | 401/403 | 标记 Key 为 failed，记录失败快照 |
| 429 限流 | 429 | 记录失败快照，触发冷却机制 |
| 5xx 错误 | 5xx | 记录失败快照，延迟重试 |
| 超时 | 504 | 记录失败快照，延迟重试 |

### 4.2 本地计算模块

**模块路径**：`app/core/credit_estimator.py`

**职责**：估算请求消耗的 credits，更新本地缓存额度

#### 4.2.1 额度消耗估算

```python
# app/core/credit_estimator.py

from typing import Any

# Firecrawl API 各端点的 credits 消耗映射
CREDIT_COST_MAP = {
    # Firecrawl v1
    "/v1/scrape": 1,
    "/v1/crawl": 5,  # 启动成本，实际按页计费
    "/v1/search": 1,
    "/v1/map": 1,

    # Firecrawl v2
    "/v2/scrape": 1,
    "/v2/crawl": 5,
    "/v2/map": 1,
    "/v2/extract": 2,
    "/v2/batch/scrape": 1,  # 按页计费
}


def estimate_credit_cost(endpoint: str, response_data: dict[str, Any] | None = None) -> int:
    """
    估算请求消耗的 credits

    Args:
        endpoint: 请求端点（如 /v1/scrape）
        response_data: 响应数据（可选，用于更精确的n
    Returns:
        估算的 credits 消耗

    Examples:
        >>> estimate_credit_cost("/v1/scrape")
        1
        >>> estimate_credit_cost("/v1/crawl", {"data": {"total": 10}})
        10
    """
    # 基础消耗
    base_cost = CREDIT_COST_MAP.get(endpoint, 1)

    # 特殊处理：crawl 根据实际抓取页数计算
    if "/crawl" in endpoint and response_data:
        pages = response_data.get("data", {}).get("total", 1)
        return max(base_cost, pages)

    # 特殊处理：batch 操作根据批次大小计算
    if "/batch/" in endpoint and response_data:
        batch_size = response_data.get("data", {}).get("count", 1)
        return base_cost * batch_size

    return base_cost


def normalize_endpoint(path: str) -> str:
    """
    规范化端点路径，用于匹配 CREDIT_COST_MAP

    Args:
        path: 原始路径（如 /v1/scrape?url=xxx）

    Returns:
        规范化后的端点（如 /v1/scrape）

    Examples:
        >>> normalize_endpoint("/v1/scrape?url=https://example.com")
        '/v1/scrape'
        >>> normalize_endpoint("/v1/crawl/abc123")
        '/v1/crawl'
    """
    # 移除查询参数
    path = path.split("?")[0]

    # 移除动态路径参数（如 /v1/crawl/{id}）
    parts = path.split("/")
    if len(parts) > 3 and parts[2] in ("crawl", "batch"):
        # /v1/crawl/abc123 -> /v1/crawl
        return "/".join(parts[:3])

    return path
```

#### 4.2.2 本地额度更新

```python
# app/core/credit_estimator.py

import logging
from sqlalchemy.orm import Session
from app.db.models import ApiKey

logger = logging.getLogger(__name__)


async def update_local_credits(
    db: Session,
    key: ApiKey,
    delta: int,
    endpoint: str | None = None,
) -> None:
    """
    更新本地缓存的额度

    Args:
        db: 数据库会话
        key: API Key 对象
        delta: 额度变化量（负数表示消耗，正数表示增加）
        endpoint: 请求端点（用于日志）

    Side Effects:
        更新 key.cached_remaining_credits
    """
    if key.cached_remaining_credits is None:
        logger.warning(
            "local_credit_update_skipped",
            extra={
                "fields": {
                    "api_key_id": key.id,
                    "reason": "cached_credits_not_initialized",
                }
            },
        )
        return

    old_credits = key.cached_remaining_credits
    new_credits = max(0, old_credits + delta)
    key.cached_remaining_credits = new_credits

    try:
        db.commit()
        logger.info(
            "local_credit_updated",
            extra={
                "fields": {
                    "api_key_id": key.id,
                    "endpoint": endpoint,
                    "delta": delta,
                    "old_credits": old_credits,
                    "new_credits": new_credits,
                }
            },
        )
    except Exception as e:
        db.rollback()
        logger.error(
            "local_credit_update_failed",
            extra={
                "fields": {
                    "api_key_id": key.id,
                    "error": str(e),
                }
            },
        )
```

2.3 集成到 forwarder

```python
# app/core/forwarder.py

from app.core.credit_estimator import estimate_credit_cost, normalize_endpoint, update_local_credits

class Forwarder:
    async def forward_request(self, ...):
        # ... 现有的转发逻辑 ...

        # 请求成功后，更新本地额度
        if response.status_code < 400 and key:
            try:
                endpoint = normalize_endpoint(request.url.path)
                response_data = response.json() if response.headers.get("content-type") == "application/json" else None
                estimated_cost = estimate_credit_cost(endpoint, response_data)

                # 更新本地缓存额度
                await update_local_credits(
                    db=db,
                    key=key,
                    delta=-estimated_cost,  # 负数表示消耗
                    endpoint=endpoint,
                )
            except Exception as e:
                logger.error(f"Failed to update local credits: {e}")

        return response
```

### 4.3 智能刷新模块

**模块路径**：`app/core/credit_refresh.py`

**职责**：计算下次刷新时间，管理刷新任务调度

#### 4.3.1 刷新间隔计算

```python
# app/core/credit_refresh.py

from datetime import datetime, timedelta
from app.db.models import ApiKey
from app.config import AppConfigcalculate_next_refresh_time(key: ApiKey, config: AppConfig) -> datetime:
    """
    根据额度情况计算下次刷新时间

    策略：
    - 额度 < 10%：高频刷新（15 分钟）
    - 额度 10%-30%：中频刷新（30 分钟）
    - 额度 30%-50%：正常刷新（60 分钟）
    - 额度 > 50%：低频刷新（120 分钟）
    - 额度耗尽：停止刷新，等待账期重置

    Args:
        key: API Key 对象
        config: 应用配置

    Returns:
        下次刷新时间
    """
    now = datetime.utcnow()

    # 如果没有缓存数据，立即刷新
    if key.cached_remaining_credits is None or key.cached_plan_credits is None:
        return now

    # 如果计划额度为 0，避免除零错误
    if key.cached_plan_credits == 0:
        return now + timedelta(minutes=config.credit_monitoring.fixed_refresh.interval_minutes)

    # 计算使用率
    usage_ratio = 1 - (key.cached_remaining_credits / key.cached_plan_credits)

    # 额度耗尽，等待账期重置
    if key.cached_remaining_credits == 0:
        # 假设每月 1 号重置（实际应从 billing_period_end 获取）
        next_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0) + timedelta(days=32)
        return next_month.replace(day=1)

    # 根据使用率选择刷新间隔
    if not config.credit_monitoring.smart_refresh.enabled:
        # 固定刷新策略
        interval_minutes = config.credit_monitoring.fixed_refresh.interval_minutes
    else:
        # 智能刷新策略
        if usage_ratio > 0.9:  # 剩余 < 10%
            interval_minutes = config.credit_monitoring.smart_refresh.high_usage_interval
        elif usage_ratio > 0.7:  # 剩余 10%-30%
            interval_minutes = config.credit_monitoring.smart_refresh.medium_usage_interval
        elif usage_ratio > 0.5:  # 剩余 30%-50%
            interval_minutes = config.credit_monitoring.smart_refresh.normal_usage_interval
        else:  # 剩余 > 50%
            interval_minutes = config.credit_monitoring.smart_refresh.low_usage_interval

    return now + timedelta(minutes=interval_minutes)
```

本节将在下一部分继续编写...

#### 4.3.2 后台刷新任务

```python
# app/core/credit_refresh.py

import asyncio
import logging
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db.models import ApiKey, CreditSnapshot
from app.core.credit_fetcher import fetch_credit_from_firecrawl
from app.config import AppConfig

logger = logging.getLogger(__name__)


async def credit_refresh_loop(
    db_factory,  # 数据库会话工厂
    master_key: bytes,
    config: AppConfig,
):
    """
    后台额度刷新循环任务

    职责：
    1. 定期检查需要刷新的 Key
    2. 分批调用 Firecrawl API 获取真实额度
    3. 更新本地缓存和下次刷新时间
    4. 清理过期快照

    Args:
        db_factory: 数据库会话工厂函数
        master_key: 主密钥
        config: 应用配置
    """
    logger.info("credit_refresh_loop_started")

    while True:
        try:
            db: Session = db_factory()
            now = datetime.utcnow()

            # 1. 查询需要刷新的 Key
            keys_to_refresh = db.query(ApiKey).filter(
                ApiKey.is_active == True,
                ApiKey.status.in_(["active", "cooling"]),
                or_(
                    ApiKey.next_refresh_at.is_(None),
                    ApiKey.next_refresh_at <= now
                )
            ).all()

            logger.info(
                "credit_refresh_batch_start",
                extra={"fields": {"count": len(keys_to_refresh)}}
            )

            # 2. 分批处理
            batch_size = config.credit_monitoring.batch_size
            batch_delay = config.credit_monitoring.batch_delay_seconds

            for i in range(0, len(keys_to_refresh), batch_size):
                batch = keys_to_refresh[i:i + batch_size]

                for key in batch:
                    request_id = f"refresh-{key.id}-{int(now.timestamp())}"

                    try:
                        # 调用上游 API 获取真实额度
                        snapshot = await fetch_credit_from_firecrawl(
                            db=db,
                            key=key,
                            master_key=master_key,
                            config=config,
                            request_id=request_id,
                        )

                        # 更新缓存和下次刷新时间
                        key.cached_remaining_credits = snapshot.remaining_credits
                        key.cached_plan_credits = snapshot.plan_credits
                        key.last_credit_snapshot_id = snapshot.id
                        key.last_credit_check_at = now
                        key.next_refresh_at = calculate_next_refresh_time(key, config)

                        logger.info(
                            "credit_refresh_success",
                            extra={
                                "fields": {
                                    "api_key_id": key.id,
                                    "remaining": snapshot.remaining_credits,
                                    "plan": snapshot.plan_credits,
                                    "next_refresh": key.next_refresh_at.isoformat(),
                                }
                            },
                        )

                    except Exception as e:
                        logger.error(
                            "credit_refresh_failed",
                            extra={
                                "fields": {
                                    "api_key_id": key.id,
                                    "error": str(e),
                                }
                            },
                        )
                        # 失败后延迟重试
                        retry_delay = config.credit_monitoring.retry_delay_minutes
                        key.next_refresh_at = now + timedelta(minutes=retry_delay)

                db.commit()

                # 批次间延迟
                if i + batch_size < len(keys_to_refresh):
                    await asyncio.sleep(batch_delay)

            # 3. 清理过期快照
            await cleanup_old_snapshots(db, config)

            db.close()

        except Exception as e:
            logger.exception("credit_refresh_loop_error", extra={"error": str(e)})

        # 4. 等待下次检查（每 5 分钟检查一次）
        await asyncio.sleep(300)


async def cleanup_old_snapshots(db: Session, config: AppConfig):
    """
    清理过期的额度快照

    Args:
        db: 数据库会话
        config: 应用配置
    """
    retention_days = config.credit_monitoring.retention_days
    if retention_days <= 0:
        return

    cutoff_date = datetime.utcnow() - timedelta(days=retention_days)

    deleted_count = db.query(CreditSnapshot).filter(
        CreditSnapshot.snapshot_at < cutoff_date
    ).delete()

    if deleted_count > 0:
        db.commit()
        logger.info(
            "credit_snapshots_cleaned",
            extra={"fields": {"deleted_count": deleted_count}}
        )
```

### 4.4 Group 聚合模块

**模块路径**：`app/core/credit_aggregator.py`

**职责**：聚合 Client 下所有 Key 的额度信息

```python
# app/core/credit_aggregator.py

from sqlalchemy.orm import Session
from app.db.moort Client, ApiKey


def aggregate_client_credits(db: Session, client_id: int) -> dict:
    """
    聚合 Client 下所有 Key 的额度

    Args:
        db: 数据库会话
        client_id: Client ID

    Returns:
        聚合后的额度信息

    Raises:
        ValueError: Client 不存在
    """
    client = db.query(Client).filter(Client.id == client_id).one_or_none()
    if not client:
        raise ValueError(f"Client {client_id} not found")

    # 查询该 Client 下的所有 Key
    keys = db.query(ApiKey).filter(
        ApiKey.client_id == client_id,
        ApiKey.is_active == True
    ).all()

    if not keys:
        return {
            "client_id": client_id,
            "client_name": client.name,
            "total_remaining_credits": 0,
            "total_plan_credits": 0,
            "usage_percentage": 0.0,
            "keys": [],
        }

    # 聚合额度
    total_remaining = 0
    total_plan = 0
    key_details = []

    for key in keys:
        remaining = key.cached_remaining_credits or 0
        plan = key.cached_plan_credits or 0

        total_remaining += remaining
        total_plan += plan

        usage_pct = 0.0 if plan == 0 else ((plan - remaining) / plan) * 100

details.append({
            "api_key_id": key.id,
            "name": key.name,
            "remaining_credits": remaining,
            "plan_credits": plan,
            "usage_percentage": round(usage_pct, 2),
            "last_updated_at": key.last_credit_check_at.isoformat() if key.last_credit_check_at else None,
        })

    # 计算总使用率
    total_usage_pct = 0.0 if total_plan == 0 else ((total_plan - total_remaining) / total_plan) * 100

    return {
        "client_id": client_id,
        "client_name": client.name,
        "total_remaining_credits": total_remaining,"total_plan_credits": total_plan,
        "usage_percentage": round(total_usage_pct, 2),
        "keys": key_details,
    }
```

---

## 5. API 接口设计

### 5.1 GET /admin/keys/{id}/credits

**功能**：获取单个 Key 的额度信息

**实现**：

```python
# app/api/control_plane.py

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_admin
from app.db.models import ApiKey, CreditSnapshot
from app.errors import FcamError

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


@router.get("/keys/{key_id}/credits")
def get_key_credits(
    key_id: int,
    db: Session = Depends(get_db),
):
    """
    获取指定 Key 的额度信息

    优先返回本地缓存（实时），同时返回最新快照（真实值）
    """
    key = db.query(ApiKey).filter(ApiKey.id == key_id).one_or_none()
    if not key:
        raise FcamError(status_code=404, code="KEY_NOT_FOUND", message="API Key not found")

    # 获取最新快照
    latest_snapshot = None
    if key.last_credit_snapshot_id:
        latest_snapshot = db.query(CreditSnapshot).filter(
            CreditSnapshot.id == key.last_credit_snapshot_id
        ).one_or_none()

    # 构建响应
    response = {
        "api_key_id": key.id,
        "cached_credits": {
            "remaining_credits": key.cached_remaining_credits,
            "plan_credits": key.cached_plan_credits,
            "last_updated_at": key.last_credit_check_at.isoformat() if key.last_credit_check_at else None,
            "is_estimated": key.cached_remaining_credits is not None and (
                latest_snapshot is None or
                key.cached_remaining_credits != latest_snapshot.remaining_credits
            ),
        },
        "latest_snapshot": None,
        "next_refresh_at": key.next_refresh_at.isoformat() if key.next_refresh_at else None,
    }

    if latest_snapshot:
        response["latest_snapshot"] = {
            "remaining_credits": latest_snapshot.remaining_credits,
            "plan_credits": latest_snapshot.plan_credits,
            "billing_period_start": latest_snapshot.billing_period_start.isoformat() if latest_snapshot.billing_period_start else None,
            "billing_period_end": latest_snapshot.billing_period_end.isoformat() if latest_snapshot.billing_period_end else None,
            "snapshot_at": latest_snapshot.snapshot_at.isoformat(),
            "fetch_success": latest_snapshot.fetch_success,
        }

    return response
```

### 5.2 GET /admin/clients/{id}/credits

**功能**：获取 Client 的聚合额度信息

```python
@router.get("/clients/{client_id}/credits")
def get_client_credits(
    client_id: int,
    db: Session = Depends(get_db),
):
    """
    获取 Client 下所有 Key 的聚合额度
    """
    from app.core.credit_aggregator import aggregate_client_credits

    try:
        result = aggregate_client_credits(db, client_id)
        return result
    except ValueError as e:
        raise FcamError(status_code=404, code="CLIENT_NOT_FOUND", message=str(e))
```

### 5.3 POST /admin/keys/{id}/credits/refresh

**功能**：手动刷新单个 Key 的额度

```python
@router.post("/keys/{key_id}/credits/refresh")
async def refresh_key_credits(
    key_id: int,
    request: Request,
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
    secrets: Secrets = Depends(get_secrets),
):
    """
    手动触发单个 Key 的额度刷新
    """
    key = db.query(ApiKey).filter(ApiKey.id == key_id).one_or_none()
    if not key:
        raise FcamError(status_code=404, code="KEY_NOT_FOUND", message="API Key not found")

    # 检查刷新频率限制（最小间隔 5 分钟）
    if key.last_credit_check_at:
        elapsed = (datetime.utcnow() - key.last_credit_check_at).total_seconds()
        if elapsed < 300:  # 5 分钟
            raise FcamError(
                status_code=429,
            ode="REFRESH_TOO_FREQUENT",
                message=f"Please wait {int(300 - elapsed)} seconds before refreshing again",
            )

    # 调用刷新
    from app.core.credit_fetcher import fetch_credit_from_firecrawl
    from app.core.credit_refresh import calculate_next_refresh_time

    request_id = f"manual-refresh-{key_id}-{int(datetime.utcnow().timestamp())}"

    try:
        snapshot = await fetch_credit_from_firecrawl(
            db=db,
            key=key,
            master_key=secrets.master_key_bytes,
            config=config,
            request_id=request_id,
        )

        # 更新缓存
        key.cached_remaining_credits = snapshot.remaining_credits
        key.cached_plan_credits = snapshot.plan_credits
        key.last_credit_snapshot_id = snapshot.id
        key.last_credit_check_at = datetime.utcnow()
        key.next_refresh_at = calculate_next_refresh_time(key, config)
        db.commit()

        return {
            "api_key_id": key.id,
            "snapshot": {
                "remaining_credits": snapshot.remaining_credits,
                "plan_credits": snapshot.plan_credits,
                "snapshot_at": snapshot.snapshot_at.isoformat(),
            "fetch_success": snapshot.fetch_success,
            },
        }

    except FcamError:
        raise
    except Exception as e:
        raise FcamError(
            status_code=500,
            code="REFRESH_FAILED",
            message=f"Failed to refresh credits: {str(e)}",
        )
```

---

## 6. 前端组件设计

### 6.1 Key 列表增强

**文件**：`webui/src/views/ClientsKeysView.vue`

**新增列定义**：

```typescript
// 额度列
{
  title: '额度状态',
  key: 'credits',
  render: (row: KeyItem) => {
    if (!row.cached_credits) {
      return h('span', { style: 'color: #999' }, '未初始化');
    }

    const { remaining_credits, plan_credits, is_estimated } = row.cached_credits;
    const percentage = plan_credits > 0 ? (remaining_credits / plan_credits) * 100 : 0;

    // 进度条颜色
    let color = '#18a058'; // 绿色
    if (percentage < 20) color = '#d03050'; // 红色
    else if (percentage < 50) color = '#f0a020'; // 黄色

    return h('div', { style: 'display: flex; align-items: center; gap: 8px' }, [
      h(NProgress, {
        type: 'line',
        percentage: percentage,
        color: color,
        height: 8,
        style: 'flex: 1; min-width: 100px',
      }),
      h('span', { style: 'white-space: nowrap' }, [
        is_estimated ? '~' : '',
        `${remaining_credits.toLocaleString()} / ${plan_credits.toLocaleString()}`,
      ]),
    ]);
  },
},

// 下次刷新列
{
  title: '下次刷新',
  key: 'next_refresh',
  render: (row: KeyItem) => {
    if (!row.next_refresh_at) return '-';
    return h('span', formatRelativeTime(row.next_refresh_at));
  },
}
```

### 6.2 Client 额度聚合视图

**新增组件c/components/ClientCreditsCard.vue`

```vue
<template>
  <n-card :title="`${clientName} 总额度`" size="small">
    <n-space vertical>
      <n-progress
        type="line"
        :percentage="usagePercentage"
        :color="progressColor"
        :height="12"
      />
      <n-text>
        剩余: {{ totalRemaining.toLocaleString() }} / {{ totalPlan.toLocaleString() }}
        ({{ usagePercentage.toFixed(1) }}%)
      </n-text>

      <n-collapse>
        <n-collapse-item title="查看各 Key 详情">
          <n-list>
            <n-list-item v-for="key in keys" :key="key.api_key_id">
              <n-thing :title="key.name">
                <template #description>
                  {{ key.remaining_credits }} / {{ key.plan_credits }}
                  ({{ key.usage_percentage }}%)
                </template>
              </n-thing>
            </n-list-item>
          </n-list>
        </n-collapse-item>
      </n-collapse>
    </n-space>
  </n-card>
</template>

<script setup lang="ts">
import { computed } from 'vue';

const props = defineProps<{
  clientName: string;
  totalRemaining: number;
  totalPlan: number;
  usagePercentage: number;
  keys: Array<{
    api_key_id: number;
    name: string;
    remaining_credits: number;
    plan_credits: number;
    usage_percentage: number;
  }>;
}>();

const progressColor = computed(() => {
  const remaining = (100 - props.usagePercentage);
  if (remaining < 20) return '#d03050';
  if (remaining < 50) return '#f0a020';
  return '#18a058';
});
</script>
```

---

## 7. 测试方案

### 7.1 单元测试

**测试文件**：`tests/unit/test_credit_estimator.py`

```python
import pytest
from app.core.credit_estimator import estimate_credit_cost, normalize_endpoint


def test_estimate_credit_cost_scrape():
    assert estimate_credit_cost("/v1/scrape") == 1
    assert estimate_credit_cost("/v2/scrape") == 1


def test_estimate_credit_cost_crawl():
    # 基础成本
    assert estimate_credit_cost("/v1/crawl") == 5

    # 根据页数计算
    response_data = {"data": {"total": 10}}
    assert estimate_credit_cost("/v1/crawl", response_data) == 10


def test_normalize_endpoint():
    assert normalize_endpoint("/v1/scrape?url=https://example.com") == "/v1/scrape"
    assert normalize_endpoint("/v1/crawl/abc123") == "/v1/crawl"
```

### 7.2 集成测试

**测试文件**：`tests/integration/test_credit_monitoring.py`

```python
port pytest
from datetime import datetime, timedelta
from app.db.models import ApiKey, CreditSnapshot
from app.core.credit_refresh import calculate_next_refresh_time


def test_smart_refresh_high_usage(db, config):
    """测试高使用率时的刷新间隔"""
    key = ApiKey(
        cached_remaining_credits=50,
        cached_plan_credits=1000,  # 剩余 5%
    )
    db.add(key)
    db.commit()

    next_refresh = calculate_next_refresh_time(key, config)
    expected = datetime.utcnow() + timedelta(minutes=15)

    assert abs((next_refresh - expected).total_seconds()) < 60


def test_credit_snapshot_creation(db, test_key):
    """测试额度快照创建"""
    snapshot = CreditSnapshot(
        api_key_id=test_key.id,
        remaining_credits=8500,
        plan_credits=10000,
        fetch_success=True,
    )
    db.add(snapshot)
    db.commit()

    assert snapshot.id is not None
    assert snapshot.snapshot_at is not None
```

---

## 8. 部署与运维

### 8.1 数据库迁移

```bash
# 创建迁移脚本
alembic revision --autogenerate -m "add credit monitoring tables"

# 应用迁移
alembic upgrade head
```

### 8.2 配置示例

```yaml
# config.yaml
credit_monitoring:
  enabled: true
  smart_refresh:
  d: true
    high_usage_interval: 15
    medium_usage_interval: 30
    normal_usage_interval: 60
    low_usage_interval: 120
  batch_size: 10
  batch_delay_seconds: 5
  local_estimation:
    enabled: true
    sync_on_request: true
  retention_days: 90
```

### 8.3 监控指标

建议监控以下指标：

| 指标 | 说明 | 告警阈值 |
|-----|------|---------|
| credit_refresh_success_rate | 刷新成功率 | < 95% |
| credit_refresh_latency_p99 | 刷新延迟 P99 | > 5s |
| cached_credits_drift | 本地计算偏差 | > 10% |
| keys_with_low_credits | 低额度 Key 数量 | > 5 |

---

## 9. 附录

### 9.1 错误码清单

| 错误码 | HTTP 状态码 | 说明 |
|-------|-----------|------|
| KEY_NOT_FOUND | 404 | API Key 不存在 |
| CLIENT_NOT_FOUND | 404 | Client 不存在 |
| DECRYPTION_FAILED | 500 | Key 解密失败 |
| INVALID_API_KEY | 401/403 | Key 无效或未授权 |
| RATE_LIMITED | 429 | 上游 API 限流 |
| REFRESH_TOO_FREQUENT | 429 | 刷新过于频繁 |
| UPSTREAM_ERROR | 500/502 | 上游 API 错误 |
| TIMEOUT | 504 | 请求超时 |

### 9.2 相关文档

- PRD: `docs/PRD/2026-02-26-api-key-credit-monitoring.md`
- API 契约: `docs/MVP/Firecrawl-API-Manager-API-Contract.md`
- 数据库模型: `app/db/models.py`

---

**文档结束**
