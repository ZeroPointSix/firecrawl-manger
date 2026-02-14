# FCAM 实施日志（Repository Worklog）

> 目的：把“我做了什么/为什么这么做/验证结果/下一步”作为可追溯记录，避免语义与实现漂移。  
> 约束：不记录任何明文 token/api_key/Authorization；如需示例一律用占位符。

## 2026-02-10（UTC）

### M0 → M1：工程骨架落地

**完成内容**
- 初始化目录结构：`app/`、`tests/`、`migrations/`、`scripts/`
- FastAPI 骨架：`app/main.py` + 探活 `GET /healthz`、就绪 `GET /readyz`
- 配置系统（单一入口）：默认值 → `config.yaml`（`FCAM_CONFIG`）→ env 覆盖（`FCAM_*__*`）
- 机密注入：按配置读取 `FCAM_ADMIN_TOKEN`、`FCAM_MASTER_KEY`（仅 env/secret）
- DB：SQLAlchemy 模型（对齐 PRD）+ Alembic 初始迁移（`0001_init`）
- 网关基础约束：路径白名单、Body 大小限制、Content-Type 校验
- 可观测：结构化 JSON 日志（request_id 注入）+ 脱敏（Bearer/token/api_key 等）
- 测试：pytest + pytest-cov 门禁脚本（`--cov=app --cov-fail-under=80`），并补齐脱敏/配置/中间件/readyz 的单测
- Docker(dev)：`Dockerfile` + `docker-compose.yml` + `scripts/entrypoint.sh`

**关键选择（KISS/DRY/SOLID）**
- 先按 `TD.md` 的 M1 做“可运行骨架”，把可测/可观测/可迁移的边界先搭起来，再进入 M2/M3 的业务链路。
- 日志脱敏采用“字段名脱敏 + 文本模式脱敏”两层，避免遗漏（但不写入任何明文机密）。

**当前阻塞**
- 当前执行环境缺少 `python/pytest/docker` 命令，导致 M1 DoD（`pytest` 与 `docker compose up`）无法在此环境直接实跑验证。
- 后续将采用“仓库内自举（portable python）”的方式跑测试/覆盖率，避免全局安装污染环境（如仍需 Docker 将另行说明）。

**已采取措施（消除 Python/pytest 阻塞）**
- 新增 `scripts/bootstrap-python.ps1`：下载 NuGet portable python（不做全局安装）→ 创建 `.venv` → 安装依赖 → 跑 `pytest --cov=app --cov-fail-under=80`

**本次验证结果**
- `powershell -NoProfile -ExecutionPolicy Bypass -File "scripts/bootstrap-python.ps1"`：通过（pytest 全绿；覆盖率门禁通过）
- `docker compose up --build`：当前环境缺少 docker CLI/daemon，暂无法验证（后续如需满足 M1 DoD，将按“安装 Docker 或在具备 Docker 的环境中验证”处理）

### M2：数据面 request_logs 落库 + /api 集成测试补齐

**完成内容**
- request_logs：在 `RequestIdMiddleware` 中为每个 `/api/*` 请求落 1 条 `request_logs`（成功/拒绝/上游透传/超时均覆盖）
- request_logs 字段：新增 `retry_count`（对应契约与 `agent.md` 13.4/8.1 建议字段）
- /api 集成测试：为每个数据面端点补齐 200/429/5xx/timeout 的 mock Firecrawl 用例；并增加 request_logs “恰好 1 条”与 `retry_count` 断言

**关键选择（KISS/DRY/SOLID）**
- 选择在中间件统一落库（而非每个路由手写落库），保证“每个入站请求恰好 1 条”且避免重复代码。
- 网关错误码通过 `request.state.error_code` 传递并落到 `RequestLog.error_message`（结构化可过滤），不额外引入 `error_code` 列（YAGNI，后续如确有需要再扩展）。
- request_logs 落库失败不影响请求返回（仅记录结构化错误日志），避免将“可观测性写入失败”放大为用户面故障。

