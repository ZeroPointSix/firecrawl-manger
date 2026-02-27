# TDD: API Key 额度监控与展示 - 测试驱动开发文档

## 文档信息

| 项目 | 内容 |
|-----|------|
| 文档版本 | v1.0 |
| 创建日期 | 2026-02-26 |
| 最后更新 | 2026-02-26 |
| 作者 | 开发者何夕2077 |
| 关联 PRD | `docs/PRD/2026-02-26-api-key-credit-monitoring.md` |
| 关联 FD | `docs/FD/2026-02-26-api-key-credit-monitoring-fd.md` |
| 状态 | 草稿 |

## 1. 概述

### 1.1 文档目的

本文档详细描述 API Key 额度监控功能的测试策略、测试用例、测试数据和测试实施方案，确保功能质量和系统稳定性。

### 1.2 测试目标

1. **功能正确性**：验证所有功能按照 PRD 和 FD 要求正确实现
2. **性能要求**：确保满足性能指标（刷新延迟、查询响应时间）
3. **容错能力**：验证异常场景下的错误处理和恢复机制
4. **数据一致性**：确保本地计算和真实值的一致性
5. **并发安全**：验证多实例部署下的数据一致性

### 1.3 测试范围

**包含**：
- 额度采集模块
- 本地计算模块
- 智能刷新模块
- Group 聚合模块
- API 接口
- 前端组件
- 数据库操作
- 后台任务

**不包含**：
- Firecrawl 上游 API 的测试（外部依赖）
- 浏览器兼容性测试（前端框架已覆盖）
- 性能压测（单独进行）

---

## 2. 测试策略

### 2.1 测试金字塔

```
           ┌─────────────┐
           │  E2E 测试   │  10%
           │   (5 个)    │
           └─────────────┘
         ┌─────────────────┐
         │   集成测试      │  30%
         │   (15 个)       │
         └─────────────────┘
     ┌───────────────────────┐
     │     单元测试          │  60%
     │     (30 个)           │
     └───────────────────────┘
```

**测试分层原则**：
- **单元测试（60%）**：测试单个函数/方法，快速反馈
- **集成测试（30%）**：测试模块间交互，验证数据流
- **E2E 测试（10%）**：测试完整业务流程，验证用户场景

### 2.2 测试类型

| 测试类型 | 工具 | 覆盖范围 | 执行频率 |
|---------|------|---------|---------|
| 单元测试 | pytest | 核心函数、工具类 | 每次提交 |
| 集成测试 | pytest + TestClient | API 接口、数据库 | 每次提交 |
| E2E 测试 | pytest + httpx | 完整业务流程 | 每次发布 |
| 性能测试 | locust | 并发场景、响应时间 | 每周 |
| 安全测试 | bandit | 代码安全扫描 | 每次提交 |

### 2.3 测试环境

| 环境 | 用途 | 数据库 | 配置 |
|-----|------|--------|------|
| 本地开发 | 开发调试 | SQLite (内存) | 最小配置 |
| CI/CD | 自动化测试 | SQLite (文件) | 标准配置 |
| 测试环境 | 集成测试 | Postgres | 生产配置 |
| 预发布环境 | E2E 测试 | Postgres | 生产配置 |

### 2.4 测试覆盖率要求

| 模块 | 行覆盖率 | 分支覆盖率 | 说明 |
|-----|---------|-----------|------|
| 核心业务逻辑 | ≥ 90% | ≥ 85% | credit_fetcher, credit_estimator, credit_refresh |
| API 接口 | ≥ 85% | ≥ 80% | control_plane.py |
| 数据模型 | ≥ 80% | ≥ 75% | models.py |
| 工具函数 | ≥ 85% | ≥ 80% | 辅助函数 |
| **总体要求** | **≥ 80%** | **≥ 75%** | 项目整体 |

---

测试用例设计

### 3.1 单元测试用例

#### 3.1.1 额度采集模块 (credit_fetcher.py)

**测试文件**：`tests/unit/test_credit_fetcher.py`

##### TC-CF-001: 成功获取额度

**测试目标**：验证正常情况下能成功获取额度

**前置条件**：
- 有效的 API Key
- Firecrawl API 返回 200

