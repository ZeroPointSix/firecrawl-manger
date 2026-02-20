# TD（实施代办清单 / Task List）

> **DEPRECATED**：`docs/project/` 目录将逐步废弃；新的执行入口请以 `docs/TODO/README.md`（代办清单）与 `docs/PLAN/README.md`（实施计划）为准。  
> 本文件仅保留为历史记录与兼容引用，原则上不再增量维护（避免多处事实来源漂移）。

> 适用范围：按 `docs/MVP/Firecrawl-API-Manager-PRD.md` + `docs/agent.md` + `docs/MVP/Firecrawl-API-Manager-API-Contract.md` 落地实现。  
> 使用方式：从上到下执行；每完成一项就打勾；任何“语义/接口”变更必须同步更新对应文档。

## 0. 全局规则（强制）
- **文档优先**：接口/语义/失败码/状态机调整，必须先改文档（`docs/agent.md`、`docs/MVP/Firecrawl-API-Manager-API-Contract.md`），再改代码。
- **日志优先**：所有“可能出问题”的分支都要有结构化日志（但必须脱敏，不得泄露 token/api_key）。
- **测试优先**：新增/修改的业务逻辑必须补测试；覆盖率 **≥ 80%**；并且**每个函数/每个类至少有 1 个测试用例覆盖**（以 `app/` 业务代码为准）。

## 1. 里程碑（建议）
- **M0（文档冻结）**：语义/契约/失败策略达成一致，可进入编码
- **M1（可运行骨架）**：FastAPI + DB + 鉴权 + Docker 能启动
- **M2（MVP 核心链路）**：/api 转发 + key 池调度 + 429 冷却 + 配额
- **M3（控制面闭环）**：/admin CRUD + stats/quota + logs 查询 + 审计
- **M4（可观测与质量门禁）**：指标/日志/清理策略/覆盖率门禁/回归矩阵
- **M5（生产形态）**：Postgres +（可选）Redis + 多实例一致性方案 + 部署示例
- **M6（可选：最小 WebUI）**：提供内置 `/ui2`，用于最小化操作成本（调用现有 `/admin/*`）

每个里程碑的“完成标准（DoD）”统一要求：
- 对应功能的自动化测试齐全（见 0. 全局规则 + 各阶段测试矩阵）
- 日志可用于排障：关键分支有日志且包含 `request_id`，并通过脱敏测试
- 文档与实现一致（接口/语义/失败码/状态机），不允许“实现先跑、文档滞后”

---

## 2. M0：文档冻结（先做）
- [x] 已冻结 `quota.timezone=UTC`（与 Firecrawl 账期口径对齐；如需本地日历可改为 Asia/Shanghai），并在 `docs/agent.md` 与接口契约中一致
- [x] 已冻结 `quota.count_mode=success`（成功计数），并在 `docs/agent.md` 与接口契约中一致
- [x] 已冻结 `/api/*` 的“上游错误透传 vs 网关包装”策略（默认透传，上游错误不包装）
- [x] 已冻结失败码表（`docs/agent.md` 第 14 节）与错误体字段（`docs/MVP/Firecrawl-API-Manager-API-Contract.md`）
- [x] 已冻结 Key 状态机迁移规则（`docs/agent.md` 第 16 节），包括 401/403 自动 disabled、5xx/timeout 失败退避（阈值/窗口可配置）
- [x] 已冻结“实现模式选择”：
  - [x] MVP：SQLite 单实例/单进程（并发 lease 在内存）
  - [x] 生产：Postgres +（可选）Redis（一致性实现二选一：Redis 原子计数优先；或 DB 行锁）

M0 DoD（完成标志）：
- [x] `docs/agent.md` / `docs/MVP/Firecrawl-API-Manager-API-Contract.md` / `docs/project/TD.md` 内容自洽，且关键默认值已在文档中明确可查

---

