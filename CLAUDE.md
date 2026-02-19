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
- **数据面** (`/api/*`): 业务请求转发，包含 Client 鉴权、限流、配额、并发控制、Key 池轮询
- **控制面** (`/admin/*`): Key/Client 管理、审计日志、统计查询

### 关键模块
- `app/core/forwarder.py`: 核心转发逻辑，处理 Key 选择、重试、429 冷却
- `app/core/key_pool.py`: Key 池管理，负责 Key 轮询和冷却状态
- `app/core/concurrency.py`: 并发控制（内存/Redis 实现）
- `app/core/rate_limit.py`: 令牌桶限流（内存/Redis 实现）
- `app/middleware.py`: 请求中间件（RequestId, 请求体限制, 错误处理）
- `app/api/data_plane.py`: 数据面路由 (`/api/*`)
- `app/api/control_plane.py`: 控制面路由 (`/admin/*`)
- `app/api/firecrawl_compat.py`: Firecrawl 兼容层 (`/v1/*`)
- `app/db/models.py`: SQLAlchemy 数据模型
- `app/config.py`: 配置加载（YAML + 环境变量覆盖）

### 前端结构
- `webui/src/views/`: Vue 页面组件（ClientsKeysView, LogsView 等）
- `webui/src/api/`: API 调用封装
- `webui/src/state/`: 全局状态管理（adminToken）
- 构建输出：`app/ui2/`（被 `.gitignore` 忽略，需手动构建）

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

**其他文档**：
- `docs/MVP/Firecrawl-API-Manager-PRD.md` - 产品需求文档
- `docs/handbook.md` - 接入方/运维快速手册
- `docs/WORKLOG.md` - 变更日志
- `docs/project/TD.md` - 实施待办清单

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