**测试步骤**：
1. Mock Firecrawl API 返回成功响应
2. 调用 `fetch_credit_from_firecrawl()`
3. 验证返回的 CreditSnapshot 对象

**预期结果**：
- 返回 CreditSnapshot 对象
- `fetch_success = True`
- `remaining_credits` 和 `plan_credits` 正确
- 数据库中创建了快照记录

**测试代码**：
```python
import pytest
from unittest.mock import AsyncMock, patch
from datetime import datetime

from app.core.credit_fetcher import fetch_credit_from_firecrawl
from app.db.models import ApiKey, CreditSnapshot


@pytest.mark.asyncio
async def test_fetch_credit_success(db, test_key, master_key, config):
    """TC-CF-001: 成功获取额度"""
    # Mock Firecrawl API 响应
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "success": True,
        "data": {
            "remainingCredits": 8500,
            "planCredits": 10000,
            "billingPeriodStart": "2026-02-01T00:00:00Z",
            "billingPeriodEnd": "2026-03-01T00:00:00Z",
        }
    }

    with patch("httpx.AsyncClient.get", return_value=mock_response):
        snapshot = await fetch_credit_from_firecrawl(
            db=db,
            key=test_key,
            master_key=master_key,
            config=config,
            request_id="test-001",
        )

    # 验证结果
    assert snapshot is not None
    assert snapshot.fetch_success is True
    assert snapshot.remaining_credits == 8500
    assert snapshot.plan_credits == 10000
    assert snapshot.api_key_id == test_key.id

    # 验证数据库记录
    db_snapshot = db.query(CreditSnapshot).filter(
        CreditSnapshot.id == snapshot.id
    ).one()
    assert db_snapshot.remaining_credits == 8500
```

##### TC-CF-002: API Key 无效 (401)

**测试目标**：验证 Key 无效时的错误处理

**前置条件**：
- API Key 已失效
- Firecrawl API 返回 401

**测试步骤**：
1. Mock Firecrawl API 返回 401
2. 调用 `fetch_credit_from_firecrawl()`
3. 验证异常和 Key 状态

**预期结果**：
- 抛出 `FcamError` 异常
- Key 状态更新为 `failed`
- 创建失败快照记录

**测试代码**：
```python
@pytest.mark.asyncio
async def test_fetch_credit_unauthorized(db, test_key, master_key, config):
    """TC-CF-002: API Key 无效 (401)"""
    mocponse = AsyncMock()
    mock_response.status_code = 401
    mock_response.text = "Unauthorized"

    with patch("httpx.AsyncClient.get", return_value=mock_response):
        with pytest.raises(FcamError) as exc_info:
            await fetch_credit_from_firecrawl(
                db=db,
                key=test_key,
                master_key=master_key,
                config=config,
                request_id="test-002",
            )

    # 验证异常
    assert exc_info.value.code == "INVALID_API_KEY"
    assert exc_info.value.status_code == 401

    # 验证 Key 状态
    db.refresh(test_key)
    assert test_key.status == "failed"

    # 验证失败快照
    snapshot = db.query(CreditSnapshot).filter(
        CreditSnapshot.api_key_id == test_key.id
    ).first()
    assert snapshot.fetch_success is False
    assert "401" in snapshot.error_message
```

##### TC-CF-003: API 限流 (429)

**测试目标**：验证限流时的错误处理

**测试代码**：
```python
@pytest.mark.asyncio
async def test_fetch_credit_rate_limited(db, test_key, master_key, config):
    """TC-CF-003: API 限流 (429)"""
    mock_response = AsyncMock()
    mock_response.status_code = 429
    mock_response.text = "Rate Limited"

    with patch("httpx.AsyncClient.get", return_value=mock_response):
        withest.raises(FcamError) as exc_info:
            await fetch_credit_from_firecrawl(
                db=db,
                key=test_key,
                master_key=master_key,
                config=config,
                request_id="test-003",
            )

    assert exc_info.value.code == "RATE_LIMITED"
    assert exc_info.value.status_code == 429

    # 验证失败快照
    snapshot = db.query(CreditSnapshot).filter(
        CreditSnapshot.api_key_id == test_key.id
    ).first()
    assert snapshot.fetch_success is False
    assert "429" in snapshot.error_message
```

