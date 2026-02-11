# Firecrawl API Manager 技术方案与架构设计（MVP → 生产）

> 基于：`Firecrawl-API-Manager-PRD.md`（v1.1，2026-02-10）
>
> 单一事实来源：本仓库的技术方案以 `agent.md` 为准；API 接口契约见 `Firecrawl-API-Manager-API-Contract.md`。

## 0. 背景与目标
Firecrawl API Manager（下称 **FCAM**）是一个可容器化部署的轻量级 HTTP 网关服务，用于：
- **集中管理多把 Firecrawl API Key**（加密落库、状态追踪、轮换）
- 对内向多个业务服务提供统一入口：`/api/*`（**鉴权 / 限流 / 配额 / 并发治理**）
- 对运维提供控制面：`/admin/*`（**Key/Client 管理 + 审计日志**）
- 通过 **Key 池轮询 + 429 冷却**，降低单 Key 速率限制对业务的影响

本设计文档重点覆盖：**技术栈选型、模块拆分、核心流程、数据一致性策略、安全边界、可观测、Docker 部署与扩展**，并对照 PRD 给出“可验收”的实现落点。

## 1. 设计原则与范围
### 1.1 设计原则
- **数据面/控制面强隔离**：至少逻辑隔离；生产建议端口/网段隔离
- **最小暴露面**：只允许转发白名单路径到指定 `base_url`；限制体积/超时/头
- **状态可控**：Key/Client 的配额、冷却、并发必须有一致性策略（MVP 与生产分层）
- **可观测优先**：请求链路（request_id）、结构化日志、可统计指标、审计追溯
- **容器友好**：12-factor 配置、健康检查、无状态（生产可水平扩展）

### 1.2 MVP 范围（与 PRD 对齐）
- `/api/*`：鉴权、转发、Key 轮询、429 冷却、重试、Client 维度治理（限流/配额/并发）
- `/admin/*`：Key/Client CRUD、统计、请求日志、审计日志
- SQLite（单实例）可跑通；生产推荐 Postgres +（可选）Redis

### 1.3 非目标（本阶段不做或可选）
- 完整前端管理台（当前仅提供最小内置 WebUI：`/ui`；更复杂能力可先用 Swagger/脚本）
- 多 Provider（ScrapingBee/Apify）——预留接口
- 复杂成本计费/credit 精准扣账（除非 Firecrawl 提供可依赖字段）

## 2. 总体架构
### 2.1 逻辑架构（数据面 / 控制面）
```text
┌───────────────────────────┐
│   业务服务（多个）         │
│   - Service A/B/C          │
│   - Bearer Token / mTLS    │
└─────────────┬─────────────┘
              │
              ▼
┌───────────────────────────┐
│ FCAM Data Plane (/api/*)   │
│ - Client 鉴权               │
│ - Client 限流/配额/并发治理  │
│ - Key Pool 选择/并发/冷却    │
│ - 转发 + 重试 + 幂等（可选） │
│ - Request Log/指标           │
└─────────────┬─────────────┘
              │
              ▼
┌───────────────────────────┐
│ Firecrawl API (/v1/*)      │
└───────────────────────────┘

┌───────────────────────────┐
│ FCAM Control Plane         │
│ - /admin/* Admin Token      │
│ - Key/Client 管理 + 审计日志 │
└───────────────────────────┘
```

### 2.2 生产部署架构（推荐）
```text
            (内网/VPN/零信任)
                    │
                    ▼
        ┌──────────────────────┐
        │ Reverse Proxy / WAF   │  TLS 终止 / mTLS / IP allowlist
        └───────────┬──────────┘
                    │
      ┌─────────────┴─────────────┐
      │                           │
      ▼                           ▼
┌───────────────┐           ┌───────────────┐
│ FCAM (replica)│   ...     │ FCAM (replica)│  无状态水平扩展
└───────┬───────┘           └───────┬───────┘
        │                           │
        ├──────────────┬────────────┤
        ▼              ▼            ▼
   ┌────────┐     ┌────────┐   ┌──────────┐
   │Postgres│     │ Redis  │   │ Observab. │  Prometheus / Loki / OTEL
   └────────┘     └────────┘   └──────────┘
```

> 说明：Redis 主要用于 **分布式限流/并发计数/冷却状态**（多实例一致性）。只有单实例时可不引入。

