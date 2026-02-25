# Firecrawl API Manager 接口契约（MVP）

> 对齐文档：`docs/MVP/Firecrawl-API-Manager-PRD.md:343`（核心端点清单）  
> 目标：让前后端/调用方在不读代码的情况下，能明确“怎么调、会返回什么、失败怎么处理、如何分页过滤”。

## 1. 通用约定

### 1.1 Base URL
- 网关（FCAM）：`http(s)://<fcam_host>:<port>`
- 上游（Firecrawl）：由配置 `firecrawl.base_url` 决定（推荐：`https://api.firecrawl.dev`）

> 约定：`firecrawl.base_url` 建议配置为上游 root（不包含版本号）；FCAM 会根据入站路径转发到对应版本：  
> - `/api/*` 与 `/v1/*` → 上游 `/v1/*`  
> - `/v2/*`           → 上游 `/v2/*`  
>
> 兼容：允许 `firecrawl.base_url` 以 `/v1` 或 `/v2` 结尾（历史/特殊配置），FCAM 会自动去重版本前缀，避免出现 `/v1/v1` 或 `/v1/v2` 的重复拼接。

完整 URL 示例（假设 `firecrawl.base_url=https://api.firecrawl.dev`）：
- `POST http://localhost:8000/api/scrape` → `POST https://api.firecrawl.dev/v1/scrape`
- `POST http://localhost:8000/api/crawl` → `POST https://api.firecrawl.dev/v1/crawl`
- `GET  http://localhost:8000/api/crawl/abc123` → `GET https://api.firecrawl.dev/v1/crawl/abc123`
- `POST http://localhost:8000/v2/scrape` → `POST https://api.firecrawl.dev/v2/scrape`

### 1.2 Content-Type
- 除 `GET` 外，默认只接受：`Content-Type: application/json`
- 响应默认：`application/json`（转发端点会透传上游的 `Content-Type`）

### 1.3 鉴权
#### 数据面（/api/*）
- Header：`Authorization: Bearer <CLIENT_TOKEN>`
- 每个业务服务对应一个 client token（由 `/admin/clients` 创建）

#### 控制面（/admin/*）
- Header：`Authorization: Bearer <ADMIN_TOKEN>`
- Admin token 不与数据面 token 复用；建议配合 IP allowlist / VPN / 仅内网暴露

### 1.4 请求 ID
- 网关为每个请求生成 `request_id`，并在响应头返回：`X-Request-Id: <request_id>`
- 网关自身产生的错误响应体也会包含该 `request_id`
- 若客户端传入 `X-Request-Id`，网关可选择沿用（实现细节），但仍会校验格式并避免日志注入

### 1.4.1 幂等键（建议 / 可强制）
- Header：`X-Idempotency-Key: <string>`
- 建议用于：`POST /api/crawl`、`POST /api/agent`
- 若同 `client_id + idempotency_key` 且请求体一致：直接返回历史响应（避免重复创建任务）
- 若启用强制且缺失：返回 400（`IDEMPOTENCY_KEY_REQUIRED`）
- 若同 `client_id + idempotency_key` 但请求体不同：返回 409（`IDEMPOTENCY_KEY_CONFLICT`）
- 若同 `client_id + idempotency_key` 的请求正在处理：返回 409（`IDEMPOTENCY_IN_PROGRESS`），并带 `Retry-After`

### 1.5 时间格式与时区
- `timestamp`：RFC3339（必须带时区；示例可能为 `UTC`，如 `2026-02-10T06:33:21Z`）
- `date`：`YYYY-MM-DD`（用于每日配额重置口径；与配置 `quota.timezone` 一致）

默认口径（已定稿）：
- `quota.timezone=UTC`（与 Firecrawl 账期时间戳口径对齐）
- `quota.count_mode=success`（成功计数：仅最终 2xx 才消耗 1 次配额）

