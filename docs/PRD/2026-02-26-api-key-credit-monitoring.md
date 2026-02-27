# PRD: API Key 额度监控与展示

## 1. 背景与目标

### 1.1 背景
当前 FCAM 系统管理多个 Firecrawl API Key，但缺乏对上游 Firecrawl 账户真实额度（credits）的可见性。运维人员无法实时了解：
- 每个 API Key 的剩余额度
- 额度消耗速率和趋势
- 账期重置时间
- 何时需要充值或调整 Key 分配策略

### 1.2 目标
构建一套完整的额度监控体系，实现：
1. **实时可见**：前端展示每个 API Key 和 Group 的上游额度信息
2. **智能更新**：后端智能刷新策略，减少对上游 API 的调用
3. **本地计算**：基于请求消耗本地估算额度，定期同步真实值
4. **历史追踪**：记录额度变化历史，支持趋势分析
5. **主动告警**：额度不足时提供视觉提示（未来可扩展为通知）

### 1.3 设计原则
1. **减少上游调用**：不在每次请求时调用上游 API，而是定期轮询 + 本地计算
2. **智能刷新**：根据额度情况动态调整刷新频率（额度低 → 刷新频繁）
3. **Group 聚合**：支持按 Client 分组展示额度，便于管理
4. **容错设计**：上游 API 失败不影响系统正常运行

### 1.4 非目标
- 不实现自动充值功能
- 不实现额度预测算法（首期仅展示历史数据）
- 不实现跨 Key 的额度调度优化

---

## 2. 核心功能

### 2.1 功能概览

| 功能模块 | 优先级 | 说明 |
|---------|--------|------|
| 额度数据采集 | P0 | 调用 Firecrawl API 获取额度信息 |
| 本地额度计算 | P0 | 基于请求消耗本地估算额度 |
| 额度历史存储 | P0 | 数据库记录额度快照 |
| 控制面 API | P0 | 提供额度查询接口（单 Key + Group 聚合） |
| 前端展示 | P0 | Key 列表中展示额度信息 |
| 智能刷新策略 | P1 | 根据额度情况动态调整刷新频率 |
| Group 级别聚合 | P1 | 按 Client 分组展示总额度 |
| 额度趋势图 | P1 | 可视化额度变化趋势 |
| 手动刷新 | P1 | 支持单个/批量手动刷新 |

---

## 3. 技术方案

### 3.1 Firecrawl API 集成

#### 3.1.1 上游接口
**端点**：`GET https://api.firecrawl.dev/v2/team/credit-usage`

**认证**：`Authorization: Bearer <api_key>`

**响应示例**：
```json
{
  "success": true,
  "data": {
    "remainingCredits": 8500,
    "planCredits": 10000,
    "billingPeriodStart": "2026-02-01T00:00:00Z",
    "billingPeriodEnd": "2026-03-01T00:00:00Z"
  }
}
```

#### 3.1.2 错误处理
- **401/403**：Key 无效或权限不足 → 标记 Key 状态为 `failed`
- **429**：触发冷却机制（复用现有 cooldown 逻辑）
- **5xx**：记录错误但不影响 Key 状态，下次轮询重试
- **超时**：使用配置的 `firecrawl.timeout`，超时视为失败

### 3.2 数据库设计