## 3. M1：可运行工程骨架
### 3.1 工程化与质量工具
- [x] 初始化目录结构：`app/`、`tests/`、`migrations/`、`scripts/`
- [x] 依赖管理与锁定（推荐 `pyproject.toml` + 可重复安装）
- [x] 代码风格与静态检查（建议 ruff/format/mypy 任选组合）
- [x] 测试框架：pytest + pytest-cov，加入覆盖率门禁（`--cov-fail-under=80`）
- [x] 统一日志：JSON/结构化日志格式、request_id 注入、脱敏过滤器

### 3.2 配置系统
- [x] 配置加载：默认值 → `config.yaml` → env 覆盖（在 `docs/agent.md` 第 17.2 约定）
- [x] 机密注入：`FCAM_ADMIN_TOKEN`、`FCAM_MASTER_KEY`（仅 env/secret）
- [x] 配置校验：启动时校验必需配置缺失则 fail-fast（并在 `/readyz` 反映）

### 3.3 DB 与迁移
- [x] SQLAlchemy 模型对齐 PRD（api_keys/clients/request_logs/audit_logs/idempotency_records）
- [x] Alembic 初始化与首个 migration（SQLite/PG 兼容）
- [x] DB Session/事务封装（支持测试隔离）

### 3.4 探活与基础中间件
- [x] `GET /healthz`（仅进程存活）
- [x] `GET /readyz`（DB 可用 + 关键配置就绪）
- [x] 请求体大小限制、content-type 校验、路径白名单校验（网关约束）

M1 DoD（完成标志）：
- [x] `docker compose up` 可启动服务；`/healthz`=200；`/readyz` 能正确反映 DB/配置状态
- [x] `pytest` 通过且覆盖率门禁生效（`--cov-fail-under=80`）
- [x] 结构化日志默认开启，且有“脱敏不泄露 token/api_key”的单测

---

## 4. M2：数据面（/api/*）核心链路
### 4.1 Client 鉴权与治理
- [x] Client token 校验（DB 存 hash；token 只返回一次）
- [x] Client 并发限制（内存 semaphore，按 `client_id`）
- [x] Client 限流（MVP 内存令牌桶/滑窗；生产可迁移 Redis）
- [x] Client 每日配额（可选字段；惰性重置）

### 4.2 Key Pool（选择/并发/冷却/配额）
- [x] Round-robin + 配额感知选择（跳过 disabled/cooling/quota_exceeded/failed）
- [x] Key 并发 lease（MVP 内存 semaphore；状态权威说明见 `docs/agent.md` 第 15.1）
- [x] 429 冷却：尊重 `Retry-After`，否则默认 cooldown 秒数
- [x] Key 配额（daily_quota/daily_usage）更新与惰性重置
- [x] 失败退避：网络超时/5xx 达阈值转 failed，并可自动恢复窗口

### 4.3 转发器（Forwarder）
- [x] 端点映射实现（与 `docs/MVP/Firecrawl-API-Manager-API-Contract.md` 的 URL 示例一致）
- [x] 覆盖/丢弃不可信 header（`Authorization`/`Host`/`X-Forwarded-*` 等按策略）
- [x] 超时、重试、切 key 策略（只重试可重试错误；对 4xx 不重试）
- [x] 为每次入站请求产出 1 条 request_log（记录 retry_count 等）

### 4.4 幂等（建议优先做）
- [x] `X-Idempotency-Key` 支持（至少覆盖 `POST /api/crawl`、`POST /api/agent`）
- [x] request_hash 冲突检测（同 key 不同 body → 409）
- [x] TTL（默认 24h）与清理策略

### 4.5 数据面端点实现（按契约）
- [x] `POST /api/scrape`
- [x] `POST /api/crawl`
- [x] `GET /api/crawl/{id}`
- [x] `POST /api/search`
- [x] `POST /api/agent`

M2 DoD（完成标志）：
- [x] 每个 `/api/*` 端点都有集成测试（mock Firecrawl：200/429/5xx/timeout）
- [x] 429 冷却、切 key 重试、配额（success 口径）在回归测试中可重复验证
- [x] request_logs：每个入站请求恰好 1 条（含 retry_count 等），并保证脱敏

---

## 5. M3：控制面（/admin/*）闭环
### 5.1 Admin 鉴权与审计
- [x] Admin token 校验（独立于 client token）
- [x] 审计日志：所有管理操作写入 `audit_logs`（actor/ip/ua/action/resource）