##### TC-CF-004: 请求超时

**测试目标**：验证超时场景的错误处理

**测试代码**：
```python
import httpx

@pytest.mark.asyncio
async def test_fetch_credit_timeout(db, test_key, master_key, config):
    """TC-CF-004: 请求超时"""
    with patch("httpx.AsyncClient.get", side_effect=httpx.TimeoutException("Timeout")):
        with pytest.raises(FcamError) as exc_info:
            await fetch_credit_from_firecrawl(
                db=db,
                key=test_key,
                master_key=master_key,
                config=config,
                request_id="test-004",
            )

    assert exc_info.value.code == "TIMEOUT"
    assert exc_info.vaus_code == 504

    # 验证失败快照
    snapshot = db.query(CreditSnapshot).filter(
        CreditSnapshot.api_key_id == test_key.id
    ).first()
    assert snapshot.fetch_success is False
    assert "timeout" in snapshot.error_message.lower()
```

##### TC-CF-005: 解密失败

**测试目标**：验证 Key 解密失败时的错误处理

**测试代码**：
```python
from cryptography.exceptions import InvalidTag

@pytest.mark.asyncio
async def test_fetch_credit_decryption_failed(db, test_key, config):
    """TC-CF-005: 解密失败"""
    # 使用错误的 master_key
    wrong_master_key = b"wrong_key_32_bytes_minimum______"
 pytest.raises(FcamError) as exc_info:
        await fetch_credit_from_firecrawl(
            db=db,
            key=test_key,
            master_key=wrong_master_key,
            config=config,
            request_id="test-005",
        )

    assert exc_info.value.code == "DECRYPTION_FAILED"
    assert exc_info.value.status_code == 500

    # 验证失败快照
    snapshot = db.query(CreditSnapshot).filter(
        CreditSnapshot.api_key_id == test_key.id
    ).first()
    assert snapshot.fetch_success is False
    assert "Decryption failed" in snapshot.error_message
```

#### 3.1.2 本地计算模块 (credit_estimator.py)

**测试文件**：`tests/unit/test_credit_estimator.py`

##### TC-CE-001: 估算 scrape 请求消耗

**测试代码**：
```python
from app.core.credit_estimator import estimate_credit_cost


def test_estimate_scrape_cost():
    """TC-CE-001: 估算 scrape 请求消耗"""
    assert estimate_credit_cost("/v1/scrape") == 1
    assert estimate_credit_cost("/v2/scrape") == 1
    assert estimate_credit_cost("/v1/scrape", {"data": {}}) == 1
```

##### TC-CE-002: 估算 crawl 请求消耗（基础）

**测试代码**：
```python
def test_estimate_crawl_cost_base():
    """TC-CE-002: 估算 crawl 请求消耗（基础）"""
    assert estimate_credit_cost("/v1/crawl") == 5
  assert estimate_credit_cost("/v2/crawl") == 5
```

##### TC-CE-003: 估算 crawl 请求消耗（按页数）

**测试代码**：
```python
def test_estimate_crawl_cost_with_pages():
    """TC-CE-003: 估算 crawl 请求消耗（按页数）"""
    response_data = {"data": {"total": 10}}
    assert estimate_credit_cost("/v1/crawl", response_data) == 10

    response_data = {"data": {"total": 100}}
    assert estimate_credit_cost("/v1/crawl", response_data) == 100

    # 页数小于基础成本
    response_data = {"data": {"total": 2}}
    assert estimate_credit_cost("/v1/crawl", response_data) == 5
```

##### TC-CE-004: 估算 batch 请求消耗

**测试代码**：
```python
def test_estimate_batch_cost():
    """TC-CE-004: 估算 batch 请求消耗"""
    response_data = {"data": {"count": 5}}
    assert estimate_credit_cost("/v2/batch/scrape", response_data) == 5

    response_data = {"data": {"count": 10}}
    assert estimate_credit_cost("/v2/batch/scrape", response_data) == 10
```

##### TC-CE-005: 规范化端点路径