备注：若后续对 `/api/agent` 引入“失败也计费”的上游能力（取决于 Firecrawl 计费规则/模型；例如 FIRE-1 相关请求可能例外），可在实现中为该端点单独配置更保守的计数口径。

补充：Firecrawl 的 credits 更偏“账期/周期”口径；本项目的 `daily_quota`/`quota_reset_at` 属于网关内部治理字段。

### 1.6 通用错误体（网关自建错误）
> 说明：`/api/*` 在“上游透传模式”下，上游错误通常**不包装**（保持 Firecrawl 兼容）。只有当错误由网关产生（鉴权/限流/无 Key/网关校验失败等）时，返回以下格式。

```json
{
  "request_id": "01JKXYZ...",
  "error": {
    "code": "CLIENT_UNAUTHORIZED",
    "message": "Missing or invalid client token",
    "details": {
      "hint": "Use Authorization: Bearer <CLIENT_TOKEN>"
    }
  }
}
```

当返回 429 类错误时，网关建议同时返回响应头：
- `Retry-After: <seconds>`（整数秒）

- `error.code`：稳定枚举，便于调用方做自动化处理
- `error.message`：人类可读
- `error.details`：可选，结构化信息（不包含敏感数据）

### 1.7 分页约定（日志类接口）
日志类接口采用 **游标分页**（按 `id` 倒序）：
- Query：
  - `limit`：返回条数（默认 50，最大 200）
  - `cursor`：可选；表示“从该 id 之前继续翻页”（即 `id < cursor`）
- Response：
  - `items`：数据数组（倒序）
  - `next_cursor`：下一页游标（为空表示没有更多）
  - `has_more`：是否还有更多

```json
{
  "items": [],
  "next_cursor": 12345,
  "has_more": true
}
```

---

## 2. 控制面（/admin/*）

### 2.1 GET /admin/keys — 获取密钥列表
**Auth**：Admin

**Response 200**
```json
{
  "items": [
    {
      "id": 1,
      "name": "free-01",
      "api_key_masked": "fc-****5678",
      "plan_type": "free",
      "is_active": true,
      "status": "active",

      "daily_quota": 5,
      "daily_usage": 3,
      "quota_reset_at": "2026-02-10",

      "max_concurrent": 2,
      "current_concurrent": 0,

      "rate_limit_per_min": 10,
      "cooldown_until": null,

      "total_requests": 123,
      "last_used_at": "2026-02-10T06:33:21Z",
      "created_at": "2026-02-10T05:00:00Z"
    }
  ]
}
```

字段含义（核心）：
- `api_key_masked`：脱敏展示（**不返回明文**）
- `status`：`active | cooling | quota_exceeded | failed | disabled`
- `quota_reset_at`：该 key 当天配额口径日期（见 4.2“每日重置口径”）

**Errors**
- 401/403：Admin 鉴权失败（`ADMIN_UNAUTHORIZED`）

### 2.2 POST /admin/keys — 添加新密钥
**Auth**：Admin

**Request**
```json
{
  "api_key": "fc-xxxxxxxxxxxxxxxx",
  "name": "free-01",
  "plan_type": "free",
  "daily_quota": 5,
  "max_concurrent": 2,
  "rate_limit_per_min": 10,
  "is_active": true
}
```

**Response 201**
返回新建 key（同 2.1 单项结构，不包含 `api_key` 明文）。

**Errors**
- 400：参数不合法（`VALIDATION_ERROR`）
- 409：重复 key（`API_KEY_DUPLICATE`）

### 2.3 PUT /admin/keys/{id} — 更新密钥配置
**Auth**：Admin

**Request（字段可选，按需更新）**
```json
{
  "name": "free-01",
  "plan_type": "free",
  "daily_quota": 10,
  "max_concurrent": 2,
  "rate_limit_per_min": 10,
  "is_active": true
}
```

**Response 200**
返回更新后的 key。

**Errors**
- 404：不存在（`NOT_FOUND`）