### 5.2 Key 管理
- [x] `GET /admin/keys`
- [x] `POST /admin/keys`（加密落库 + 去重 hash + last4）
- [x] `PUT /admin/keys/{id}`（更新配额/并发/套餐/启用禁用）
- [x] `DELETE /admin/keys/{id}`
- [x] `POST /admin/keys/{id}/test`（测试成功/429/401/403 的状态迁移符合状态机）
- [x] `POST /admin/keys/reset-quota`

### 5.3 Client 管理
- [x] `GET /admin/clients`
- [x] `POST /admin/clients`（生成 token，仅返回一次）
- [x] `PUT /admin/clients/{id}`
- [x] `DELETE /admin/clients/{id}`（建议软禁用）
- [x] `POST /admin/clients/{id}/rotate`（仅返回一次）

### 5.4 统计与查询
- [x] `GET /admin/stats`
- [x] `GET /admin/stats/quota`（summary + keys 明细；口径与文档一致）
- [x] `GET /admin/logs`（过滤 + 游标分页）
- [x] `GET /admin/audit-logs`（过滤 + 游标分页）

M3 DoD（完成标志）：
- [x] 所有 `/admin/*` 端点按契约实现并有测试（含鉴权失败、分页/过滤边界）
- [x] 审计日志覆盖所有管理操作（create/update/delete/rotate/reset/test）

---

## 6. M4：可观测、清理策略与测试体系
### 6.1 可观测
- [x] 结构化日志字段规范（最少包含 request_id/client_id/endpoint/status_code/latency_ms）
- [x] 日志脱敏清单落地（见 `docs/agent.md` 第 17.3），并加单测验证“不会输出明文 token/api_key”
- [x] 指标（/metrics）：请求数、耗时、key 冷却次数、配额剩余等（见 `docs/agent.md` 第 8 章）

### 6.2 TTL/保留策略
- [x] request_logs 清理（保留 N 天）
- [x] audit_logs 清理（保留更久或永久）
- [x] idempotency_records 清理（TTL 24h）
- [x] 清理任务的可运行方式：定时任务（容器内）或外部 cron（二选一并文档化）

### 6.3 最小测试矩阵（必须全部自动化）
- [x] mock Firecrawl 集成测试（可控返回 200/429/401/403/5xx/timeout）
- [x] 并发回归（client/key 并发限制）
- [x] 429 冷却回归（Retry-After/默认冷却）
- [x] 配额口径回归（success + 惰性重置 + 时区）
- [x] 幂等回归（重复请求、冲突 body、TTL）
- [x] 失败模式回归（无 key / 全冷却 / 全配额用尽 / DB 不可用 / 上游超时）
- [x] 覆盖率门禁：`pytest --cov=app --cov-fail-under=80`

M4 DoD（完成标志）：
- [x] `/metrics`（或等价）可用；关键指标齐全
- [x] TTL/清理策略可执行且有测试/演练说明（至少在测试环境验证清理效果）
- [x] 覆盖率持续 ≥ 80%，并在 CI/本地脚本中可一键验证

---

## 7. M5：Docker 部署与生产形态
- [x] Dockerfile（生产镜像，非 root，合理的层缓存）
- [x] docker-compose（dev：SQLite；prod：Postgres + 可选 Redis）
- [x] secrets/环境变量示例（`FCAM_MASTER_KEY`、`FCAM_ADMIN_TOKEN`）
- [x] 生产建议：数据面与控制面端口隔离（或仅内网暴露 /admin）
- [x] 扩展一致性方案落地：
  - [x] Redis：分布式限流/并发/冷却（多实例一致）
  - [ ] 或 Postgres：行锁/事务实现 lease（写清楚权衡）

M5 DoD（完成标志）：
- [x] 提供 `docker-compose` 生产示例（Postgres + 可选 Redis），并能跑通核心回归测试
- [x] 文档写清“单实例（SQLite）与多实例（PG/Redis）”的边界与建议配置

---