**测试代码**：
```python
from app.core.credit_estimator import normalize_endpoint


def test_normalize_endpoint():
    """TC-CE-005: 规范化端点路径"""
    # 移除查询参数
    assert normalize_endpoint("/v1/scrape?url=https://example.com") == "/v1/scrape"

    # 移除动态路径参数
    assert normalize_endpoint("/v1/crawl/abc123") == "/v1/crawl"
    assert normalize_endpoint("/v2/batch/scrape/xyz789") == "/v2/batch/scrape"

    # 普通路径
    assert normalize_endpoint("/v1/scrape") == "/v1/scrape"
    assert normalize_endpoint("/v2/map") == "/v2/map"
```

##### TC-CE-006: 更新本地额度（正常消耗）

**测试代码**：
```python
import pytest
from app.core.credit_estimator import update_local_credits
from app.db.models import ApiKey


@pytest.mark.asyncio
async def test_update_local_credits_consume(db):
    """TC-CE-006: 更新本地额度（正常消耗）"""
    # 创建测试 Key
    key = ApiKey(
        api_key_ciphertext=b"test",
        api_key_hash="test_hash",
        api_key_last4="1234",
        cached_remaining_credits=1000,
        cached_plan_credits=10000,
    )
    db.add(key)
    db.commit()

    # 消耗 10 credits
    await update_local_credits(db, key, delta=-10, endpoint="/v1/scrape")

    # 验证结果
    db.refresh(key)
    assert key.cached_remaining_credits == 990
```

##### TC-CE-007: 更新本地额度（额度不足）

**测试代码**：
```python
@pytest.mark.asyncio
async def test_update_local_credits_insufficient(db):
    """TC-CE-007: 更新本地额度（额度不足）"""
    key = ApiKey(
        api_key_ciphertext=b"test",
        api_key_hash="test_hash",
        api_key_last4="1234",
        cached_remaining_credits=5,
        cached_plan_credits=10000,
    )
    db.add(key)
    db.commit()

    # 消耗 10 credits（超过剩余额度）
    await update_local_credits(db, key, delta=-10, endpoint="/v1/scrape")

    # 验证结果（不应为负数）
    db.refresh(key)
    assert key.cached_remaining_credits == 0
```

##### TC-CE-008: 更新本地额度（未初始化）

**测试代码**：
```python
@pytest.mark.asyncnc def test_update_local_credits_not_initialized(db):
    """TC-CE-008: 更新本地额度（未初始化）"""
    key = ApiKey(
        api_key_ciphertext=b"test",
        api_key_hash="test_hash",
        api_key_last4="1234",
        cached_remaining_credits=None,  # 未初始化
        cached_plan_credits=None,
    )
    db.add(key)
    db.commit()

    # 尝试更新（应跳过）
    await update_local_credits(db, key, delta=-10, endpoint="/v1/scrape")

    # 验证结果（应保持 None）
    db.refresh(key)
    assert key.cached_remaining_credits is None
```

本节将在下一部分继续编写...

#### 3.1.3 智能刷新模块 (credit_refresh.py)

**测试文件**：`tests/unit/test_credit_refresh.py`

##### TC-CR-001: 计算刷新间隔（高使用率）

**测试目标**：验证高使用率时使用短刷新间隔

**测试代码**：
```python
from datetime import datetime, timedelta
from app.core.credit_refresh import calculate_next_refresh_time
from app.db.models import ApiKey


def test_calculate_refresh_high_usage(config):
    """TC-CR-001: 计算刷新间隔（高使用率）"""
    # 剩余 5%（高使用率）
    key = ApiKey(
        cached_remaining_credits=50,
        cached_plan_credits=1000,
    )

    next_refresh = calculate_next_refresh_time(key, config)
    expected = datetime.utcnow() + timedelta(minutes=15)

    # 允许 1 分钟误差
    assert abs((next_refresh - expected).total_seconds()) < 60
```

##### TC-CR-002: 计算刷新间隔（中使用率）

**测试代码**：
```python
def test_calculate_refresh_medium_usage(config):
    """TC-CR-002: 计算刷新间隔（中使用率）"""
    # 剩余 20%（中使用率）
    key = ApiKey(
        cached_remaining_credits=200,
        cached_plan_credits=1000,
    )

    next_refresh = calculate_next_refresh_time(key, config)
    expected = datetime.utcnow() + timedelta(minutes=30)

    assert abs((next_refresh - expected).total_seconds()) < 60
```