**本次验证结果**
- `pytest "tests/test_api_data_plane.py"`：通过（覆盖 `/api/*` 转发映射 + 200/429/5xx/timeout + request_logs 断言）
- `pytest "tests/test_migrations.py"`：通过（Alembic head 可升级；包含新增列迁移）
- `pytest --cov=app --cov-fail-under=80`：通过（60 passed；Total coverage 89.90%）

### M3：控制面（/admin/*）闭环

**完成内容**
- 控制面路由：新增 `app/api/control_plane.py` 并挂载到 `app/main.py`
- Admin 鉴权：复用 `require_admin`（Admin Token 独立于 Client Token）
- 审计日志：对 key/client 的 create/update/delete/rotate/reset/test 写入 `audit_logs`（含 ip/ua）
- Key 管理：`/admin/keys*` 全部端点（加密落库、去重 hash、last4、test、reset-quota）
- Client 管理：`/admin/clients*` 全部端点（token 仅返回一次；rotate 仅返回一次）
- 统计与查询：`/admin/stats`、`/admin/stats/quota`、`/admin/logs`、`/admin/audit-logs`（含 cursor 分页/过滤）
- 中间件修正：允许无 body 的 `POST/DELETE`（例如 rotate/delete/reset）不强制 `Content-Type: application/json`

**关键选择（KISS/DRY/SOLID）**
- `/admin/keys/{id}` 的删除实现为“软禁用”（`is_active=false,status=disabled`），避免历史 `request_logs` 丢失 key 关联；契约仍保持 204（不返回明文 key）。
- 除 `key.test` 复用 Forwarder 内部提交外，其余管理操作与 `audit_logs` 同事务提交，保证管理操作与审计一致（失败则整体回滚）。
- `/admin/logs` 的 `error_message` 记录网关错误码（`error.code`），避免引入额外列（YAGNI），同时便于过滤查询。

**本次验证结果**
- `pytest "tests/test_admin_control_plane.py"`：通过（覆盖 /admin 全链路 + 审计 + 分页/过滤边界）
- `pytest`：通过（全量回归）
- `pytest --cov=app --cov-fail-under=80`：通过（69 passed；Total coverage 84.64%）

### M2.4：幂等（X-Idempotency-Key）

**完成内容**
- 幂等核心：新增 `app/core/idempotency.py`，使用 `idempotency_records` 落库（`client_id + idempotency_key` 唯一）
- 幂等接入：对 `POST /api/crawl`、`POST /api/agent` 支持 `X-Idempotency-Key`；同 key 同 body 自动 replay；同 key 不同 body 返回 409
- TTL：默认 24h（惰性清理过期记录）；超时/不可用导致“未知是否创建任务”的场景保持 `in_progress`，避免重复创建风险
- 配置：新增 `idempotency.*`（可选强制 `require_on=["crawl","agent"]`）并更新 `config.yaml`
- 模型对齐：补齐 `IdempotencyRecord` 的唯一约束（与 Alembic 迁移一致）

**关键选择（KISS/DRY/SOLID）**
- 仅在 `crawl/agent` 接入幂等（最小必要面），其余端点不引入额外状态写入。
- “拿不到上游响应”的情况不做自动重试与完成态落库，优先保证不重复创建任务；调用方可等待 TTL 或使用新的幂等键显式重试。
- replay 存储采用“headers + body(base64)”打包在 `response_body`，避免新增表结构字段（YAGNI）。

**本次验证结果**
- `pytest "tests/test_api_data_plane.py"`：通过（新增幂等 replay/冲突/in_progress/强制缺失用例）

### M4：可观测（/metrics）+ 保留策略清理