### 2.4 DELETE /admin/keys/{id} — 删除密钥
**Auth**：Admin

**Response 204**：无 body

> 实现说明（MVP）：为保留历史 `request_logs` 的 key 关联，服务端可实现为“软禁用”（`is_active=false,status=disabled`）而非物理删除。

**Errors**
- 404：不存在（`NOT_FOUND`）

### 2.5 POST /admin/keys/{id}/test — 测试密钥健康状态
**Auth**：Admin

**Request（可选）**
```json
{
  "mode": "scrape",
  "test_url": "https://example.com"
}
```

**Response 200**
```json
{
  "key_id": 1,
  "ok": true,
  "upstream_status_code": 200,
  "latency_ms": 120,
  "observed": {
    "cooldown_until": null,
    "status": "active"
  }
}
```

说明：
- 若上游返回 401/403，可按策略将 key 标记为 `disabled`
- 若上游返回 429，将 key 标记为 `cooling` 并写入 `cooldown_until`

### 2.5.1 POST /admin/keys/batch — 批量编辑/批量测试（尽力而为）
**Auth**：Admin

> 语义：对 `ids` 中的每个 key 执行（允许部分成功），返回每项的成功/失败原因；不会因为某一项失败而回滚其他已成功项。若提供 `test`，将以**有限并发**方式执行 key test，以降低大量 keys 顺序测试导致的超时风险。

**Request**
```json
{
  "ids": [1, 2, 3],
  "patch": {
    "is_active": true,
    "plan_type": "free",
    "daily_quota": 10,
    "max_concurrent": 2,
    "rate_limit_per_min": 10,
    "name": "optional"
  },
  "reset_cooldown": true,
  "soft_delete": false,
  "test": {
    "mode": "scrape",
    "test_url": "https://example.com"
  }
}
```

字段说明：
- `ids`：要操作的 key_id 列表（建议 ≤ 200）
- `patch`：对 key 执行字段更新（全部可选；不支持在 batch 内轮换 `api_key` 明文）
- `reset_cooldown`：若为 true，清空 `cooldown_until`；并将 `cooling/failed` 恢复为 `active`（不覆盖 `disabled/quota_exceeded/decrypt_failed`）
- `soft_delete`：若为 true，等价于“批量禁用”（`is_active=false,status=disabled`）
- `test`：若提供，则对每个 key 触发一次 key test（等价于调用 `/admin/keys/{id}/test`，按 `control_plane.batch_key_test_max_workers` 限制并发），并返回 test 结果

**Response 200**
```json
{
  "requested": 3,
  "succeeded": 2,
  "failed": 1,
  "results": [
    {
      "id": 1,
      "ok": true,
      "key": { "id": 1, "api_key_masked": "fc-****5678", "status": "active" },
      "test": { "ok": true, "upstream_status_code": 200, "latency_ms": 123 }
    },
    {
      "id": 999,
      "ok": false,
      "error": { "code": "NOT_FOUND", "message": "Not found" }
    }
  ]
}
```

### 2.6 POST /admin/keys/reset-quota — 手动重置所有密钥配额
**Auth**：Admin

**Response 200**
```json
{
  "ok": true,
  "reset_at": "2026-02-10T00:00:00Z",
  "affected_keys": 12
}
```

---

### 2.7 GET /admin/clients — 获取调用方列表
**Auth**：Admin

**Response 200**
```json
{
  "items": [
    {
      "id": 1,
      "name": "service-a",
      "is_active": true,

      "daily_quota": 1000,
      "daily_usage": 12,
      "quota_reset_at": "2026-02-10",

      "rate_limit_per_min": 60,
      "max_concurrent": 10,

      "created_at": "2026-02-10T05:00:00Z",
      "last_used_at": "2026-02-10T06:33:21Z"
    }
  ]
}
```

### 2.8 POST /admin/clients — 创建调用方（生成 token，仅返回一次）
**Auth**：Admin