#### 3.2.1 新增表：credit_snapshots

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
```

**字段说明**：
- `remaining_credits`：剩余额度
- `plan_credits`：计划总额度
- `billing_period_start/end`：账期时间（用于判断是否跨账期）
- `fetch_success`：本次获取是否成功（失败时仍记录快照，但标记为失败）
- `error_message`：失败原因（如 "401 Unauthorized"）

#### 3.2.2 ApiKey 表扩展

为了支持本地额度计算和智能刷新，在 `api_keys` 表增加字段：

```sql
ALTER TABLE api_keys ADD COLUMN last_credit_snapshot_id INTEGER;
ALTER TABLE api_keys ADD COLUMN last_credit_check_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE api_keys ADD COLUMN cached_remaining_credits INTEGER;
ALTER TABLE api_keys ADD COLUMN cached_plan_credits INTEGER;
ALTER TABLE api_keys ADD COLUMN next_refresh_at TIMESTAMP WITH TIME ZONE;
```

**字段说明**：
- `last_credit_snapshot_id`：指向最新的快照记录
- `last_credit_check_at`：最后一次真实检查时间（调用上游 API）
- `cached_remaining_credits`：缓存的剩余额度（本地计算）
- `cached_plan_credits`：缓存的计划总额度
- `next_refresh_at`：下次刷新时间（智能刷新策略计算）

### 3.3 本地额度计算机制

#### 3.3.1 设计思路

**核心原则**：减少对上游 API 的调用，通过本地计算估算额度。

**工作流程**：
1. **定期同步**：后台任务定期调用 Firecrawl API 获取真实额度（作为基准）
2. **本地扣减**：每次请求成功后，本地扣减估算的额度消耗
3. **智能刷新**：根据额度情况动态调整下次刷新时间
4. **容错处理**：本地计算可能不准确，定期同步真实值进行校准

#### 3.3.2 额度消耗估算

Firecrawl 不同操作消耗的 credits 不同，我们需要根据请求类型估算消耗：

```python
# app/core/credit_estimator.py

CREDIT_COST_MAP = {
    # Firecrawl v1
    "/v1/scrape": 1,           # 单页抓取
    "/v1/crawl": 5,            # 爬虫任务（按页计费，这里是启动成本）
    "/v1/search": 1,           # 搜索

    # Firecrawl v2
    "/v2/scrape": 1,
    "/v2/crawl": 5,
    "/v2/map": 1,
    "/v2/extract": 2,
}

def estimate_credit_cost(endpoint: str, response_data: dict | None = None) -> int:
    """
    估算请求消耗的 credits

    Args:
        endpoint: 请求端点（如 /v1/scrape）
        response_data: 响应数据（可选，用于更精确的估算）

    Returns:
        估算的 credits 消耗
    """
    # 基础消耗
    base_cost = CREDIT_COST_MAP.get(endpoint, 1)

    # 如果是 crawl，根据实际抓取页数计算
    if "/crawl" in endpoint and response_data:
        pages = response_data.get("data", {}).get("total", 1)
        return max(base_cost, pages)

    return base_cost
```

#### 3.3.3 本地额度更新逻辑

在 `forwarder.py` 的请求成功后，更新本地缓存的额度：

```python
# app/core/forwarder.py

async def forward_request(...):
    # ... 现有的转发逻辑 ...

    # 请求成功后，更新本地额度
    if response.status_code < 400 and key:
        estimated_cost = estimate_credit_cost(endpoint, response_data)
        await update_local_credits(db, key, -estimated_cost)

    return response

async def update_local_credits(db: Session, key: ApiKey, delta: int):
    """
    更新本地缓存的额度

    Args:
        db: 数据库会话
        key: API Key 对象
        delta: 额度变化量（负数表示消耗）
    """
    if key.cached_remaining_credits is not None:
        new_credits = max(0, key.cached_remaining_credits + delta)
        key.cached_remaining_credits = new_credits
        db.commit()

        logger.info(
            "local_credit_updated",
            extra={
                "fields": {
                    "api_key_id": key.id,
                    "delta": delta,
                    "remaining": new_credits,
                }
            }
        )
```

### 3.4 智能刷新策略

#### 3.4.1 动态刷新间隔

根据额度情况动态调整刷新频率：

```python
# app/core/credit_refresh.py