##### TC-CR-003: 计算刷新间隔（正常使用率）

**测试代码**：
```python
def test_calculate_refresh_normal_usage(config):
    """TC-CR-003: 计算刷新间隔（正常使用率）"""
    # 剩余 40%（正常使用率）
    key = ApiKey(
        cached_remaining_credits=400,
        cached_plan_credits=1000,
    )

    next_refresh = calculate_next_refresh_time(key, config)
    expected = datetime.utcnow() + timedelta(minutes=60)

    assert abs((next_refresh - expected).total_seconds()) < 60
```

##### TC-CR-004: 计算刷新间隔（低使用率）

**测试代码**：
```python
def test_calculate_refresh_low_usage(config):
    """TC-CR-004: 计算刷新间隔（低使用率）"""
    # 剩余 80%（低使用率）
    key = ApiKey(
        cached_remaining_credits=800,
        cached_plan_credits=1000,
    )

    next_refresh = calculate_next_refresh_time(key, config)
    expected = datetime.utcnow() + timedelta(minutes=120)

    assert abs((next_refresh - expected).total_seconds()) < 60
```

##### TC-CR-005: 计算刷新间隔（额度耗尽）

**测试代码**：
```python
def test_calculate_refresh_depleted(config):
    """TC-CR-005: 计算刷新间隔（额度耗尽）"""
    # 额度耗尽
    key = ApiKey(
        cached_remaining_credits=0,
        cached_plan_credits=1000,
    )

    next_refresh = calculate_next_refresh_time(key, config)

    # 应该等待到下个月 1 号
    now = datetime.utcnow()
    next_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0) + timedelta(days=32)
    expected = next_month.replace(day=1)

    assert next_refresh.day == 1
    assert next_refresh.month == expected.month
```

##### TC-CR-006: 计算刷新间隔（未初始化）

**测试代码**：
```python
def test_calculate_refresh_not_initialized(config):
    """TC-CR-006: 计算刷新间隔（未初始化）"""
    # 缓存未初始化
    key = ApiKey(
        cached_remaining_credits=None,
        cached_plan_credits=None,
    )

    next_refresh = calculate_next_refresh_time(key, config)

    # 应该立即刷新
    now = datetime.utcnow()
    assert abs((next_refresh - now).total_seconds()) < 5
```

##### TC-CR-007: 固定刷新策略

**测试代码**：
```python
def test_calculate_refresh_fixed_strategy(config):
    """TC-CR-007: 固定刷新策略"""
    # 禁用智能刷新
    config.credit_monitoring.smart_refresh.enabled = False
    config.credit_monitoring.fixed_refresh.interval_minutes = 60

    key = ApiKey(
        cached_remaining_credits=50,  # 高使用率
        cached_plan_credits=1000,
    )

    next_refresh = calculate_next_refresh_time(key, config)
    expected = datetime.utcnow() + timedelta(minutes=60)

    # 应该使用固定间隔，而不是智能间隔
    assert abs((next_refresh - expected).total_seconds()) < 60
```

#### 3.1.4 Group 聚合模块 (credit_aggregator.py)

**测试文件**：`tests/unit/test_credit_aggregator.py`

##### TC-CA-001: 聚合单个 Client 的额度

