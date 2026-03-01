# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

Firecrawl API Manager (FCAM) 是一个轻量级 HTTP 网关，用于集中管理多个 Firecrawl API Key，提供统一的转发入口和控制面板。

技术栈：
- 后端：Python 3.11+, FastAPI, Uvicorn, SQLAlchemy, Alembic
- 前端：Vue 3, TypeScript, Vite, Naive UI
- 数据库：SQLite (开发), Postgres (生产)
- 状态管理：内存 (开发), Redis (生产，用于分布式限流/并发/冷却)
- 容器化：Docker, docker-compose

## 核心架构

### 双平面架构
- **数据面** (`/api/*`, `/v1/*`, `/v2/*`): 业务请求转发，包含 Client 鉴权、限流、配额、并发控制、Key 池轮询、幂等性保证、资源粘性绑定
- **控制面** (`/admin/*`): Key/Client 管理、批量操作、审计日志、统计查询、额度监控、WebUI 管理界面

### 额度监控机制
- **智能刷新**：根据额度使用率动态调整刷新频率（额度低 → 刷新频繁）
- **本地计算**：每次请求后本地估算额度消耗，减少对上游 API 的调用
- **定期同步**：后台任务定期调用 Firecrawl API 获取真实额度，校准本地估算
- **历史追踪**：记录额度快照，支持趋势分析和可视化
- **Group 聚合**：支持按 Client 分组展示总额度

### 关键模块

#### API 路由层 (`app/api/`)
- `control_plane.py`: 控制面路由 (58KB, 核心管理接口)
  - Key 管理 (CRUD, 批量测试, 导入)
  - Client 管理 (CRUD, 批量操作)
  - 请求日志查询
  - 审计日志查询
  - 统计数据查询
- `data_plane.py`: 数据面路由 (5KB, 转发入口)
- `firecrawl_compat.py`: Firecrawl v1 兼容层 (`/v1/*`)
- `firecrawl_v2_compat.py`: Firecrawl v2 兼容层 (`/v2/*`, 28KB)
- `health.py`: 健康检查 (`/healthz`, `/readyz`)
- `deps.py`: 依赖注入

#### 核心业务逻辑 (`app/core/`)
- `forwarder.py`: 核心转发逻辑 (34KB, 841 行)
  - Key 选择和轮询
  - 自动重试和故障转移
  - 429 冷却处理
  - 401/403 自动禁用
  - 配额消费
- `key_pool.py`: Key 池管理，负责 Key 轮询和冷却状态
- `concurrency.py`: 并发控制（内存/Redis 实现，租约模式）
- `rate_limit.py`: 令牌桶限流（内存/Redis 实现）
- `idempotency.py`: 幂等性处理（Idempotency-Key 支持）
- `cooldown.py`: 冷却管理（内存/Redis 实现）
- `security.py`: 加密解密（Fernet 对称加密）
- `resource_binding.py`: 资源粘性绑定（同一 Client 访问同一资源使用同一 Key）
- `batch_clients.py`: 批量客户端操作
- `key_import.py`: Key 导入
- **额度监控模块**（Credit Monitoring）：
  - `credit_fetcher.py`: 调用 Firecrawl API 获取真实额度
  - `credit_estimator.py`: 估算请求消耗的额度
  - `credit_aggregator.py`: 聚合多个 Key 的额度（Client 级别）
  - `credit_refresh.py`: 智能刷新策略（根据额度动态调整刷新频率）
  - `credit_refresh_scheduler.py`: 后台定时刷新调度器

#### 数据库层 (`app/db/`)
- `models.py`: SQLAlchemy 数据模型 (183 行)
  - ApiKey: API 密钥（加密存储，状态管理，额度缓存）
  - Client: 客户端（配额、限流、并发控制）
  - RequestLog: 请求日志
  - IdempotencyRecord: 幂等性记录
  - UpstreamResourceBinding: 上游资源绑定
  - AuditLog: 审计日志
  - CreditSnapshot: 额度快照（记录 Firecrawl 真实额度历史）
- `session.py`: 数据库会话管理
- `cleanup.py`: 数据清理

#### 其他核心模块
- `middleware.py`: 中间件栈
  - RequestIdMiddleware: 请求 ID 生成
  - FcamErrorMiddleware: 统一错误处理
  - RequestLimitsMiddleware: 请求体限制和路径白名单
- `config.py`: 配置管理 (227 行, YAML + 环境变量覆盖)
- `errors.py`: 错误处理和标准错误响应
- `main.py`: 应用入口 (192 行, FastAPI 应用创建)
- `observability/`: 可观测性
  - `logging.py`: 结构化日志（JSON 格式，敏感字段脱敏）
  - `metrics.py`: Prometheus 指标

