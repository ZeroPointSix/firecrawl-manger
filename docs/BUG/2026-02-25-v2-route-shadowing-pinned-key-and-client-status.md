# BUG：v2 路由吞噬 + pinned key 绕过治理 + Client 状态语义不一致（修复记录）

> **创建时间**：2026-02-25  
> **状态**：Fixed（已落地到代码与测试）  
> **优先级**：P0（路由/治理/删除语义）  
> **影响范围**：数据面 `/v2/*` 兼容层、sticky resource binding、Forwarder、控制面 Client 管理、请求日志 `endpoint` 字段、接口契约文档

---

## 1. 背景

在对齐 Firecrawl API v2 OpenAPI 并补齐缺失端点（`/v2/scrape|search|map|team/*|crawl/active|crawl/params-preview`）的过程中，引入了：
- **显式端点定义**（替代仅靠通配符转发，保证 `request.state.endpoint` 可追溯）
- **sticky resource binding**（创建资源时记录 `resource_id -> api_key_id`，后续查询固定使用同一 key，避免“换 key 查不到资源”的 404）

关联文档：
- PRD：`docs/PRD/2026-02-25-firecrawl-v2-missing-endpoints.md`
- FD：`docs/FD/2026-02-25-firecrawl-v2-missing-endpoints-fd.md`
- TDD：`docs/TDD/2026-02-25-firecrawl-v2-missing-endpoints-tdd.md`
- 批量 Client Bug 背景：`docs/TODO/2026-02-25-client-batch-operations-bugfix.md`

---

## 2. 问题概述（修复项）

1) **v2 静态路由被参数路由吞噬（P0）**  
`/v2/crawl/active`、`/v2/crawl/params-preview` 会被 `/v2/crawl/{job_id}` 匹配，导致功能与日志 `endpoint` 都错误。

2) **Forwarder pinned key 模式绕过 key 级治理（P0）**  
当 `pinned_api_key_id` 被传入时，未经过 key 级 **限流** 与 **并发** 控制，存在打穿治理上限的风险。

3) **Client.status 语义未贯穿 create/update/delete（P0）**  
引入 `clients.status` 后，若不在控制面统一维护，将出现“禁用/删除语义不清、列表过滤不一致、UI 展示不一致”等问题。

---

## 3. BUG #1：`/v2/crawl/active` 被 `/v2/crawl/{job_id}` 吞噬

### 3.1 复现

调用：
```bash
curl -X GET "http://localhost:8000/v2/crawl/active" \
  -H "Authorization: Bearer <CLIENT_TOKEN>"
```

**预期**：
- 命中显式端点 `GET /v2/crawl/active`
- `RequestLog.endpoint == "crawl_active"`

**实际（修复前）**：
- 命中 `GET /v2/crawl/{job_id}`，其中 `job_id="active"`
- `RequestLog.endpoint` 被记录为 `crawl_status`

### 3.2 根因

FastAPI 路由匹配按**定义顺序**优先匹配；当参数路由：
- `@router.get("/crawl/{job_id}")`

定义在静态路由：
- `@router.get("/crawl/active")`
- `@router.post("/crawl/params-preview")`

之前时，`active` / `params-preview` 会被当作 `{job_id}` 捕获。

### 3.3 修复

将静态路由移动到参数路由之前，并显式加注释提醒顺序约束：
- `app/api/firecrawl_v2_compat.py:216`

### 3.4 验证

新增/加强测试：  
- `tests/integration/test_firecrawl_v2_missing_endpoints.py:178`（校验 `RequestLog.endpoint` 为显式端点标识）

---

## 4. BUG #2：Forwarder pinned key 绕过 key 级限流/并发

### 4.1 复现（逻辑复现）

场景：在 sticky resource binding 下，路由层会传入 `pinned_api_key_id`，Forwarder 使用指定 key 发起上游请求。  
修复前该分支不会走 “key 级限流 + key 级并发” 的守卫逻辑，导致 pinned key 请求**绕过治理**。

典型风险：
- key `max_concurrent=1` 时，pinned key 请求仍可继续打上游（应被拦截）
- key `rate_limit_per_min` 已触发限流时，pinned key 请求仍可继续打上游（应被拦截）

### 4.2 根因

Forwarder 的实现分为两条路径：
- **正常选择 key**（KeyPool 选择）路径：有 `TokenBucketRateLimiter.allow(...)` 与 `ConcurrencyManager.try_acquire(...)`
- **pinned key** 路径：修复前缺失上述两项治理逻辑

即：sticky binding 为了“固定用同一把 key 查资源”，引入了 `pinned_api_key_id`，但 pinned 分支未同步治理策略，形成治理绕过。

### 4.3 修复