def calculate_next_refresh_time(key: ApiKey) -> datetime:
    """
    根据额度情况计算下次刷新时间

    策略：
    - 额度 < 10%：15 分钟刷新一次（高频）
    - 额度 10%-30%：30 分钟刷新一次（中频）
    - 额度 30%-50%：60 分钟刷新一次（正常）
    - 额度 > 50%：120 分钟刷新一次（低频）
    - 额度耗尽：停止刷新，等待账期重置
    """
    if key.cached_remaining_credits is None or key.cached_plan_credits is None:
        # 没有缓存数据，立即刷新
        return datetime.utcnow()

    usage_ratio = 1 - (key.cached_remaining_credits / key.cached_plan_credits)

    if key.cached_remaining_credits == 0:
        # 额度耗尽，等待账期重置（假设每月1号重置）
        next_month = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0) + timedelta(days=32)
        return next_month.replace(day=1)
    elif usage_ratio > 0.9:  # 剩余 < 10%
        interval_minutes = 15
    elif usage_ratio > 0.7:  # 剩余 10%-30%
        interval_minutes = 30
    elif usage_ratio > 0.5:  # 剩余 30%-50%
        interval_minutes = 60
    else:  # 剩余 > 50%
        interval_minutes = 120

    return datetime.utcnow() + timedelta(minutes=interval_minutes)
```

#### 3.4.2 刷新触发条件

后台任务只刷新满足条件的 Key：

```python
async def credit_refresh_loop():
    while True:
        try:
            now = datetime.utcnow()

            # 获取需要刷新的 Key
            keys_to_refresh = db.query(ApiKey).filter(
                ApiKey.is_active == True,
                ApiKey.status.in_(["active", "cooling"]),
                or_(
                    ApiKey.next_refresh_at.is_(None),
                    ApiKey.next_refresh_at <= now
                )
            ).all()

            logger.info(f"Found {len(keys_to_refresh)} keys to refresh")

            # 分批处理
            for batch in chunk(keys_to_refresh, config.batch_size):
                for key in batch:
                    try:
                        # 调用上游 API 获取真实额度
                        snapshot = await fetch_credit_from_firecrawl(key)
                        db.add(snapshot)

                        # 更新缓存和下次刷新时间
                        key.cached_remaining_credits = snapshot.remaining_credits
                        key.cached_plan_credits = snapshot.plan_credits
                        key.last_credit_snapshot_id = snapshot.id
                        key.last_credit_check_at = now
                        key.next_refresh_at = calculate_next_refresh_time(key)

                    except Exception as e:
                        logger.error(f"Failed to fetch credits for key {key.id}: {e}")
                        # 记录失败快照
                        db.add(CreditSnapshot(
                            api_key_id=key.id,
                            fetch_success=False,
                            error_message=str(e)
                        ))
                        # 失败后延迟重试
                        key.next_refresh_at = now + timedelta(minutes=10)

                db.commit()
                await asyncio.sleep(contch_delay_seconds)

            # 清理过期快照
            cleanup_old_snapshots(retention_days=config.retention_days)

        except Exception as e:
            logger.exception("Credit refresh loop failed")

        # 每 5 分钟检查一次是否有需要刷新的 Key
        await asyncio.sleep(300)