**Request**
```json
{
  "name": "service-a",
  "daily_quota": 1000,
  "rate_limit_per_min": 60,
  "max_concurrent": 10,
  "is_active": true
}
```

**Response 201**
```json
{
  "client": {
    "id": 1,
    "name": "service-a",
    "is_active": true,
    "daily_quota": 1000,
    "daily_usage": 0,
    "quota_reset_at": "2026-02-10",
    "rate_limit_per_min": 60,
    "max_concurrent": 10,
    "created_at": "2026-02-10T05:00:00Z",
    "last_used_at": null
  },
  "token": "fcam_client_xxx..."
}
```

说明：
- `token` **仅在创建时返回一次**；服务端只存 `token_hash`

### 2.9 PUT /admin/clients/{id} — 更新调用方限额/状态
**Auth**：Admin

**Request（字段可选）**
```json
{
  "daily_quota": 2000,
  "rate_limit_per_min": 120,
  "max_concurrent": 20,
  "is_active": true
}
```

**Response 200**：返回更新后的 client。

### 2.10 DELETE /admin/clients/{id} — 删除/禁用调用方
**Auth**：Admin

**Response 204**

> 建议实现为“软禁用”（`is_active=false`），避免历史日志失联。

### 2.11 POST /admin/clients/{id}/rotate — 轮换调用方 token（仅返回一次）
**Auth**：Admin

**Response 200**
```json
{
  "client_id": 1,
  "token": "fcam_client_new_xxx..."
}
```

### 2.12 PATCH /admin/clients/batch — 批量操作调用方
**Auth**：Admin

**Request**
```json
{
  "client_ids": [1, 2, 3],
  "action": "enable" | "disable" | "delete"
}
```

参数说明：
- `client_ids`：要操作的 Client ID 列表（必填，最少 1 个，最多 100 个）
- `action`：操作类型
  - `enable`：批量启用
  - `disable`：批量禁用
  - `delete`：批量删除（软删除，设置 `is_active=false`）

**Response 200**
```json
{
  "success_count": 2,
  "failed_count": 1,
  "failed_items": [
    {
      "client_id": 3,
      "error": "Client not found"
    }
  ]
}
```

响应说明：
- `success_count`：成功操作的 Client 数量
- `failed_count`：失败的 Client 数量
- `failed_items`：失败的详细信息（仅包含失败项）

**Errors**
- 400：参数错误（`VALIDATION_ERROR`）
  - `client_ids` 为空
  - `client_ids` 超过 100 个
  - `action` 无效
- 401/403：Admin 鉴权失败（`ADMIN_UNAUTHORIZED`）
- 503：数据库不可用（`DB_UNAVAILABLE`）

**使用场景**：
- 批量启用多个 Client
- 批量禁用多个 Client（例如临时下线某些服务）
- 批量删除多个 Client（软删除，不影响历史日志）

**注意事项**：
- 批量操作在单个数据库事务中执行
- 部分失败时，成功的操作会被提交，失败的会被记录
- 每个 Client 的操作都会记录审计日志
- 重复的 `client_ids` 会被自动去重

---

### 2.12 GET /admin/stats — 统计信息（概览）
**Auth**：Admin

**Response 200（示例）**
```json
{
  "keys": {
    "total": 10,
    "active": 8,
    "cooling": 1,
    "quota_exceeded": 1,
    "disabled": 0,
    "failed": 0
  },
  "clients": {
    "total": 5,
    "active": 5,
    "disabled": 0
  }
}
```

### 2.13 GET /admin/stats/quota — 配额使用统计
**Auth**：Admin

**Query（可选）**
- `include_keys`: `true|false`（默认 `true`，返回每个 key 明细）
- `include_clients`: `true|false`（默认 `false`）