**测试代码**：
```python
from app.core.credit_aggregator import aggregate_client_credits
from app.db.models import Client, ApiKey


def test_aggregate_single_client(db):
    """TC-CA-001: 聚合单个 Client 的额度"""
    # 创建 Client
    client = Client(name="test-client")
    db.add(client)
    db.commit()

    # 创建 3 个 Key
    keys = [
        ApiKey(
            client_id=client.id,
            api_key_ciphertext=b"test1",
            api_key_hash="hash1",
            api_key_last4="0001",
            name="key-1",
            cached_remaining_credits=8500,
            cached_plan_credits=10000,
            is_active=True,
        ),
        ApiKey(
            client_id=client.id,
            api_key_ciphertext=b"test2",
            api_key_hash="hash2",
            api_key_last4="0002",
            name="key-2",
            cached_remaining_credits=9000,
            cached_plan_credits=10000,
            is_active=True,
        ),
        ApiKey(
            client_id=client.id,
            api_key_ciphertext=b"test3",
            api_key_hash="hash3",
            api_key_last4="0003",
            name="key-3",
            cached_remaining_credits=7500,
            cached_plan_credits=10000,
            is_active=True,
        ),
    ]
    db.add_all(keys)
    db.commit()

    # 聚合额度
    result = aggregate_client_credits(db, client.id)

    # 验证结果
    assert result["client_id"] == client.id
    assert result["client_name"] == "test-client"
    assert result["total_remaining_credits"] == 25000  # 8500 + 9000 + 7500
    assert result["total_plan_credits"] == 30000  # 10000 * 3
    assert result["usage_percentage"] == 16.67  # (30000 - 25000) / 30000 * 100
    assert len(result["keys"]) == 3
```

##### TC-CA-002: 聚合空 Client（无 Key）

**测试代码**：
```python
def test_aggregate_empty_client(db):
    """TC-CA-002: 聚合空 Client（无 Key）"""
    client = Client(name="empty-client")
    db.add(client)
    db.commit()

    result = aggregate_client_credits(db, client.id)

    assert result["client_id"] == client.id
    assert result["total_remaining_credits"] == 0
    assert result["total_plan_credits"] == 0
    assert result["usage_percentage"] == 0.0
    assert len(result["keys"]) == 0
```

##### TC-CA-003: 聚合不存在的 Client

**测试代码**：
```python
import pytest


def test_aggregate_nonexistent_client(db):
    """TC-CA-003: 聚合不存在的 Client"""
    with pytest.raises(ValueError) as exc_info:
        aggregate_client_credits(db, 99999)

    assert "not found" in str(exc_info.value).lower()
```

##### TC-CA-004: 聚合时排除非活跃 Key

**测试代码**：
```python
def test_aggregate_exclude_inactive_keys(db):
    """TC-CA-004: 聚合时排除非活跃 Key"""
    client = Client(name="test-client")
    db.add(client)
    db.commit()

    keys = [
        ApiKey(
            client_id=client.id,
            api_key_ciphertext=b"test1",
            api_key_hash="hash1",
            api_key_last4="0001",
            name="active-key",
            cached_remaining_credits=8500,
            cached_plan_credits=10000,
            is_active=True,
        ),
        ApiKey(
            client_id=client.id,
            api_key_ciphertext=b"test2",
            api_key_hash="hash2",
            api_key_last4="0002",
            name="inactive-key",
            cached_remaining_credits=5000,
            cached_plan_credits=10000,
            is_active=False,  # 非活跃
        ),
    ]
    db.add_all(keys)
    db.commit()

    result = aggregate_client_credits(db, client.id)

    # 应该只包含活跃 Key
    assert result["total_remaining_credits"] == 8500
    assert result["total_plan_credits"] == 10000
    assert len(result["keys"]) == 1
```

##### TC-CA-005: 聚合时处理未初始化的 Key

**测试代码**：
```python
def test_aggregate_with_uninitialized_keys(db):
    """TC-CA-005: 聚合时处理未初始化的 Key"""
    client = Client(name="test-client")
    db.add(client)
    db.commit()

    keys = [
        ApiKey(
            client_id=client.id,
            api_key_ciphertext=b"test1",
            api_key_hash="hash1",
            api_key_last4="0001",
            name="initialized-key",
            cached_remaining_credits=8500,
            cached_plan_credits=10000,
            is_active=True,
        ),
        ApiKey(
            client_id=client.id,
            api_key_ciphertext=b"test2",
            api_key_hash="hash2",
            api_key_last4="0002",
            name="uninitialized-key",
            cached_remaining_credits=None,  # 未初始化
            cached_plan_credits=None,
            is_active=True,
        ),
    ]
    db.add_all(keys)
    db.commit()

    result = aggregate_client_credits(db, client.id)

    # 未初始化的 Key 应该按 0 计算
    assert result["total_remaining_credits"] == 8500
    assert result["total_plan_credits"] == 10000
    assert len(result["keys"]) == 2
```

---

## 4. 集成测试用例

### 4.1 API 接口测试