```

### 3.5 后端 API 设计

#### 3.5.1 GET /admin/keys/{id}/credits

**功能**：获取指定 Key 的最新额度信息（优先返回本地缓存）

**响应**：
```json
{
  "api_key_id": 1,
  "cached_credits": {
    "remaining_credits": 8500,
    "plan_credits": 10000,
    "last_updated_at": "2026-02-26T10:30:00Z",
    "is_estimated": false
  },
  "latest_snapshot": {
    "remaining_credits": 8600,
    "plan_credits": 10000,
    "billing_period_start": "2026-02-01T00:00:00Z",
    "billing_period_end": "2026-03-01T00:00:00Z",
    "snapshot_at": "2026-02-26T10:00:00Z",
    "fetch_success": true
  },
  "usage_rate": {
    "credits_per_hour": 125.5,
    "estimated_depletion_at": "2026-02-28T18:00:00Z"
  },
  "next_refresh_at": "2026-02-26T11:00:00Z"
}
```

**字段说明**：
- `cached_credits`：本地缓存的额度（实时，包含本地计算）
- `latest_snapshot`：最后一次真实快照（可能有延迟）
- `is_estimated`：是否为估算值（true 表示基于本地计算）

**错误码**：
- `404`：Key 不存在
- `503`：Master Key 未配置

#### 3.5.2 GET /admin/clients/{id}/credits

**功能**：获取指定 Client 关联的所有 Key 的额度聚合信息

**响应**：
```json
{
  "client_id": 1,
  "client_name": "production-service",
  "total_remaining_credits": 25000,
  "total_plan_credits": 30000,
  "usage_percentage": 16.67,
  "keys": [
    {
      "api_key_id": 1,
      "name": "key-1",
      "remaining_credits": 8500,
      "plan_credits": 10000,
      "usage_percentage": 15.0,
      "last_updated_at": "2026-02-26T10:30:00Z"
    },
    {
      "api_key_id": 2,
      "name": "key-2",
      "remaining_credits": 9000,
      "plan_credits": 10000,
      "usage_percentage": 10.0,
      "last_updated_at": "2026-02-26T10:25:00Z"
    },
    {
      "api_key_id": 3,
      "name": "key-3",
      "remaining_credits": 7500,
      "plan_credits": 10000,
      "usage_percentage": 25.0,
      "last_updated_at": "2026-02-26T10:20:00Z"
    }
  ],
  "usage_rate": {
    "credits_per_hour": 250,
    "estimated_depletion_at": "2026-03-05T10:00:00Z"
  }
}
```

**错误码**：
- `404`：Client 不存在

#### 3.5.3 GET /admin/keys/{id}/credits/history

**功能**：获取额度历史记录（用于趋势图）

**Query 参数**：
- `limit`：返回条数（默认 100，最大 500）
- `since`：起始时间（ISO8601 格式）
- `until`：结束时间（ISO8601 格式）

**响应**：
```json
{
  "api_key_id": 1,
  "snapshots": [
    {
      "remaining_credits": 8500,
      "plan_credits": 10000,
      "snapshot_at": "2026-02-26T10:00:00Z",
      "fetch_success": true
    },
    {
      "remaining_credits": 8750,
      "plan_credits": 10000,
      "snapshot_at": "2026-02-26T09:00:00Z",
      "fetch_success": true
    }
  ],
  "total_count": 48
}
```

#### 3.5.4 POST /admin/keys/{id}/credits/refresh

**功能**：手动触发单个 Key 的额度刷新

**响应**：
```json
{
  "api_key_id": 1,
  "snapshot": {
    "remaining_credits": 8500,
    "plan_credits": 10000,
    "snapshot_at": "2026-02-26T10:35:00Z",
    "fetch_success": true
  }
}
```

**错误码**：
- `429`：刷新过于频繁（建议最小间隔 5 分钟）
- `503`：上游 API 不可用

#### 3.5.5 POST /admin/keys/credits/refresh-all

**功能**：批量刷新所有活跃 Key 的额度

**Request**：
```json
{
  "key_ids": [1, 2, 3],  // 可选，不传则刷新所有活跃 Key
  "force": false         // 是否忽略刷新间隔限制
}
```

**响应**：
```json
{
  "total": 10,
  "success": 8,
  "failed": 2,
  "results": [
    {
      "api_key_id": 1,
      "success": true,
      "remaining_credits": 8500
    },
    {
      "api_key_id": 2,
      "success": false,
      "error": "401 Unauthorized"
    }
  ]
}
```

### 3.6 配置项设计

#### 3.6.1 config.yaml 配置

```yaml
credit_monitoring:
  enabled: true

  # 智能刷新策略
  smart_refresh:
    enabled: true
    # 不同额度水平的刷新间隔（分钟）
    high_usage_interval: 15    # 剩余 < 10%
    medium_usage_interval: 30  # 剩余 10%-30%
    normal_usage_interval: 60  # 剩余 30%-50%
    low_usage_interval: 120    # 剩余 > 50%

  # 固定刷新策略（smart_refresh.enabled=false 时使用）
  fixed_refresh:
    interval_minutes: 60

  # 批量处理配置
  batch_size: 10                # 每批处理 10 个 Key
  batch_delay_seconds: 5        # 批次间延迟 5 秒

  # 本地额度计算
  local_estimation:
    enabled: true               # 启用本地额度估算
    sync_on_request: true       # 每次请求后更新本地额度

  # 数据保留
  retention_days: 90            # 快照保留 90 天

  # 容错配置
  retry_on_failure: true
  retry_delay_minutes: 10       # 失败后延迟重试