**完成内容**
- 指标：新增 `app/observability/metrics.py`，按配置暴露 `GET /metrics`（Prometheus）
- 采集：在 `RequestIdMiddleware` 记录 requests_total/latency；Forwarder 记录 key_selected/key_cooldown/quota_remaining
- 保留策略：新增 `app/db/cleanup.py` + `scripts/cleanup.py`，支持 request_logs/audit_logs/idempotency_records 清理
- 配置：新增 `observability.*`（metrics + retention）并更新 `config.yaml`
- 测试：新增 metrics/cleanup 用例，并补齐幂等 TTL、all cooling、failure mode、client quota 惰性重置等回归
- 文档：更新 `agent.md`、`Firecrawl-API-Manager-API-Contract.md`、`README.md`、`TD.md`

**关键选择（KISS/DRY/SOLID）**
- 选择 Prometheus 官方 `prometheus-client`，避免手写 exposition 格式与线程安全问题。
- `/metrics` 默认关闭，减少默认暴露面；由 `observability.metrics_enabled` 显式开启。
- 清理任务选择外部 cron 调用 `scripts/cleanup.py`（避免在 API 进程内引入常驻定时器/多 worker 一致性问题）。

**本次验证结果**
- `pytest`：通过（全量回归）
- `pytest --cov=app --cov-fail-under=80`：通过（覆盖率门禁通过）

### M5：生产形态（Postgres/Redis）+ 端口隔离 + 分布式状态

**完成内容**
- 生产依赖：新增 `redis`、`psycopg[binary]`（支持 Redis 状态后端与 Postgres 连接）
- 分布式状态（Redis，按 `state.mode=redis` 开启）：
  - 并发：新增 `RedisConcurrencyManager`（client/key 共享并发 lease）
  - 限流：新增 `RedisTokenBucketRateLimiter`（client rpm）
  - 冷却：新增 `RedisCooldownStore`，429 时写入 TTL，key_pool 在 cooling 状态时读取 TTL
- 端口隔离：新增 `server.enable_data_plane/enable_control_plane`，允许拆分 `/api` 与 `/admin` 到不同实例/端口；`/readyz` 校验也按启用面板做条件检查
- 容器化（生产示例）：
  - `Dockerfile`：非 root 用户运行 + 仅安装生产依赖
  - `scripts/entrypoint.sh`：uvicorn host/port 读取 `FCAM_SERVER__HOST/PORT`
  - `docker-compose.prod.yml`：Postgres + Redis + 两实例示例（api/admin 端口隔离）
- 文档与示例：更新 `README.md`、`agent.md`、`.env.example`、`TD.md`

**关键选择（KISS/DRY/SOLID）**
- 选择 Redis 作为“并发/限流/冷却”的分布式权威（与 `agent.md` 15.2 推荐一致），避免 DB 行锁实现复杂度。
- 端口隔离通过“同一代码、不同部署开关”实现（不引入双进程/反向代理强依赖），保持部署简单可控。

**本次验证结果**
- `pytest`：通过（全量回归）
- `pytest --cov=app --cov-fail-under=80`：通过（覆盖率门禁通过）

## 2026-02-11（UTC）

### Runbook：本地启动服务（非 Docker）

**执行步骤（Windows / PowerShell）**
- 迁移：`& ".venv/Scripts/python.exe" -m alembic upgrade head`
- 启动：`& ".venv/Scripts/python.exe" -m uvicorn "app.main:app" --host "127.0.0.1" --port "8000"`

**关键选择（KISS/安全默认）**
- 本地启动默认绑定 `127.0.0.1`，避免意外对外网暴露（如需对外访问再显式改为 `0.0.0.0`）。
- `FCAM_ADMIN_TOKEN` / `FCAM_MASTER_KEY` 仅通过环境变量注入，不写入任何文档/日志明文。

**验证结果**
- `GET http://127.0.0.1:8000/healthz` → 200 `{"ok": true}`
- `GET http://127.0.0.1:8000/readyz` → 200 `{"ok": true}`

### M6（可选）：最小内置 WebUI（/ui）