**测试文件**：`tests/integration/test_credit_api.py`

#### TC-API-001: GET /admin/keys/{id}/credits - 成功获取

**测试代码**：
```python
from fastapi.testclient import TestClient


def test_get_key_credits_success(client: TestClient, test_key, admin_token):
    """TC-API-001: 获取 Key 额度信息"""
    # 初始化缓存额度
    test_key.cached_remaining_credits = 8500
    test_key.cached_plan_credits = 10000
    db.commit()

    response = client.get(
        f"/admin/keys/{test_key.id}/credits",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["api_key_id"] == test_key.id
    assert data["cached_credits"]["remaining_credits"] == 8500
    assert data["cached_credits"]["plan_credits"] == 10000
```

#### TC-API-002: GET /admin/keys/{id}/credits - Key 不存在

**测试代码**：
```python
def test_get_key_credits_not_found(client: TestClient, admin_token):
    """TC-API-002: 获取不存在的 Key 额度"""
    response = client.get(
        "/admin/keys/99999/credits",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 404
    data = response.json()
    assert data["error"]["code"] == "KEY_NOT_FOUND"
```

#### TC-API-003: GET /admin/clients/{id}/credits - 成功聚合

**测试代码**：
```python
def test_get_client_credits_success(client: TestClient, test_client, admin_token, db):
    """TC-API-003: 获取 Client 聚合额度"""
    # 创建多个 Key
    keys = [
        ApiKey(
            client_id=test_client.id,
            api_key_ciphertext=b"test1",
            api_key_hash="hash1",
            api_key_last4="0001",
            cached_remaining_credits=8500,
            cached_plan_credits=10000,
            is_active=True,
        ),
        ApiKey(
            client_id=test_client.id,
            api_key_ciphertext=b"test2",
            api_key_hash="hash2",
            api_key_last4="0002",
            cached_remaining_credits=9000,
            cached_plan_credits=10000,
            is_active=True,
        ),
    ]
    db.add_all(keys)
    db.commit()

    response = client.get(
        f"/admin/clients/{test_client.id}/credits",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["client_id"] == test_client.id
    assert data["total_remaining_credits"] == 17500
    assert data["total_plan_credits"] == 20000
    assert len(data["keys"]) == 2
```

#### TC-API-004: POST /admin/keys/{id}/credits/refresh - 成功刷新

**测试代码**：
```python
from unittest.mock import patch, AsyncMock


def test_refresh_key_credits_success(client: TestClient, test_key, admin_token):
    """TC-API-004: 手动刷新 Key 额度"""
    # Mock Firecrawl API
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "success": True,
        "data": {
            "remainingCredits": 9500,
            "planCredits": 10000,
            "billingPeriodStart": "2026-02-01T00:00:00Z",
            "billingPeriodEnd": "2026-03-01T00:00:00Z",
        }
    }

    with patch("httpx.AsyncClient.get", return_value=mock_response):
        response = client.post(
            f"/admin/keys/{test_key.id}/credits/refresh",
            headers={"Authorization": f"Bearer {admin_token}"}
        )

    assert response.status_code == 200
    data = response.json()
    assert data["api_key_id"] == test_key.id
    assert data["snapshot"]["remaining_credits"] == 9500
    assert data["snapshot"]["fetch_success"] is True
```

#### TC-API-005: POST /admin/keys/{id}/credits/refresh - 刷新过于频繁

**测试代码**：
```python
from datetime import datetime, timedelta


def test_refresh_key_credits_too_frequent(client: TestClient, test_key, admin_token, db):
    """TC-API-005: 刷新过于频繁"""
    # 设置最近刚刷新过
    test_key.last_credit_check_at = datetime.utcnow() - timedelta(minutes=2)
    db.commit()

    response = client.post(
        f"/admin/keys/{test_key.id}/credits/refresh",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 429
    data = response.json()
    assert data["error"]["code"] == "REFRESH_TOO_FREQUENT"
```

### 4.2 数据库操作测试

**测试文件**：`tests/integration/test_credit_database.py`

#### TC-DB-001: 创建额度快照