```

### 3.7 前端设计

#### 3.7.1 Key 列表增强

在 `ClientsKeysView.vue` 的 Key 表格中新增列：

| 列名 | 内容 | 说明 |
|-----|------|------|
| 额度状态 | 进度条 + 数值 | `8,500 / 10,000 (85%)` |
| 账期 | 日期范围 | `2026-02-01 ~ 2026-03-01` |
| 最后更新 | 相对时间 | `5 分钟前` + 估算标识 |
| 下次刷新 | 相对时间 | `55 分钟后` |

**视觉设计**：
- 额度 > 50%：绿色进度条
- 额度 20%-50%：黄色进度条
- 额度 < 20%：红色进度条 + 警告图标
- 额度 = 0：灰色 + "已耗尽"标签
- 估算值：显示 "~" 符号提示（如 `~8,500`）

#### 3.7.2 Client 视图增强

在 Client 列表中增加额度聚合信息：

**展示内容**：
- Client 名称旁显示总额度徽章（如 `25k / 30k`）
- 点击 Client 展开，显示该 Client 下所有 Key 的额度分布
- 支持按额度使用率排序

**交互设计**：
- 鼠标悬停显示详细信息（每个 Key 的额度）
- 点击"查看趋势"跳转到 Group 级别的趋势图

#### 3.7.3 额度趋势图

新增 `CreditTrendChart.vue` 组件（参考 `RequestTrendChart.vue`）：

**功能**：
- X 轴：时间（最近 7 天 / 30 天 / 自定义）
- Y 轴：剩余额度
- 支持单 Key 视图和 Group 聚合视图
- 支持多 Key 对比（最多 5 个）
- 标注账期重置点
- 显示本地估算值和真实快照值（不同颜色区分）

**交互**：
- 鼠标悬停显示详细数值
- 点击数据点跳转到对应时间的请求日志
- 切换视图模式（单 Key / Group 聚合）

#### 3.7.4 手动刷新

在 Key 列表顶部新增操作按钮：
- **刷新所有额度**：调用 `POST /admin/keys/credits/refresh-all`
- **刷新选中**：批量刷新勾选的 Key
- 显示刷新进度和结果（成功/失败数量）
- 显示下次自动刷新时间

#### 3.7.5 API 调用封装

新增 `webui/src/api/credits.ts`：

```typescript
export interface CreditSnapshot {
  remaining_credits: number;
  plan_credits: number;
  billing_period_start: string;
  billing_period_end: string;
  snapshot_at: string;
  fetch_success: boolean;
}

export interface CachedCredits {
  remaining_credits: number;
  plan_credits: number;
  last_updated_at: string;
  is_estimated: boolean;
}

export interface CreditInfo {
  api_key_id: number;
  cached_credits: CachedCredits;
  latest_snapshot: CreditSnapshot | null;
  usage_rate?: {
    credits_per_hour: number;
    estimated_depletion_at: string | null;
  };
  next_refresh_at: string;
}

export interface ClientCreditsInfo {
  client_id: number;
  client_name: string;
  total_remaining_credits: number;
  total_plan_credits: number;
  usage_percentage: number;
  keys: Array<{
    api_key_id: number;
    name: string;
    remaining_credits: number;
    plan_credits: number;
    usage_percentage: number;
    last_updated_at: string;
  }>;
  usage_rate?: {
    credits_per_hour: number;
    estimated_depletion_at: string | null;
  };
}