## 8. M6（可选）：内置 WebUI（/ui2）
- [x] 仅在 `server.enable_control_plane=true` 时挂载 `/ui2`（静态页面）
- [x] WebUI 只做“最小可用”的 Key/Client 管理（复用现有 `/admin/*`），不新增后端业务语义
- [x] UI 形态：Vue（构建产物托管到 `app/ui2/`）；服务端挂载 `GET /ui2/`；`/ui/` 仅保留为 307 跳转
- [x] Admin Token 支持同标签页/本机持久化（可配置过期）并提供一键清空；默认同标签页保存以减少重复输入
- [x] 测试：控制面开启时 `/ui2/` 返回 HTML；控制面关闭时 `/ui2/` 返回 404；`/ui/` 跳转到 `/ui2/`
- [x] 文档：`README.md` 增加 `/ui2/` 入口说明；`docs/WORKLOG.md` 记录选择与验证结果

---

## 9. P0：ClawCloud 部署稳定性（SQLite CrashLoop）与 SQLite→Postgres 后端直迁

> 参考文档：  
> - PRD：`docs/PRD/2026-02-20-clawcloud-sqlite-crashloop-and-postgres-migration.md`  
> - FD：`docs/FD/2026-02-20-clawcloud-postgres-migration-fd.md`  
> - TDD：`docs/TDD/2026-02-20-clawcloud-postgres-migration-tdd.md`

### 9.1 启动期 DB 一致性（避免迁移期/运行期错配）
- [ ] `scripts/entrypoint.sh`：为 `FCAM_DATABASE_URL` 与 `FCAM_DATABASE__URL` 做双向 alias（只配一个 DSN 也能一致）
- [ ] `scripts/entrypoint.sh`：识别 DB backend（sqlite/postgres），Postgres 模式跳过 `/app/data` 可写性检查
- [ ] `scripts/entrypoint.sh`：为 `alembic upgrade head` 增加“等待 DB 就绪”的重试（`FCAM_DB_MIGRATE_RETRIES`、`FCAM_DB_MIGRATE_SLEEP_SECONDS`）
- [ ] `scripts/entrypoint.sh`：补充启动日志（`db.backend/db.source`），DSN 必须脱敏
- [ ] （建议）`app/config.py`：支持 `FCAM_DATABASE_URL` 映射到 `database.url`（防止绕过 entrypoint 直接跑 uvicorn 时错配）
- [ ] 文档：更新 `docs/deploy-clawcloud.md`、`docs/docker.md`，把“双写 DSN”从“必须”降级为“兼容写法”（推荐写法 + 自动 alias 说明）

### 9.2 后端直迁工具（SQLite → Postgres）（最高优先级）
- [ ] 新增迁移命令（推荐）：`python -m app.tools.migrate_sqlite_to_postgres --sqlite-path ... --postgres-url ...`
- [ ] 支持安全开关：`--dry-run`、`--truncate`、`--include/--exclude`、`--batch-size`、`--verify`
- [ ] 前置检查：SQLite 可读、Postgres 可连、目标 schema 已 `alembic upgrade head`、目标表为空（除非 `--truncate`）
- [ ] 迁移顺序与依赖：`clients` → `api_keys` → `idempotency_records` → `request_logs` → `audit_logs`
- [ ] 保留源 `id`；迁移后对 Postgres 序列执行 `setval(...)` 修正（避免后续插入冲突）
- [ ] 迁移后校验：行数对比 + 抽样字段校验（密文字段只做长度/非空校验，不解密）
- [ ] 输出 summary（每表源行数/迁移行数/耗时）与失败时的可行动建议

### 9.3 测试与验收
- [ ] 单测：env alias 行为（只配 `FCAM_DATABASE_URL` / 只配 `FCAM_DATABASE__URL` 的一致性）
- [ ] 集成测试：临时 Postgres + 临时 SQLite → 运行迁移工具 → 校验行数、外键一致、序列修正后可继续插入
- [ ] ClawCloud 验收：Pod 连续运行 ≥ 30 分钟、重启次数 0、`/healthz`=200、`/readyz`=200、最小写入→重启→读回闭环