**完成内容**
- 新增内置静态页面：`GET /ui/`（仅当 `server.enable_control_plane=true` 时挂载）
- WebUI 复用既有 `/admin/*` 控制面能力：Key/Client 的创建/更新/启用禁用/软禁用、Key test、Client rotate、`/admin/stats` 查看
- Admin Token 由用户在页面输入，仅保存在浏览器内存中（刷新即丢失）

**关键选择（KISS/安全默认）**
- 不新增任何“UI 专用后端 API”，只做静态页面 + fetch 调用现有 `/admin/*`（避免重复语义与接口漂移）。
- UI 页面本身不做强制鉴权（否则浏览器难以携带 header 加载 HTML），但只在控制面启用时挂载，并建议仅内网暴露控制面端口。
- Token 默认不落本地存储（localStorage/sessionStorage），降低误操作导致的长期泄露面（如需持久化再显式扩展）。

**本次验证结果**
- `pytest "tests/test_ui.py"`：通过（控制面开关覆盖：200 HTML + 静态资源 / 404 NOT_FOUND）
- `pytest --cov=app --cov-fail-under=80`：通过（92 passed；Total coverage 85.32%）

### M6.1：WebUI 视觉重构（Dashboard 风格）

**完成内容**
- UI 重构为本地静态资源：`app/ui/index.html` + `app/ui/app.css` + `app/ui/app.js`
- 交互形态：侧边栏多标签（dashboard/keys/clients/logs/audit/help）+ 主区分屏（列表/详情）
- 运维可视化补齐：在 UI 内直接查询 `request_logs`（`/admin/logs`）与 `audit_logs`（`/admin/audit-logs`）
- 连接校验复用 `/admin/stats`：验证成功后同时回填 Dashboard 输出，避免“连接成功但概览仍未加载”的困惑
- 修正视图切换隐藏逻辑：仅切换 `.view[data-view]` 区块，不影响侧边栏导航常驻
- 旧版 UI 快照保留：`app/ui.v1.html`（不再由 `/ui/` 提供，仅用于对照/回滚）

**关键选择（KISS/安全默认）**
- 不依赖 CDN 与构建工具（NPM/bundle），避免内网/离线环境下 UI 失效；同时减少供应链暴露面。
- 仍坚持“不新增 UI 专用后端 API”，所有操作都复用既有 `/admin/*`（保持契约单一事实来源）。

**本次验证结果**
- `pytest "tests/test_ui.py"`：通过（含 `/ui/app.css`、`/ui/app.js` 可访问断言）

### M6.2：WebUI 导航布局调整（顶部横向 Tabs）

**完成内容**
- 导航从“侧边栏竖向列表”改为“顶部横向 Tabs”（概览 / API Keys / Clients / 请求日志 / 审计日志 / 帮助）。
- 页面布局改为“固定 Header + 主内容区滚动”，减少整页长滚动，保持各视图通过 Tabs 完整隔离切换。
- 修复 Tabs 切换不生效：补齐 CSS `[hidden]{display:none !important}`，避免 `.view{display:block}` 覆盖 `hidden` 属性导致所有视图同时显示。
- UI 测试补强：断言 HTML 中包含 Tabs 与各 `data-view` 按钮，避免导航结构回归。

**关键选择（KISS/DRY）**
- 保持原有 `data-view` + `.nav-item` 的视图切换机制不变，仅调整 DOM 位置与 CSS（降低 JS 回归风险）。
- 使用标准 `hidden` 属性做视图隔离，并在 CSS 中显式约束 `[hidden]`（避免不同浏览器/样式覆盖导致的渲染差异）。
- 不引入任何前端构建链路/外部依赖（继续离线可用，减少供应链暴露面）。

**本次验证结果**
- `pytest --cov=app --cov-fail-under=80`：通过（93 passed；Total coverage 85.32%）

### M6.3：WebUI Token 持久化 + 前端日志 + 可配置 Key Test