export async function fetchKeyCredits(keyId: number): Promise<CreditInfo> {
  const res = await http.get(`/admin/keys/${keyId}/credits`);
  return res.data;
}

export async function fetchClientCredits(clientId: number): Promise<ClientCreditsInfo> {
  const res = await http.get(`/admin/clients/${clientId}/credits`);
  return res.data;
}

export async function fetchKeyCreditsHistory(
  keyId: number,
  params?: { limit?: number; since?: string; until?: string }
): Promise<{ snapshots: CreditSnapshot[]; total_count: number }> {
  const res = await http.get(`/admin/keys/${keyId}/credits/history`, { params });
  return res.data;
}

export async function refreshKeyCredits(keyId: number): Promise<CreditSnapshot> {
  const res = await http.post(`/admin/keys/${keyId}/credits/refresh`);
  return res.data.snapshot;
}

export async function refreshAllCredits(keyIds?: number[]): Promise<{
  total: number;
  success: number;
  failed: number;
  results: Array<{ ap: number; success: boolean; remaining_credits?: number; error?: string }>;
}> {
  const res = await http.post('/admin/keys/credits/refresh-all', { key_ids: keyIds });
  return res.data;
}
```

---

## 4. 实施计划

### 4.1 阶段划分

#### Phase 1: 核心功能（P0）
**目标**：实现基础的额度查询和展示

**任务**：
1. 数据库迁移：创建 `credit_snapshots` 表
2. 后端实现：
   - `fetch_credit_from_firecrawl()` 核心函数
   - `GET /admin/keys/{id}/credi
   - `POST /admin/keys/{id}/creditsefresh` 接口
3. 前端实现：
   - Key 列表增加额度列
   - 手动刷新单个 Key
4. 测试：单元测试 + 集成测试

**验收标准**：
- 可以手动刷新并查看任意 Key 的额度
- 额度信息正确展示在前端

#### Phase 2: 自动化与历史（P1）
**目标**：实现定时刷新和趋势分析

**任务**：
1. 后端实现：
   - 后台定时任务
   - `GET /admin/keys/{id}/credits/history` 接口
   - `POST /admin/keys/credits/refresh-all` 接口
2. 前端实现：
   - `CreditTrendChart.vue` 组件
   - 批量刷新功能
3. 配置：`config.yaml` 增加 `credit_monitoring` 配置项
4. 文档：更新 API 契约和运维手册

**验收标准**：
- 系统自动每小时刷新所有 Key 的额度
- 可以查看任意 Key 的额度历史趋势图

#### Phase 3: 优化与告警（P2，未来）
**任务**：
- 额度不足时发送通知（邮件/Webhook）
- 额度消耗速率预测
- 跨 Key 的额度使用分析

### 4.2 工作量估算

| 任务 | 工时 | 负责人 |
|-----|------|--------|
| 数据库设计与迁移 | 3h | 后端 |
| 本地额度计算逻辑 | 6h | 后端 |
| 智能刷新策略实现 | 6h | 后端 |
| 后端核心逻辑 | 8h | 后端 |
| 后端 API 接口（含 Group 聚合） | 8h | 后端 |
| 后台定时任务 | 4h | 后端 |
| 前端 API 封装 | 3h | 前端 |
| 前端 UI 实现（Key + Client 视图） | 10h | 前端 |
| 前端趋势图组件 | 8h | 前端 |
| 单元测试 | 6h | 后端 |
| 集成测试 | 6h | 后端 |
| E2E 测试 | 3h | 前端 |
| 文档更新 | 3h | 全栈 |
| **总计** | **74h** | **约 9 人天** |

---

## 5. 风险与依赖

### 5.1 技术风险