### 前端结构 (`webui/`)
- `src/views/`: Vue 页面组件
  - `DashboardView.vue`: 仪表盘（统计概览）
  - `ClientsKeysView.vue`: Client 和 Key 管理
  - `LogsView.vue`: 请求日志查询
  - `AuditView.vue`: 审计日志查询
- `src/api/`: API 调用封装
  - `clients.ts`: Client API
  - `keys.ts`: Key API
  - `logs.ts`: 日志 API
  - `dashboard.ts`: 统计 API
  - `credits.ts`: 额度 API（Key/Client 额度查询、历史、刷新）
- `src/components/`: 可复用组件
  - `ConnectModal.vue`: AdmiToken 输入弹窗
  - `StatCard.vue`: 统计卡片
  - `RequestTrendChart.vue`: 请求趋势图
  - `CreditDisplay.vue`: 额度展示组件（进度条、百分比）
  - `CreditTrendChart.vue`: 额度趋势图（历史额度变化）
- `src/state/`: 全局状态管理（adminToken）
- `src/router/`: 路由配置（Hash 模式）
- 构建输出：`app/ui2/`（被 `.gitignore` 忽略，需手动构建）

### 数据库迁移 (`migrations/versions/`)
- `0001_init.py`: 初始化 schema
- `0002_add_retry_count_to_request_logs.py`: 添加重试计数
- `0003_add_account_fields_to_api_keys.py`: 添加账户字段
- `0004_add_client_id_to_api_keys.py`: 添加 Client 关联
- `0005_add_error_details_to_request_logs.py`: 添加错误详情
- `0006_add_upstream_resource_bindings.py`: 添加资源绑定表
- `0007_add_status_to_clients.py`: 添加 Client 状态字段
- `0008_add_credit_monitoring.py`: 添加额度监控表（credit_snapshots）和 ApiKey 缓存字段
- `0009_add_credit_monitoring_indexes.py`: 添加额度监控索引（优化查询性能）

## 常用命令

### 环境设置
```powershell
# Windows - 使用内置 Python 环境
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts/bootstrap-python.ps1"

# 或手动创建虚拟环境
python -m venv .venv
.venv\Scripts\activate  # Windows
# source .venv/bin/activate  # Linux/Mac
pip install -r requirements.txt -r requirements-dev.txt
```

### 环境变量（必需）
```powershell
$env:FCAM_ADMIN_TOKEN="dev_admin_token"
$env:FCAM_MASTER_KEY="dev_master_key_32_bytes_minimum____"
```

### 数据库迁移
```bash
# 应用所有迁移
alembic upgrade head

# 创建新迁移
alembic revision --autogenerate -m "描述"

# 回滚一个版本
alembic downgrade -1
```

### 启动服务
```powershell
# Windows
& ".venv/Scripts/python.exe" -m uvicorn "app.main:app" --host "0.0.0.0" --port 8000

# Linux/Mac
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 前端构建
```bash
cd webui
npm ci
npm run build  # 输出到 app/ui2/
npm run dev    # 开发模式（不要在生产环境使用）
```

### 测试
```bash
# 运行所有测试（覆盖率门禁 80%）
pytest --cov=app --cov-fail-under=80

# 运行单个测试文件
pytest tests/integration/test_forwarder.py -v

# 运行单个测试函数
pytest tests/integration/test_forwarder.py::test_forwarder_retries_on_429_and_switches_key -v

# E2E 测试（真实 HTTP 请求，不使用 TestClient）
$env:FCAM_E2E="1"
pytest -q tests/e2e/test_e2e_real_api.py

# E2E 测试（真实上游 Firecrawl API，有费用风险）
$env:FCAM_E2E="1"
$env:FCAM_E2E_ALLOW_UPSTREAM="1"
$env:FCAM_E2E_FIRECRAWL_API_KEY="fc-xxx"
pytest -q tests/e2e/test_e2e_real_api.py::test_e2e_firecrawl_compat_scrape_success_with_real_upstream
```

### Docker
```bash
# 开发环境（SQLite）
docker compose up --build

# 生产环境（Postgres + Redis + 端口隔离）
docker compose --profile prod up --build postgres redis fcam_api fcam_admin