**完成内容**
- Admin Token 支持持久化保存：
  - 同标签页（`sessionStorage`，刷新不丢失）
  - 本机持久（`localStorage`）
  - 支持配置过期时间（小时）与一键清空（同时清理两类存储）。
- 增强可观测性：在「帮助」页增加“前端日志”面板，记录 UI 内部请求/错误/状态变化（不记录任何 token）。
- 增强验证能力：Key Test 支持配置 `test_url`，默认 `https://www.google.com`（仍可按环境改为 `https://example.com` 等）。
- 增加端到端验证入口：Dashboard 新增「数据面自检（/api/scrape）」表单，可粘贴 Client Token + `test_url` 直接验证转发链路（不持久化 Client Token）。

**关键选择（KISS/安全默认）**
- token 默认保存方式选择“同标签页”，避免“刷新即丢失”的操作摩擦，同时比 `localStorage` 更少长期泄露面；如确需跨重启持久化，再显式选择 `localStorage`。
- 所有日志与错误展示均避免输出 token（仅记录 method/path/status/耗时/错误码）。
- 不新增任何 UI 专用后端接口：仍复用 `/admin/*` 与 `/admin/keys/{id}/test`（DRY）。

**本次验证结果**
- `pytest --cov=app --cov-fail-under=80`：通过（93 passed；Total coverage 85.32%）

**排查记录（日志核对）**
- 数据库存储：`data/api_manager.db`（`config.yaml: database.path` 默认值）。
- `request_logs.count=0`：说明该实例尚未收到任何 `/api/*` 数据面请求（因此“请求日志”页为空属预期）。
- `audit_logs` 最新包含多次 `key.test`（例如 `2026-02-11 15:17:59Z`，`resource_id=1`）：说明已触发控制面 Key Test，服务端会向上游 `POST {firecrawl.base_url}/scrape` 发起探测请求。

### Runbook：端口冲突时启动（示例：18000）

**问题现象**
- 启动 uvicorn 绑定 `127.0.0.1:8000` 或 `:8001` 失败：`[Errno 10048] ... only one usage of each socket address`

**处理方式（KISS）**
- 选择一个空闲端口（例如 18000），并用 env override 同步 `server.port`（仅用于日志字段对齐，不影响实际绑定端口）：
  - `FCAM_SERVER__PORT=18000`
  - `uvicorn ... --port 18000`

**验证结果**
- `GET /healthz` → 200
- `GET /readyz` → 200
- `GET /ui/` → 200（HTML）
- `GET /docs` → 200（HTML）

### API 使用指南（面向调用方/运维）

**完成内容**
- 新增 `API-Usage.md`：涵盖启动/配置、Key 池管理、Client Token 发放、`/api/*` 调用（scrape/crawl/agent）与测试路径。

**关键选择（KISS/DRY）**
- “字段/错误体/分页”等契约细节仍以 `Firecrawl-API-Manager-API-Contract.md` 为单一事实来源；`API-Usage.md` 只聚焦上手与调用姿势，避免文档漂移。

**本次验证结果**
- `pytest -q tests/test_api_data_plane.py::test_api_endpoints_forward_to_expected_upstream ...`：通过（MockTransport 模拟上游，覆盖 scrape/crawl/agent 转发路径与幂等行为）
- `pytest --cov=app --cov-fail-under=80`：通过（93 passed；Total coverage 85.32%）

## 2026-02-12（UTC）

### WebUI 重构（/ui2：Vue3 + Vite + NaiveUI）— Keys 批量/管理补齐

**完成内容**
- Clients & Keys：Key 表格补齐多选、批量删除（purge）、单条测试/启用禁用/轮换/删除（purge），并保持所有操作均复用既有 `/admin/*`。
- Key 导入：弹窗展示导入汇总（created/updated/skipped/failed）与失败明细（仅 line_no + message，不回显原始导入文本）。
- Keys 页安全提示：接入 `GET /admin/encryption-status`，当存在不可解密 Key 时给出强提示与建议。
- 可用性修正：连接/断开 Admin Token 后各页面自动触发数据加载/清空（避免“连接成功但页面仍空白/需手动刷新”的困惑）。