| 风险 | 影响 | 缓解措施 |
|-----|------|---------|
| Firecrawl API 限流 | 无法频繁刷新额度 | 智能刷新策略，根据额度动态调整频率 |
| 本地估算不准确 | 额度显示偏差 | 定期同步真实值校准，标识估算值 |
| 上游 API 变更 | 接口失效 | 版本化处理，增加错误监控 |
| 数据库性能 | 查询慢 | 定期清理，增加索引，缓存热数据 |
| 多实例部署冲突 | 重复刷新 | 使用分布式锁（Redis），智能调度 |
| 额度消耗估算偏差 | 本地计算不准 | 定期同步真实值，调整估算系数 |

### 5.2 依赖项

- **上游 API**：依赖 Firecrawl `/v2/team/credit-usage` 接口稳定性
- **数据库**：需要 Alembic 迁移支持
- **前端库**：可能需要引入图表库（如 ECharts 或 Chart.js）

---

## 6. 成功指标

### 6.1 功能指标
- ✅ 所有活跃 Key 的额度信息可见
- ✅ 额度刷新成功率 > 95%
- ✅ 前端额度展示延迟 < 2 秒

### 6.2 性能指标
- 单次额度刷新耗时 < 3 秒
- 批量刷新 100 个 Key 耗时 < 5 分钟
- 快照查询响应时间 < 500ms

### 6.3 用户体验
- 运维人员可以快速识别额度不足的 Key
- 无需登录 Firecrawl 控制台即可查看额度
- 额度趋势图帮助预测充值时机

---

## 7. 未来扩展

### 7.1 智能告警
- 额度低于阈值时发送通知（邮件/Slack/钉钉）
- 异常消耗检测（消耗速率突增）
- 账期即将结束提醒

### 7.2 额度优化
- 根据额度情况动态调整 Key 权重
- 自动禁用额度耗尽的 Key
- 跨 Client 的额度均衡分配

### 7.3 成本分析
- 按 Client 统计额度消耗
- 生成月度成本报告
- 额度消耗趋势预测

### 7.4 高级功能
- 额度预算管理（设置 Client 级别的额度上限）
- 额度使用效率分析（每个 Client 的 ROI）
- 自动充值建议（基于历史消耗预测）

---

## 8. 设计亮点总结

### 8.1 核心优势

**1. 减少上游调用**
- ✅ 不在每次请求时调用上游 API
- ✅ 定期轮询 + 本地计算的混合模式
- ✅ 智能刷新策略，额度低时才高频刷新

**2. Group 级别聚合**
- ✅ 按 Client 分组展示总额度
- ✅ 便于管理多个 Key 的场景
- ✅ 支持 Group 级别的趋势分析

**3. 智能刷新策略**
- ✅ 根据额度情况动态调整刷新频率
- ✅ 额度充足时低频刷新，节省资源
- ✅ 额度不足时高频刷新，保证准确性

**4. 本地额度计算**
- ✅ 基于请求消耗实时更新本地额度
- ✅ 定期同步真实值进行校准
- ✅ 标识估算值，用户可区分真实值和估算值

### 8.2 与 gpt-load 的差异化

| 功能 | gpt-load | FCAM（本 PRD） |
|-----|----------|---------------|
| 上游额度监控 | ❌ | ✅ |
| 额度历史追踪 | ❌ | ✅ |
| 额度趋势分析 | ❌ | ✅ |
| Group 级别聚合 | ✅ | ✅ |
| 智能刷新策略 | ❌ | ✅ |
| 本地额度计算 | ❌ | ✅ |
| 分组管理 | ✅ | ✅（Client） |

---

## 9. 附录

### 8.1 相关文档
- Firecrawl API 文档：https://docs.firecrawl.dev/api-reference/endpoint/credit-usage
- FCAM API 契约：`docs/MVP/Firecrawl-API-Manager-API-Contract.md`
- 数据库模型：`app/db/models.py`

### 8.2 参考实现
- Key 测试功能：`app/core/forwarder.py:test_key()`
- 批量操作：`app/api/control_plane.py:batch_keys()`
- 趋势图组件：`webui/src/components/RequestTrendChart.vue`