# 公开部署（仅数据面）
docker compose --profile public up --build fcam_public
```

### 清理任务
```bash
# 清理过期日志（根据 config.yaml 中的保留策略）
python scripts/cleanup.py
```

## 开发规范

### 配置管理
- 主配置文件：`config.yaml`
- 环境变量覆盖：使用 `FCAM_` 前缀，例如 `FCAM_SERVER__PORT=8001`
- 敏感信息（Token, Key）必须通过环境变量传递，不能写入配置文件
- 额度监控配置：`credit_monitoring.*`（智能刷新策略、批量处理、数据保留等）

### 数据库操作
- 使用 SQLAlchemy ORM，不要直接写 SQL
- 所有 schema 变更必须通过 Alembic 迁移
- 迁移文件位于 `migrations/versions/`
- Key 加密存储使用 Fernet（对称加密），Master Key 从环境变量读取

### API 设计
- 数据面 (`/api/*`) 使用 Bearer Token 鉴权（Client Key）
- 控制面 (`/admin/*`) 使用 Bearer Token 鉴权（Admin Token）
- 错误响应遵循统一格式（见 `app/errors.py`）
- 所有 API 契约见 `docs/MVP/Firecrawl-API-Manager-API-Contract.md`

### 日志规范
- 使用结构化日志（JSON 格式）
- 敏感字段自动脱敏（见 `config.yaml` 中的 `logging.redact_fields`）
- 每个请求自动分配 `request_id`（通过 `RequestIdMiddleware`）

### 测试规范
- 单元测试覆盖率要求 80%
- 使用 pytest fixtures 管理测试数据库
- E2E 测试使用真实 HTTP 请求（不使用 TestClient）
- 涉及真实上游 API 的测试必须通过环境变量显式启用

### 前端开发
- 使用 TypeScript 严格模式
- API 调用统一封装在 `webui/src/api/` 目录
- Admin Token 存储在 localStorage（可选择"仅本次"使用 sessionStorage）
- 构建前必须通过类型检查：`npm run type-check`

## 关键文档

**必读文档**（按优先级）：
1. `docs/agent.md` - 技术方案与架构设计（单一事实来源）
2. `docs/MVP/Firecrawl-API-Manager-API-Contract.md` - API 接口契约
3. `docs/API-Usage.md` - API 使用指南
4. `docs/docker.md` - Docker 部署指南

**功能设计文档**：
- 额度监控功能已实现，详见代码和测试

**其他文档**：
- `docs/MVP/Firecrawl-API-Manager-PRD.md` - 产品需求文档
- `docs/handbook.md` - 接入方/运维快速手册
- `docs/WORKLOG.md` - 变更日志

## 常见问题

### `ADMIN_UNAUTHORIZED: Missing or invalid admin token`
- 确认环境变量 `FCAM_ADMIN_TOKEN` 已设置且与请求中的 `Authorization: Bearer <token>` 一致
- 如果使用 `/ui2/`，确认已输入正确的 Admin Token 并点击"保存"
- Swagger (`/docs`) 不会自动添加 Authorization 头，建议使用 `/ui2/` 或命令行工具

### `sqlite3.OperationalError: unable to open database file`
- Docker 容器以非 root 用户（uid 10001）运行
- 如果使用宿主机目录挂载，需要设置正确的权限：`sudo chown -R 10001:10001 ./data`
- 或改用 Docker 命名卷：`docker volume create fcam_data`

### WebUI 显示"UI2 静态文件尚未构建"
- 需要手动构建前端：`cd webui && npm ci && npm run build`
- 构建输出位于 `app/ui2/`，该目录被 `.gitignore` 忽略

### 测试覆盖率不足 80%
- 运行 `pytest --cov=app --cov-report=html` 生成详细报告
- 查看 `htmlcov/index.html` 找出未覆盖的代码
- 优先覆盖核心业务逻辑（forwarder, key_pool, middleware）

## 部署注意事项

### 生产环境建议
- 使用 Postgres 替代 SQLite（支持并发写入）
- 使用 Redis 实现分布式状态（多实例部署时必需）
- 数据面和控制面端口隔离（`server.enable_data_plane` / `server.enable_control_plane`）
- 启用 Prometheus 指标：`observability.metrics_enabled: true`
- 配置日志保留策略：`observability.retention.*`
- 定期运行清理任务：`python scripts/cleanup.py`

### 安全配置
- Admin Token 和 Master Key 必须使用强随机值（至少 32 字节）
- 生产环境禁用 Swagger：`server.enable_docs: false`
- 限制请求体大小：`security.request_limits.max_body_bytes`
- 仅允许白名单路径转发：`security.request_limits.allowed_paths`
- 考虑在反向代理层启用 mTLS 或 IP 白名单

### 水平扩展
- 确保 `state.mode: redis`（多实例共享状态）
- 数据库连接池配置（SQLAlchemy `pool_size`, `max_overflow`）
- 使用负载均衡器（Nginx, HAProxy）分发请求
- 健康检查端点：`GET /healthz`, `GET /readyz`