**Response 200**
```json
{
  "summary": {
    "total_quota": 50,
    "used_today": 23,
    "remaining": 27,
    "keys_exhausted": 4,
    "keys_available": 6
  },
  "keys": [
    {
      "id": 1,
      "api_key_masked": "fc-****5678",
      "status": "quota_exceeded",
      "daily_quota": 5,
      "daily_usage": 5,
      "quota_reset_at": "2026-02-10",
      "cooldown_until": null
    }
  ]
}
```

字段口径建议：
- `total_quota`：所有“可用参与调度”的 key 的 `daily_quota` 之和（不含 disabled）
- `used_today`：对应 key 的 `daily_usage` 之和（按 `quota.count_mode` 口径；默认 `success`）

---

### 2.14 GET /admin/logs — 请求日志查询（过滤 + 分页）
**Auth**：Admin

说明：记录数据面入站请求（`/api/*` 与 `/v1/*`）。

**Query**
- 分页：
  - `limit`：默认 50，最大 200
  - `cursor`：上一页返回的 `next_cursor`
- 过滤：
  - `from`：RFC3339（含时区）
  - `to`：RFC3339（含时区）
  - `client_id`：整数
  - `api_key_id`：整数
  - `endpoint`：`scrape|crawl|crawl_status|search|agent`
  - `status_code`：整数
  - `success`：`true|false`
  - `level`：`info|warn|error`（按请求结果推导，用于快速筛选）
  - `q`：模糊搜索（`request_id` / `endpoint` / `error_message`，大小写不敏感）
  - `request_id`：精确匹配
  - `idempotency_key`：精确匹配

**Response 200**
```json
{
  "items": [
    {
      "id": 10001,
      "created_at": "2026-02-10T06:33:21Z",
      "level": "info",
      "request_id": "01JKXYZ...",

      "client_id": 1,
      "api_key_id": 2,
      "api_key_masked": "fc-****5678",

      "method": "POST",
      "endpoint": "scrape",
      "status_code": 200,
      "response_time_ms": 123,
      "success": true,

      "retry_count": 1,
      "error_message": null,
      "error_details": null,
      "idempotency_key": null
    }
  ],
  "next_cursor": 9990,
  "has_more": true
}
```

### 2.15 GET /admin/audit-logs — 审计日志查询（过滤 + 分页）
**Auth**：Admin

**Query**
- 分页：同 2.14
- 过滤：
  - `from` / `to`
  - `actor_type`：`admin|system`
  - `action`：例如 `key.create|key.update|client.rotate|quota.reset`
  - `resource_type`：`api_key|client`
  - `resource_id`：字符串/整数（实现而定）

**Response 200**
```json
{
  "items": [
    {
      "id": 501,
      "created_at": "2026-02-10T06:40:00Z",
      "actor_type": "admin",
      "actor_id": "admin",
      "action": "client.rotate",
      "resource_type": "client",
      "resource_id": "1",
      "ip": "10.0.0.10",
      "user_agent": "curl/8.0"
    }
  ],
  "next_cursor": 480,
  "has_more": true
}
```

---

## 3. 数据面（/api/*）— 转发端点（兼容 Firecrawl）
> 说明：这些接口的 **成功响应与上游错误响应**默认“透传”。  
> 网关仅在“自身拦截/治理失败/无 Key”等场景返回 1.6 的错误体。

### 3.0 Firecrawl 兼容层（/v1/*，用于 SDK 迁移）
**Auth**：Client

说明：为支持“尽量少改代码”的迁移场景，FCAM 额外提供一组与 Firecrawl 路径对齐的兼容端点：
- `POST /v1/scrape`
- `POST /v1/crawl`
- `GET  /v1/crawl/{id}`
- `POST /v1/search`
- `POST /v1/agent`

语义：与 `/api/*` 等价（仅路径不同），鉴权仍为 `Authorization: Bearer <CLIENT_TOKEN>`。

### 3.0.1 Firecrawl 兼容层（/v2/*，用于 SDK 迁移）
**Auth**：Client