## 3. 技术栈（推荐）
### 3.1 语言与运行时
- Python：**3.11+**（建议，async 性能/生态更佳；兼容最低 3.9+ 取决于团队）
- ASGI：Uvicorn；生产建议 Gunicorn+UvicornWorker（如不依赖进程内状态）

### 3.2 Web 框架与依赖
- FastAPI + Pydantic（请求/响应校验、OpenAPI）
- httpx（异步转发到 Firecrawl）
- tenacity（可控重试策略，按错误类型区分）
- cryptography（Key 加密：建议 AES-GCM 或 Fernet；见 6.2）

### 3.3 数据与状态
- ORM：SQLAlchemy 2.x（可同步或异步；生产建议 async + asyncpg）
- Migration：Alembic
- DB：
  - MVP：SQLite（WAL 模式 + 单实例；并发写能力有限）
  - 生产：PostgreSQL（并发/锁/事务能力更适配“计数器 + 资源分配”场景）
- 可选缓存/分布式状态：Redis（rate-limit、并发、冷却、幂等缓存）

### 3.4 鉴权与安全
- 数据面（/api/*）：Bearer Token（MVP），生产建议 mTLS 或 mTLS + token 双因子
  - token 仅返回一次，数据库仅存 `token_hash`（确定性哈希用于查找）
- 控制面（/admin/*）：Admin Token（独立于数据面），建议再叠加 IP allowlist

### 3.5 可观测性
- 日志：标准 logging 输出 **JSON 结构化日志**（或 structlog）
- 指标：Prometheus（/metrics）或 OpenTelemetry Metrics
- Trace：OpenTelemetry（可选；通过 request_id/trace_id 串联）

## 4. 模块与代码层架构（建议目录）
> 目的：让“转发逻辑”和“治理逻辑”可测试、可替换，便于未来扩展到多 Provider。

```text
app/
  main.py                  # FastAPI app 组装
  config.py                # 配置加载（env + config.yaml）
  api/
    data_plane.py          # /api/* 路由
    control_plane.py       # /admin/* 路由
    deps.py                # 依赖注入（db session, auth, limiter）
  core/
    auth.py                # client/admin 鉴权
    forwarder.py           # httpx 转发、头/路径/超时约束
    key_pool.py            # Key 选择、冷却、并发 lease
    policy.py              # client 限流/配额/并发校验
    idempotency.py         # X-Idempotency-Key 处理
    redact.py              # 日志/响应脱敏
  db/
    models.py              # 表模型
    session.py             # 连接与事务
    migrations/            # Alembic
  observability/
    logging.py             # request_id 注入、JSON formatter
    metrics.py             # 指标定义
```

## 5. 关键数据模型（与 PRD 一致，补充索引/约束建议）
PRD 已给出 SQLite 建表草案，本设计补充“生产可用性”的建议：

### 5.1 api_keys（Key 池）
- 必备字段：`api_key_ciphertext`、`api_key_hash`（去重/查找）、`daily_quota`、`daily_usage`、`cooldown_until`、`max_concurrent`
- 建议索引：
  - `idx_api_keys_active`：`(is_active, status)`
  - `idx_api_keys_cooldown`：`(cooldown_until)`
  - `idx_api_keys_quota_reset`：`(quota_reset_at)`
- 计数一致性：
  - `daily_usage`：用于配额决策（见 7.2 计数策略）
  - `current_concurrent`：用于并发 lease（见 7.1）

### 5.2 clients（调用方）
- token：仅存 `token_hash`（推荐 `SHA-256(token)` 或 `HMAC(master, token)`）
- 建议索引：`idx_clients_active`：`(is_active)`

### 5.3 request_logs（请求日志）
- 高写入表：生产建议做 **保留策略**（按天分区或定期清理）
- 建议字段：
  - `request_id`（对外返回）、`trace_id`（如接入 OTEL）
  - `retry_count`、`selected_key_ids`（可选，便于诊断 429）

### 5.4 audit_logs（审计日志）
- 只记录控制面操作：key/client 的增删改/禁用/轮换、配额重置等
- 建议至少包含：actor、ip、ua、resource、diff（可选）

### 5.5 idempotency_records（幂等）
- 建议：只对 `crawl/agent` 强制启用
- response_body 存储策略：
  - MVP：存 `task_id` + `status_code` 足够（避免存大响应）
  - 生产：可存压缩后的裁剪响应或仅存引用

## 6. 安全设计
### 6.1 威胁模型（核心）
- API Key 泄露（日志/DB/异常栈/调试接口）
- 被当作通用代理（SSRF/转发任意 Host）
- token 被撞库/重放
- 管理面被暴露（admin token 泄露后可直接拿到 Key 管理能力）

### 6.2 机密保护（Key 加密与脱敏）
- Key 落库：**仅存密文** + `hash` + `last4`
- 加密算法建议：
  - MVP：Fernet（简单、内置随机 IV、易用）
  - 生产：AES-256-GCM（更通用；需要保存 nonce/associated data）
- 主密钥注入：
  - `FCAM_MASTER_KEY` 通过 Docker secret 或环境变量注入
  - 支持轮换：保留 `key_version` + 双写/渐进重加密（未来扩展）
- 日志脱敏：
  - 禁止输出：`Authorization`、`api_key_ciphertext`、明文 token
  - 统一打码：`fc-****5678` / `<redacted>`

### 6.3 网关约束（防 SSRF/滥用）
- 仅允许：`firecrawl.base_url` + 白名单路径（scrape/crawl/search/agent）
- 丢弃/覆盖：`Authorization`、`Host`、`X-Forwarded-*`（按策略）
- 限制请求：
  - `max_body_bytes`（默认 1MB，可配）
  - `timeout`（默认 30s，可配）
  - `content-type` 白名单（如 `application/json`）

### 6.4 接入边界（对外鉴权）
- 数据面：
  - MVP：Bearer Token（每个 client 一个 token）
  - 生产：建议 mTLS；token 作为二级授权/灰度控制
- 控制面：
  - Admin Token + IP allowlist（或仅内网暴露 + VPN/堡垒机）
  - 可选：将 control plane 放到独立端口 `admin_port`

## 7. 核心流程设计（转发、选 Key、限流、并发、重试）
### 7.1 “资源分配”模型：Lease（并发）+ Cooldown（冷却）
目标：在高并发下保证不会把某个 Key 的并发打爆，并能在 429 后快速降温。

建议实现为“Key Lease”：
1. 从候选 Key 集合中选一个（排除禁用/配额用尽/冷却中）
2. **原子化获取并发名额**（`current_concurrent + 1` 不超过 `max_concurrent`）
3. 请求结束后释放 lease（`current_concurrent - 1`）

一致性实现分层：
- 单实例 MVP：进程内 `asyncio.Semaphore` + DB 状态（重启并发计数可重置为 0）
- 多实例生产：Redis 原子计数（推荐）或 Postgres 行锁（可行但复杂）

### 7.2 配额计数策略（Key 与 Client）
配额计数通常有三种口径（建议做成配置项，默认“成功计数”）：
1. **成功计数**：仅当 Firecrawl 返回成功（如 2xx）才 `usage++`（最接近“真实可用”）
2. 尝试计数：只要发起一次下游调用就 `usage++`（更保守，避免超发）
3. 任务计数：对 crawl/agent 以“创建成功返回 task_id”为准

实现建议：
- 先做“成功计数”，并在日志中记录“尝试次数/重试次数”，方便对账与回放。
- quota_reset：优先惰性重置（每次读取时若 `quota_reset_at != today` 则重置），避免依赖容器内定时任务可靠性。

### 7.3 429/5xx 重试与切 Key
重试策略（与 PRD 对齐）：
- 最多重试 `max_retries=3`，每次尽量切换不同 Key
- 仅对明确可重试：429、网络超时、部分 5xx
- 4xx 参数错误不重试

429 冷却处理：
- 若 Firecrawl 返回 `Retry-After`：`cooldown_until = now + retry_after`
- 否则：`cooldown_until = now + cooldown_seconds`（默认 60s）
- 冷却期内 Key 不参与选择

### 7.4 幂等（crawl/agent 强烈建议强制）
幂等键：`X-Idempotency-Key`
- Key：`client_id + idempotency_key`
- 防碰撞：存 `request_hash`（相同幂等键但不同请求体直接 409）
- 记录状态：
  - `in_progress`：返回 409（`IDEMPOTENCY_IN_PROGRESS`，带 `Retry-After`；例如首次请求尚未完成/上游超时）
  - `completed`：直接返回历史响应（通常只需 task_id）
  - `failed`：可选（后续扩展）：用于允许“同 key 重试”或输出更明确的失败语义

实现提示：
- MVP 默认可选启用；如需强制，可通过配置 `idempotency.require_on=["crawl","agent"]` 让缺失幂等键直接返回 400（`IDEMPOTENCY_KEY_REQUIRED`）。

## 8. 可观测性与“记录能力”
### 8.1 请求日志（request_logs）
- 强制字段：`request_id`、`client_id`、`endpoint`、`status_code`、`response_time_ms`、`api_key_last4`（或 api_key_id）
- 建议：记录 `retry_count`、`error_type`、`cooldown_applied`
- 保留策略：
  - MVP：按天清理（例如保留 7/30 天）
  - 生产：推荐日志走 Loki/ELK；DB 中只保留统计聚合或短期明细

### 8.2 指标（Metrics）
建议最少暴露：
- `fcam_requests_total{endpoint,method,status_code,client_id}`
- `fcam_request_duration_ms_bucket{endpoint,method}`
- `fcam_key_selected_total{key_id}`
- `fcam_key_cooldown_total{key_id}`
- `fcam_quota_remaining{scope,id}`

## 9. Docker 部署与扩展
### 9.1 MVP（单机单实例，SQLite）
- 适用：个人/小团队内网使用、低并发
- 建议：
  - `./data` 挂载 volume（SQLite 文件）
  - `./logs` 挂载 volume（应用日志）
  - 仅 1 worker（避免多进程对 SQLite/进程内计数造成歧义）

### 9.2 生产（可水平扩展）
推荐组合：
- FCAM 多实例 + Postgres（强一致计数/审计/配置）
- Redis（分布式限流/并发/冷却状态，保证多实例一致性）
- Reverse Proxy（TLS/mTLS、IP allowlist、路径路由、限流兜底）

扩展要点：
- FCAM 尽量保持无状态：配置、Key/Client、计数器落在 Postgres/Redis
- 所有实例对外等价：便于滚动升级与弹性扩缩容
- 控制面隔离：推荐仅内网暴露 `/admin/*`；或通过 `server.enable_data_plane/enable_control_plane` 拆为两实例/两端口

## 10. 与 PRD 验收标准对齐（“验证”清单）
- 密钥管理（增删查改/启用禁用/测试）：对应 `/admin/keys*` + `api_keys`（密文存储）
- `/api/*` 必须鉴权：`clients.token_hash` + 中间件鉴权（或 mTLS）
- `/admin/*` 必须鉴权且有审计：Admin Token + `audit_logs`
- 转发可用且不泄露密钥：forwarder 覆盖 `Authorization` + redact
- 429 自动切 Key：cooldown + retry + key_pool
- 配额到达自动切 Key：quota 检查 + key_pool 跳过
- 每日重置配额：惰性重置（或定时任务）
- 请求日志记录：`request_logs` + retention
- 提供配额统计：`/admin/stats/quota`

## 11. 配置项（落地建议）
PRD 的 `config.yaml` 已覆盖主要项；建议再补充：
- `server.trust_proxy_headers`（是否信任 X-Forwarded-*）
- `server.enable_docs`（是否开放 `/docs` 与 `/openapi.json`；生产建议关闭）
- `server.enable_data_plane`（是否挂载 `/api/*`）
- `server.enable_control_plane`（是否挂载 `/admin/*` 与 `/ui`；用于端口隔离/仅内网暴露）
- `logging.format=json|plain`
- `observability.metrics_enabled`、`observability.metrics_path=/metrics`
- `observability.retention.request_logs_days`、`observability.retention.audit_logs_days`
- `database.url`（可选；优先于 `database.path`，便于切换 SQLite/PG）
- `state.mode=memory|redis`（多实例时建议 `redis`）
- `state.redis.url`、`state.redis.key_prefix`

---

## 12. 接口契约（前后端/调用方对齐）
- 详细接口清单、请求/响应、错误体、过滤与分页：见 `Firecrawl-API-Manager-API-Contract.md`。
- 本文档（`agent.md`）只负责“语义定稿 + 一致性与实现边界 + 失败策略”，避免与接口契约重复漂移。
- 路径映射约定：`firecrawl.base_url` **包含** `/v1`；例如 `POST /api/scrape` → 上游 `POST {base_url}/scrape`（完整示例见接口契约）。

## 13. 治理语义定稿（Client × Key 两层）
> 目标：让“限流/配额/并发/重试/日志”具备可预测口径，便于验收与排障。

### 13.1 执行顺序（数据面 /api/*）
对每个入站请求，按以下顺序处理（先拦截 cheap 的，再做下游调用）：
1. 生成 `request_id`（回写 `X-Request-Id`），记录 start 时间
2. **Client 鉴权**（Bearer token 或 mTLS）→ 得到 `client_id`
3. **Client 治理**（按 client）：
   - 并发：`client.max_concurrent`
   - 限流：`client.rate_limit_per_min`
   - 配额：`client.daily_quota`（可选，不配则不检查）
4. **请求约束**：路径白名单、body 大小、content-type、超时上限
5. **幂等**（建议强制）：对 `POST /api/crawl`、`POST /api/agent` 若缺 `X-Idempotency-Key` 可直接拒绝（MVP 可先“强烈建议”，生产建议“强制”）
6. **Key Pool 调度**：选择可用 key（跳过 disabled / cooling / quota_exceeded / failed），并获取 key 并发 lease
7. 转发到 Firecrawl：覆盖 `Authorization: Bearer <firecrawl_api_key>`
8. 处理上游响应：429 冷却、可重试错误切 key 重试
9. 释放并发 lease；写入请求日志/统计；返回响应

### 13.2 配额计数口径（默认：成功计数）
必须明确“配额是否按成功计数”。建议配置项：`quota.count_mode`：
- `success`（默认）：**仅当最终响应为 2xx** 时 `daily_usage += 1`（你已指定使用该模式）
- `attempt`：每次发起一次上游调用就计数（更保守，适合“上游按尝试计费”的场景）

> 重试是否消耗配额：
>- 在 `success` 口径下：失败重试不计入 `daily_usage`，最终成功才计 1 次
>- 在 `attempt` 口径下：每次尝试都计 1 次（包括重试）

补充说明（与 Firecrawl 成本口径相关）：
- Firecrawl 的计费以 credits 为主，并且不同端点/特性（例如 enhanced proxy、特定 Agent）可能带来不同的 credits 消耗；官方 FAQ 也提到“失败请求通常不计费，但 FIRE-1 相关请求即使失败也会计费”。因此默认采用 `success` 口径更贴近“成功才扣”的体验，但若你后续需要“按成本最保守”控制，可将 `quota.count_mode` 切到 `attempt`，或针对 `/api/agent` 做单独口径策略（后续实现可扩展）。

### 13.3 每日重置判定（时区与实现）
PRD 写“每日 00:00 重置”，但必须补充时区：
- 背景：Firecrawl 官方的用量/账期接口返回 `billingPeriodStart/billingPeriodEnd`（RFC3339 + `Z`），其账期边界口径为 **UTC**（且更偏“账期/月度”而非“每日”）。因此本项目的“每日配额重置”属于**网关内部治理口径**，并不等同于 Firecrawl 的账期重置。
- 配置项：`quota.timezone`（**默认 `UTC`**；如业务更希望按本地日历日统计，可改为 `Asia/Shanghai` 等）
- 重置方式：**惰性重置**（lazy reset）
  - 每次读取 key/client 时，计算“当前业务日期”（按 `quota.timezone`）
  - 若 `quota_reset_at != today`：将 `daily_usage` 置 0，并更新 `quota_reset_at=today`

### 13.4 重试是否记日志
建议：**每个入站请求写 1 条 request_log**，并补充字段描述“内部尝试情况”：
- `retry_count`：重试次数
- `api_key_id`：最终成功（或最终失败）的 key
- 可选：`key_ids_tried`（若担心体积，可不落库，仅在 debug 日志输出）

> 这样既保证查询简单，又能回溯“为什么一直 429/切 key”。

## 14. 失败模式与返回码策略（网关自建错误）
> 说明：`/api/*` 的上游错误通常透传；下表仅覆盖“网关自身拦截/治理/依赖不可用”的响应策略。

| 场景 | 建议 HTTP | error.code | Retry-After | 说明 |
|---|---:|---|---|---|
| Client 未鉴权/Token 错误 | 401 | CLIENT_UNAUTHORIZED | 否 | 缺/错 token |
| Client 被禁用 | 403 | CLIENT_DISABLED | 否 | 管理端禁用 |
| Client 限流（rpm） | 429 | CLIENT_RATE_LIMITED | 是（秒） | 建议基于令牌桶/滑窗 |
| Client 配额用尽（daily） | 429 | CLIENT_QUOTA_EXCEEDED | 是（到下次重置） | 按 `quota.timezone` 计算 |
| Client 并发超限 | 429 | CLIENT_CONCURRENCY_LIMITED | 可选 | 可返回建议 backoff |
| 未配置任何可用 Key | 503 | NO_KEY_CONFIGURED | 否 | 运维配置问题 |
| 所有 Key 都 disabled | 503 | ALL_KEYS_DISABLED | 否 | 需要人工介入 |
| 所有 Key 都 cooling | 429 | ALL_KEYS_COOLING | 是（最小剩余冷却秒数） | 可计算 `min(cooldown_until-now)` |
| 所有 Key 都 quota_exceeded | 429 | ALL_KEYS_QUOTA_EXCEEDED | 是（到下次重置） | “服务侧总配额耗尽” |
| DB 不可用（鉴权/计数/调度依赖） | 503 | DB_UNAVAILABLE | 否 | 建议 fail-close：拒绝数据面请求 |
| 上游请求超时且重试无效 | 504 | UPSTREAM_TIMEOUT | 可选 | 同时记录 request_log |

错误体结构与示例：见 `Firecrawl-API-Manager-API-Contract.md`（通用错误体）。

## 15. 一致性与实现边界（“怎么原子”）
### 15.1 SQLite（MVP 单实例）建议实现
约束：SQLite + 高并发 + 多进程 很难做强一致的“并发 lease/计数”，因此 **MVP 约束为单实例/单进程**。

推荐策略（MVP）
- **并发控制（Key/Client）权威在进程内**：`asyncio.Semaphore`
  - 优点：简单，不会出现 DB 崩溃导致的“并发计数泄露”
  - 代价：进程重启后并发计数归零（但重启意味着 in-flight 请求已中断，影响可接受）
- **配额与冷却（Key/Client）权威在 DB**：`daily_usage/quota_reset_at/cooldown_until/status`
- **原子配额更新**：用 DB 事务 + 条件更新（避免并发下超发）
  - 示例语义：`UPDATE ... SET daily_usage=daily_usage+1 WHERE id=? AND daily_usage < daily_quota`

影响边界说明
- 重启后：
  - `current_concurrent` 若仅在内存：归零；不会卡死 key
  - `cooldown_until` 与 `daily_usage` 仍在 DB：冷却/配额不会因重启丢失

### 15.2 Postgres（生产，多实例）建议实现
目标：在多实例下仍保证 key 的并发与配额不会被打穿。
可选两条路线：
1. **Redis 作为分布式状态权威**（推荐）
   - key/client 并发计数、限流、cooldown 放 Redis（原子 INCR/EXPIRE 或 Lua）
   - 配额计数可在 Redis 做快速判断，最终落库对账（或直接在 DB 做强一致）
2. **DB 强一致（事务 + 行锁）**
   - 候选 key：`SELECT ... FOR UPDATE SKIP LOCKED` 选一个可用 key
   - 并发 lease：在同一事务内检查/更新 `current_concurrent`
   - 配额：同事务内 `daily_usage += 1`（按口径）

## 16. Key 状态机（status 迁移规则）
状态枚举（PRD）：`active | cooling | quota_exceeded | failed | disabled`

### 16.1 迁移触发
- `active → cooling`：上游返回 429
  - `cooldown_until = now + retry_after(优先) | cooldown_seconds(默认)`
- `cooling → active`：`now >= cooldown_until` 且未配额用尽且未被禁用
- `active → quota_exceeded`：`daily_usage >= daily_quota`（按 `quota.count_mode` 更新后触发）
- `quota_exceeded → active`：每日惰性重置后（`quota_reset_at` 切换到 today）
- `* → disabled`：
  - 管理端手动禁用（`is_active=false` 或显式 disable）
  - **建议默认**：上游返回 401/403（key 失效/权限不足）自动禁用（避免反复失败）
- `active → failed`（软失败，建议实现为“临时失败窗口”而非永久）：
  - 连续网络错误/超时/5xx 达到阈值（例如 3 次）
  - 可设置 `failed_until`（或复用 `cooldown_until`）做短期退避
- `failed → active`：管理员 test 成功或失败窗口过去

### 16.2 /admin/keys/{id}/test 的恢复语义（建议）
- 测试成功：清理 `cooldown_until`、清理失败计数，置 `status=active`
- 测试 429：置 `status=cooling` 并更新 `cooldown_until`
- 测试 401/403：置 `status=disabled`（或 `is_active=false`）

## 17. 运维落地（DoD）与可执行条目
### 17.1 探活
- `/healthz`：进程存活即可（不连 DB）
- `/readyz`：必须能连 DB；并验证关键配置（如 master key/admin token 已注入）

### 17.2 配置优先级（建议实现）
1. 内置默认值
2. `config.yaml`（路径可通过 `FCAM_CONFIG` 指定）
3. 环境变量覆盖（含 Docker secret 注入）

其中机密类建议只走 env/secret：`FCAM_MASTER_KEY`、`FCAM_ADMIN_TOKEN`。

### 17.3 日志脱敏字段清单（建议默认）
必须脱敏/禁止落日志：
- `Authorization`（client/admin 与 Firecrawl key）
- `api_key`、`api_key_ciphertext`、`token`、`cookie`、`set-cookie`
- 任意可能包含密钥的 query/body 字段（至少对 `api_key`、`authorization` 做统一打码）

### 17.4 TTL/保留策略（可执行）
- `request_logs`：保留 N 天（如 7/30），超期清理（定时任务或启动时/惰性清理）
- `audit_logs`：保留更久（如 90 天或永久，取决于合规）
- `idempotency_records`：TTL 24h（或按业务配置），到期清理

落地建议（MVP）：
- 使用外部 cron/定时任务调用 `scripts/cleanup.py`（读取 `config.yaml` + env 覆盖）
- `request_logs/audit_logs` 保留天数：`observability.retention.*`
- 幂等 TTL：`idempotency.ttl_seconds`

### 17.5 最小测试矩阵（建议）
集成测试建议用 “Mock Firecrawl” 本地服务（或 httpx mock）覆盖：
- 鉴权：client/admin token 正常/缺失/错误/禁用
- Key 选择：round-robin、公平性、跳过 cooling/quota_exceeded/disabled
- 429 冷却：遵守 `Retry-After`、冷却到期恢复
- 配额：success/attempt 两种口径；每日惰性重置（含时区）
- 并发：client/key 并发上限回归（并发压测小规模即可）
- 幂等：`POST /api/crawl`、`POST /api/agent` 的重复请求返回一致（request_hash 冲突返回 409）
- 失败模式：无 key / 全冷却 / DB 不可用 / 上游超时 → 返回码与错误体符合第 14 节

---

## 18. 开发规则（固定，强制执行）
> 本节为项目工程纪律：后续实现与迭代必须遵守。

### 18.1 文档优先
- 任何接口/语义/失败码/状态机的调整，必须先更新文档：`agent.md`、`Firecrawl-API-Manager-API-Contract.md`（必要时同步 PRD），再提交代码实现。
- `agent.md` 负责“语义与策略”；`Firecrawl-API-Manager-API-Contract.md` 负责“接口契约”；禁止在多处重复维护同一事实（避免漂移）。

### 18.2 日志要求（越全面越好，但必须安全）
- 对所有“可能出问题/需要排障”的分支必须打日志（鉴权失败、限流/配额/并发拦截、选 key 失败、429 冷却、重试、上游超时、DB 异常等）。
- 日志必须结构化（JSON 或等价字段），并包含 `request_id`；禁止输出明文 `Authorization`、token、api_key 等敏感信息（严格脱敏）。

### 18.3 测试要求（覆盖率与最小粒度）
- 自动化测试覆盖率：**≥ 80%**（以 `app/` 业务代码为统计范围）。
- **每个函数/每个类至少 1 个测试用例覆盖**（以 `app/` 业务代码为准）。
- 关键路径必须有集成测试（mock Firecrawl）：并发、幂等、冷却、配额、失败模式回归。

### 18.4 质量门禁（建议落到 CI）
- `pytest --cov=app --cov-fail-under=80`
- 任何失败策略表（第 14 节）中列出的场景必须有测试用例覆盖并可重复运行。

## 19. 实施任务清单（执行入口）
- 实施任务清单（TD）：见 `TD.md`。（`TODO.md` 仅为兼容入口，避免重复维护）