**关键选择（KISS/安全默认/DRY）**
- 批量删除不新增后端 batch endpoint：前端顺序调用 `DELETE /admin/keys/{id}/purge`；部分失败时仅汇总提示并保留失败列表在控制台（不输出明文）。
- UI “删除”语义采用 purge（物理删除），“启用/禁用/轮换”统一走 `PUT /admin/keys/{id}`，避免与 `DELETE /admin/keys/{id}`（软禁用）语义混淆。
- 关闭包含敏感输入的弹窗时清空文本域/结果（减少明文在页面停留时间；不持久化）。

**配置说明（避免“没有配置好”）**
- `/ui2` 仅在 `app/ui2/` 构建产物存在时挂载（`app/main.py`）。该目录在 `.gitignore` 中，因此需要本地构建：
  - `cd webui && npm install && npm run build`
- 运行服务后访问：`GET /ui2/`（需 `server.enable_control_plane=true`）。

**本次验证结果**
- `cd webui && npm run type-check`：通过
- `cd webui && npm run build`：通过（产物输出到 `app/ui2/`）
- `pytest -q`：通过

### M6.4：请求日志分页（DB cursor）+ level/q 过滤 + UI2 分页视图

**完成内容**
- `/admin/logs`：增加 `level=info|warn|error` 快速筛选与 `q` 模糊搜索（`request_id/endpoint/error_message`），并在响应 items 中返回 `level` 字段。
- `/ui2/#/logs`：前端由“无限追加”改为“真分页”（每页 20/50/100），并支持 level 过滤与关键词搜索；分页基于服务端 cursor（不在前端堆积超长列表）。
- 契约同步：更新 `Firecrawl-API-Manager-API-Contract.md` 中 `/admin/logs` 的 query/response 字段。

**关键选择（KISS/DRY）**
- 日志 level 不落库（避免新增列与迁移），由响应按 `status_code/success` 推导（保持数据模型稳定）。
- `q` 搜索仅覆盖最常用的三个字段（request_id/endpoint/error_message），避免做“全字段全文检索”的过度设计（YAGNI）。

**本次验证结果**
- `cd webui && npm run type-check`：通过
- `cd webui && npm run build`：通过
- `pytest "tests/test_admin_control_plane.py::test_admin_logs_query_pagination_and_filters"`：通过

### UI2 视觉对齐 gpt-load（配色/组件/布局）

**完成内容**
- `/ui2` 主题与全局样式对齐 `example/gpt-load/web`：
  - 新增 `webui/src/assets/variables.css`、`webui/src/assets/style.css`（复制并作为 UI2 单一风格来源）
  - `webui/src/App.vue`：Naive UI `themeOverrides` 对齐 gpt-load；整体布局调整为“玻璃态顶栏 + 居中导航 + 内容区”
- 适配：`webui/src/components/RequestTrendChart.vue`、`webui/src/views/LogsView.vue`、`webui/src/views/AuditView.vue` 改用 gpt-load 的 CSS 变量（移除 `--fcam-*` 依赖）。

**关键选择（KISS/DRY）**
- UI 变量命名与配色以 `example/gpt-load/web` 为准，避免多套主题并存导致维护与漂移成本上升。

**本次验证结果**
- `cd webui && npm run type-check`：通过
- `pytest -q`：通过

### 安全一致性：/v1 兼容层纳入 request_limits 白名单

**完成内容**
- `RequestLimitsMiddleware` 的路径白名单从仅 `/api/*` 扩展到同时覆盖 `/v1/*`，避免兼容层绕过 `allowed_paths` 约束。

**本次验证结果**
- `pytest -q tests/test_middleware.py`：通过

### 可观测一致性：/v1 兼容层请求也写入 request_logs