**测试代码**：
```python
from app.db.models import CreditSnapshot


def test_create_credit_snapshot(db, test_key):
    """TC-DB-001: 创建额度快照"""
    snapshot = CreditSnapshot(
        api_key_id=test_key.id,
        remaining_credits=8500,
        plan_credits=10000,
        fetch_success=True,
    )
    db.add(snapshot)
    db.commit()

    # 验证创建成功
    assert snapshot.id is not None
    assert snapshot.snapshot_at is not None

    # 验证查询
    db_snapshot = db.query(CreditSnapshot).filter(
        CreditSnapshot.id == snapshot.id
    ).one()
    assert db_snapshot.remaining_credits == 8500
```

#### TC-DB-002: 级联删除快照

**测试代码**：
```python
def test_cascade_delete_snapshots(db, test_key):
    """TC-DB-002: 删除 Key 时级联删除快照"""
    # 创建快照
    snapshot = CreditSnapshot(
        api_key_id=test_key.id,
        remaining_credits=8500,
        plan_credits=10000,
        fetch_success=True,
    )
    db.add(snapshot)
    db.commit()
    snapshot_id = snapshot.id

    # 删除 Key
    db.delete(test_key)
    db.commit()

    # 验证快照也被删除
    db_snapshot = db.query(CreditSnapshot).filter(
        CreditSnapshot.id == snapshot_id
    ).one_or_none()
    assert db_snapshot is None
```

---

## 5. E2E 测试用例

**测试文件**：`tests/e2e/test_credit_monitoring_e2e.py`

### TC-E2E-001: 完整的额度监控流程

**测试目标**：验证从刷新到展示的完整流程

**测试代码**：
```python
import pytest
import httpx
from unittest.mock import patch, AsyncMock


@pytest.mark.e2e
def test_complete_credit_monitoring_flow(base_url, admin_token):
    """TC-E2E-001: 完整的额度监控流程"""
    # 1. 创建 Client 和 Key
    # 2. 刷新额度
    # 3. 查询额度
    # 4. 模拟消耗
    # 5. 验证本地计算
    # 具体实现见 FD 文档示例
    pass
```

---

## 6. 测试数据准备

### 6.1 Pytest Fixtures

**文件**：`tests/conftest.py`

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, ApiKey, Client
from app.config import AppConfig


@pytest.fixture(scope="session")
def config():
    """测试配置"""
    config = AppConfig()
    config.credit_monitoring.smart_refresh.enabled = True
    return config


@pytest.fixture(scope="function")
def db():
    """测试数据库会话"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()
```

---

## 7. Mock 策略

### 7.1 Mock Firecrawl API

```python
from unittest.mock import AsyncMock


class FirecrawlMock:
    @staticmethod
    def success_response(remaining=8500, plan=10000):
        mock = AsyncMock()
        mock.status_code = 200
        mock.json.return_value = {
            "success": True,
            "data": {
                "remainingCredits": remaining,
                "planCredits": plan,
            }
        }
        return mock
```

---

## 8. 测试执行

```bash
# 运行所有测试
pytest

# 生成覆盖率报告
pytest --cov=app --cov-report=html

# 运行指定测试
pytest tests/unit/test_credit_estimator.py
```

---

## 9. 测试用例清单

### 9.1 单元测试（30 个）

| 模块 | 用例数 |
|-----|--------|
| credit_fetcher | 5 |
| credit_estimator | 8 |
| credit_refresh | 7 |
| credit_aggregator | 5 |
| 其他 | 5 |

### 9.2 集成测试（15 个）

| 模块 | 用例数 |
|-----|--------|
| API 接口 | 8 |
| 数据库操作 | 5 |
| 其他 | 2 |

### 9.3 E2E 测试（5 个）

| 场景 | 用例数 |
|-----|--------|
| 完整流程 | 2 |
| 智能刷新 | 2 |
| 其他 | 1 |

---

## 10. 附录

### 10.1 测试覆盖率目标

- 总体覆盖率：≥ 80%
- 核心模块覆盖率：≥ 90%
- 分支覆盖率：≥ 75%

### 10.2 相关文档

- PRD: `docs/PRD/2026-02-26-api-key-credit-monitoring.md`
- FD: `docs/FD/2026-02-26-api-key-credit-monitoring-fd.md`

---

**文档结束**