在 pinned 分支补齐 key 级治理：
- 先 `allow(str(key.id), key.rate_limit_per_min)`，不允许则抛 `503 ALL_KEYS_BUSY`（并带 `Retry-After`）
- 再 `try_acquire(str(key.id), key.max_concurrent)`，无 lease 则抛 `503 ALL_KEYS_BUSY`
- 确保请求完成后释放 lease

代码位置：
- `app/core/forwarder.py:207`（Pinned key mode 注释 + 治理补齐）

### 4.4 验证

新增测试（覆盖 pinned key 的两类拦截）：
- `tests/integration/test_forwarder.py:395`（pinned key 被 rate limiter 拦截）
- `tests/integration/test_forwarder.py:433`（pinned key 被 concurrency 拦截）

---

## 5. BUG #3：Client.status 语义未贯穿 create/update/delete

### 5.1 复现（典型表现）

在引入 `clients.status`（用于区分 `disabled` vs `deleted`）后，如果控制面未统一维护，会出现：
- `POST /admin/clients` 创建 `is_active=false` 的 Client，但 `status` 仍为默认值（与 `is_active` 不一致）
- `DELETE /admin/clients/{id}` 后，Client 不应继续出现在 `GET /admin/clients` 列表中（因为列表按 `status != "deleted"` 过滤），但若 delete 未写入 `status="deleted"`，则会出现“删了还在”的错觉
- 批量 `disable` 与 `delete` 如果只写 `is_active=false`，两者效果一致，无法满足“禁用可见、删除隐藏”的产品预期

### 5.2 根因

`clients.status` 是新增字段（迁移 + 模型已存在），但：
- create/update/delete/batch 操作未形成统一的“状态机/语义约束”
- 列表接口开始按 `status != "deleted"` 做过滤后，若 delete 不同步写 status，则接口语义漂移

### 5.3 修复

**后端：状态语义一致化**
- `Client` 返回结构补充 `status` 字段：`app/api/control_plane.py:119`
- 创建 Client 时写入 `status`：`app/api/control_plane.py:848`
- 更新 `is_active` 时同步写入 `status=active/disabled`：`app/api/control_plane.py:891`
- 删除 Client 时写入 `status="deleted"` 且 `is_active=false`：`app/api/control_plane.py:916`
- `GET /admin/clients` 默认过滤 `status="deleted"`：`app/api/control_plane.py:821`

**批量操作：用 core 逻辑统一映射**
- `app/core/batch_clients.py`：`apply_batch_action_to_client(...)`、`deduplicate_client_ids(...)`

**前端：不要仅展示 is_active=true**
- `webui/src/views/ClientsKeysView.vue:111`：移除 `filter((c) => c.is_active)`，确保禁用 Client 仍可见
- `webui/src/api/clients.ts:8`：Client 类型补充 `status`

### 5.4 验证

测试与断言更新/新增：
- `tests/integration/test_admin_control_plane.py:355`（创建 client 返回 `status=active`；删除后列表不再返回该 client，DB 内 `status="deleted"`）
- `tests/integration/test_admin_control_plane.py:406`（创建 `is_active=false` 的 client 仍可在列表看到，且 `status="disabled"`）
- `tests/integration/test_batch_clients.py:129`（batch enable/disable/delete 能区分并落库 `status`）
- `tests/unit/test_batch_clients.py:20`（core 映射逻辑的单测）

迁移：
- `migrations/versions/0007_add_status_to_clients.py`

---

## 6. 文档/契约同步（避免语义漂移）

本次同步更新了接口契约，明确两类错误体与 Client 状态字段：
- `docs/MVP/Firecrawl-API-Manager-API-Contract.md:62`：控制面（`/admin/*`）错误体 `{request_id, error:{...}}`
- `docs/MVP/Firecrawl-API-Manager-API-Contract.md:85`：数据面/兼容层（`/api/*`、`/v1/*`、`/v2/*`）网关自建错误体采用 Firecrawl 兼容结构 `{success:false,error,code}`
- `docs/MVP/Firecrawl-API-Manager-API-Contract.md:300`：Client 列表/创建响应补充 `status`
- `docs/MVP/Firecrawl-API-Manager-API-Contract.md:616`：补充 v2 辅助端点与 team/* 端点列举

---

## 7. 回归验证命令（落地时使用）

```bash
# 质量门禁
.\.venv\Scripts\ruff.exe check .

# 测试 + 覆盖率门禁（≥80%）
.\.venv\Scripts\python.exe -m pytest --cov=app --cov-fail-under=80
```

---

## 8. 后续建议（非阻塞）

- 已为 `app/core/resource_binding.py` 补齐单测，并修复续期路径下可能出现的“naive/aware datetime 不能比较”问题：
  - 测试：`tests/unit/test_resource_binding.py`
  - 修复：`app/core/resource_binding.py`
- 建议在后续单独提交一次“仅格式化（ruff format）”变更，减少业务 diff 噪声。