**完成内容**
- `RequestIdMiddleware` 的 endpoint 推导同时覆盖 `/api/*` 与 `/v1/*`，确保兼容层的拒绝请求（例如 401/503）也会写入 `request_logs`，便于用 `/admin/logs` 排障与回放。
- E2E：新增 `/v1/scrape` 的“无 token（401）/无 key（503）”用例，验证日志 `endpoint/level/error_message` 一致。
- E2E（可选真实上游）：上游 smoke 用例改为走 `/v1/scrape`（更贴近 Firecrawl SDK 迁移路径）。

**本次验证结果**
- `pytest -q`：通过

### T1：真实 API E2E 测试（不使用 mock 数据）

**完成内容**
- 新增 `tests/test_e2e_real_api.py`：测试用例通过 subprocess 启动 uvicorn（真实 HTTP），跑 Alembic 迁移创建空库；通过 `/admin/*` 与数据面（`/api/*`、`/v1/*`）真实调用驱动数据生成与日志落库，再验证 `/admin/logs` 的分页/level/q 行为。
- 为避免误触外部上游请求，E2E 默认将 `firecrawl.base_url` 指向本机不可用地址；如需验证真实 Firecrawl 上游链路，建议在独立环境显式覆盖配置并提供真实 key（不写入仓库）。

**运行方式**
- `FCAM_E2E=1 && pytest "tests/test_e2e_real_api.py"`（需在项目 `.venv` 中执行）

## 2026-02-13（UTC）

### Docs：新增接入手册入口 + Docker 部署指南

**完成内容**
- 新增 `docs/handbook.md`：接入方/运维快速手册（接口一览、最短接入路径、排障姿势），并在 `README.md` 增加入口。
- 新增 `docs/docker.md`：解释 MVP（SQLite）与生产（Postgres + Redis）部署差异、持久化位置与最小启动命令；在 `docs/handbook.md`/`README.md` 增加链接入口。
- `docker-compose.yml`：dev 默认只绑定 `127.0.0.1:8000`（可通过 `FCAM_BIND_ADDR=0.0.0.0` 显式对外暴露）；并将敏感 env 改为环境变量注入（保留 dev 默认值）。

**关键选择（KISS/DRY/安全默认）**
- 文档分层：字段/分页/错误体仍以 `Firecrawl-API-Manager-API-Contract.md` 为单一事实来源；handbook 只做“索引 + 最小可用链路”，避免重复维护漂移（DRY）。
- Docker(dev) 默认端口绑定改为 `127.0.0.1`，降低“把控制面（`/admin/*`）与数据面一起暴露到公网”的误配置风险（安全默认）。

**本次验证结果**
- `cd webui && npm run type-check`：通过
- `cd webui && npm run build`：通过（产物更新到 `app/ui2/`；chunk size 警告不影响运行）
- `pytest -q`：通过（含 7 skipped；仅告警无失败）

### UI2：审计日志改为“真分页”

**完成内容**
- `/ui2/#/audit`：由“加载更多（append）”改为“真分页”（上一页/下一页 + 20/50/100 每页），分页基于服务端 cursor（不在前端堆积超长列表）。

**关键选择（KISS/一致性）**
- 复用 `/ui2/#/logs` 已验证的分页状态机（pages + cursors + hasMoreByPage），保持交互一致并降低维护成本。

**本次验证结果**
- `cd webui && npm run type-check`：通过

### Repo hygiene：忽略 Windows `nul` 伪文件 + UI2 快照

**完成内容**
- `.gitignore` 增加 `nul`：避免该保留名伪文件导致工具链异常（例如 ripgrep 报错）。
- 新增 `ui2-dashboard*.png`：用于视觉对齐与回归对比，不参与运行时逻辑。

**关联提交**
- `41c41a6 docs: add FCAM handbook`
- `d27b769 chore: ignore nul and add ui2 dashboard snapshots`
- `bd82a74 feat(webui): paginate audit logs`
- `e1bcb95 docs: add docker deployment guide`