说明：FCAM 额外提供一组与 Firecrawl v2 路径对齐的兼容端点（`/v2/*` 透明转发），例如：
- `POST /v2/scrape`
- `POST /v2/crawl`
- `GET  /v2/crawl/{id}`
- `POST /v2/map`
- `POST /v2/search`
- `POST /v2/extract`
- `POST /v2/agent`
- `POST /v2/batch/scrape`
- `GET  /v2/batch/scrape/{id}`

并提供少量别名（为兼容 `start/status` 形态，网关会重写到主路径并在必要时回退）：
- `POST /v2/crawl/start` ↔ `POST /v2/crawl`
- `GET  /v2/crawl/status/{id}` ↔ `GET /v2/crawl/{id}`
- `POST /v2/batch/scrape/start` ↔ `POST /v2/batch/scrape`
- `GET  /v2/batch/scrape/status/{id}` ↔ `GET /v2/batch/scrape/{id}`

### 3.1 POST /api/scrape → 上游 POST {base_url}/v1/scrape
**Auth**：Client

**Request**
- Body：与 Firecrawl `/scrape` 保持一致（网关不要求调用方提供 Firecrawl key）

**Response**
- 透传上游响应

### 3.2 POST /api/crawl → 上游 POST {base_url}/v1/crawl
**Auth**：Client

**建议**：调用方传 `X-Idempotency-Key`（避免重复创建任务）

### 3.3 GET /api/crawl/{id} → 上游 GET {base_url}/v1/crawl/{id}
**Auth**：Client

### 3.4 POST /api/search → 上游 POST {base_url}/v1/search
**Auth**：Client

### 3.5 POST /api/agent → 上游 POST {base_url}/v1/agent
**Auth**：Client

**建议**：调用方强制传 `X-Idempotency-Key`（避免重复创建/重复扣费风险）

---

## 4. 探活
### 4.1 GET /healthz
**Response 200**
```json
{ "ok": true }
```

### 4.2 GET /readyz
**语义**：依赖可用（至少 DB 可连、关键配置/密钥可用）

**Response 200**
```json
{ "ok": true }
```

**Response 503（示例）**
```json
{
  "request_id": "01JKXYZ...",
  "error": { "code": "NOT_READY", "message": "Database unavailable" }
}
```

### 4.3 GET /metrics
**语义**：Prometheus 指标（默认关闭；开启需配置 `observability.metrics_enabled=true`，路径可由 `observability.metrics_path` 修改）

**Response 200（节选）**
```text
# HELP fcam_requests_total Total HTTP requests processed by FCAM
# TYPE fcam_requests_total counter
fcam_requests_total{endpoint="scrape",method="POST",status_code="200",client_id="1"} 1.0
```

---

## 5. 网关错误码（建议枚举）
> 供文档与实现对齐，调用方可基于 `error.code` 做分支处理。

- 鉴权类：`ADMIN_UNAUTHORIZED`、`CLIENT_UNAUTHORIZED`、`CLIENT_DISABLED`
- 治理类：`CLIENT_RATE_LIMITED`、`CLIENT_QUOTA_EXCEEDED`、`CLIENT_CONCURRENCY_LIMITED`
- Key 池类：`NO_KEY_CONFIGURED`、`ALL_KEYS_COOLING`、`ALL_KEYS_QUOTA_EXCEEDED`、`ALL_KEYS_DISABLED`
- 请求约束：`REQUEST_TOO_LARGE`、`UNSUPPORTED_MEDIA_TYPE`、`PATH_NOT_ALLOWED`
- 幂等：`IDEMPOTENCY_KEY_REQUIRED`、`IDEMPOTENCY_KEY_CONFLICT`
- 幂等（补充）：`IDEMPOTENCY_IN_PROGRESS`
- 依赖不可用：`NOT_READY`、`DB_UNAVAILABLE`
- 上游类：`UPSTREAM_TIMEOUT`、`UPSTREAM_UNAVAILABLE`
- 通用：`VALIDATION_ERROR`、`NOT_FOUND`、`INTERNAL_ERROR`
