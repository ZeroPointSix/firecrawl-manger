# Firecrawl API Manager (FCAM)

[![CI](https://github.com/ZeroPointSix/firecrawl-manger/actions/workflows/ci.yml/badge.svg)](https://github.com/ZeroPointSix/firecrawl-manger/actions/workflows/ci.yml)
[![Docker Image](https://img.shields.io/docker/v/guangshanshui/firecrawl-manager?label=docker&logo=docker)](https://hub.docker.com/r/guangshanshui/firecrawl-manager)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

> 🚀 轻量级 Firecrawl API 网关，集中管理多个 API Key，提供统一转发、智能负载均衡、配额控制和完整审计。

---

## 📖 目录

- [核心特性](#-核心特性)
- [快速开始](#-快速开始)
- [架构概览](#-架构概览)
- [部署方式](#-部署方式)
- [配置说明](#-配置说明)
- [API 文档](#-api-文档)
- [开发指南](#-开发指南)
- [常见问题](#-常见问题)
- [文档导航](#-文档导航)
- [贡献指南](#-贡献指南)
- [许可证](#-许可证)

---

## ✨ 核心特性

### 🔑 Key 池管理
- **集中管理**：统一管理多个 Firecrawl API Key
- **智能轮询**：自动负载均衡，避免单个 Key 过载
- **故障转移**：自动检测失效 Key 并切换到可用 Key
- **冷却机制**：429 限流时自动冷却，避免频繁重试
- **加密存储**：使用 Fernet 对称加密保护 API Key

### 📊 额度监控
- **智能刷新**：根据额度使用率动态调整刷新频率
- **本地估算**：实时估算请求消耗，减少上游 API 调用
- **历史追踪**：记录额度快照，支持趋势分析和可视化
- **Client 聚合**：按 Client 分组展示总额度

### 🎯 流量控制
- **配额管理**：按 Client 设置每日请求配额
- **限流保护**：令牌桶算法，防止突发流量
- **并发控制**：限制单个 Client 的并发请求数
- **幂等性保证**：支持 Idempotency-Key，避免重复请求

### 🔒 安全与审计
- **双平面架构**：数据面和控制面分离，支持端口隔离
- **Client 鉴权**：Bearer Token 认证，细粒度权限控制
- **完整审计**：记录所有操作日志，支持追溯和分析
- **敏感字段脱敏**：自动脱敏日志中的敏感信息

### 🎨 管理界面
- **现代化 WebUI**：Vue 3 + TypeScript + Naive UI
- **实时监控**：仪表盘展示请求统计、额度使用、Key 状态
- **批量操作**：支持批量启用、禁用、删除 Client
- **可视化图表**：请求趋势图、额度趋势图

### 🐳 容器化部署
- **Docker 支持**：开箱即用的 Docker 镜像
- **多环境配置**：开发、生产、公开部署等多种场景
- **数据库支持**：SQLite（开发）、PostgreSQL（生产）
- **分布式状态**：Redis 支持多实例部署

---

## 🚀 快速开始

### 使用 Docker（推荐）

```bash
# 1. 创建配置文件
cat > .env <<EOF
FCAM_ADMIN_TOKEN=your_secure_admin_token_here
FCAM_MASTER_KEY=your_32_bytes_master_key_here____
EOF

# 2. 启动服务
docker run -d \
  --name fcam \
  -p 8000:8000 \
  -v fcam_data:/app/data \
  --env-file .env \
  guangshanshui/firecrawl-manager:latest

# 3. 访问服务
# WebUI: http://localhos
# API Docs: http://localhost:8000/docs
# Health Check: http://localhost:8000/healthz
```

### 使用 Docker Compose

```bash
# 1. 克隆仓库
git clone https://github.com/ZeroPointSix/firecrawl-manger.git
cd firecrawl-manger

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 文件，设置 FCAM_ADMIN_TOKEN 和 FCAM_MASTER_KEY

# 3. 启动服务
docker compose up -d

# 4. 查看日志
docker compose logs -f
```

### 本地开发

```bash
# 1. 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate  # Windows

# 2. 安装依赖
pip install -r requirements.txt -r requirements-dev.txt

# 3. 配置环境变量
export FCAM_ADMIN_TOKEN=dev_admin_token
export FCAM_MASTER_KEY=dev_master_key_32_bytes_minimum____

# 4. 初始化数据库
alembic upgrade head

# 5. 启动服务
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 6. 访问服务
# WebUI: http://localhost:8000/ui2/
# API Docs: http://localhost:8000/docs
```

---

## 🏗️ 架构概览

### 双平面架构

```
┌─────────────────────────────────────────────────────────────┐
│                        FCAM Gateway                          │
├─────────────────────────────────────────────────────────────┤
│  数据面 (Data Plane)          │  控制面 (Control Plane)      │
│  /api/*, /v1/*, /v2/*         │  /admin/*                   │
│                               │                             │
│  • Client 鉴权                │  • Key 管理 (CRUD)          │
│  • 请求转发                   │  • Client 管理 (CRUD)       │
│  • Key 池轮询                 │  • 批量操作                 │
│  • 限流/配额/并发             │  • 请求日志查询             │
│  • 幂等性保证                 │  • 审计日志查询             │
│  • 资源粘性绑定               │  • 统计数据查询             │
│  • 自动重试/故障转移          │  • 额度监控                 │
│                               │  • WebUI 管理界面           │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│                    Firecrawl API (上游)                      │
│  https://api.firecrawl.dev                                  │
└─────────────────────────────────────────────────────────────┘
```

### 核心组件

- **Key Pool**: Key 池管理，负载均衡和故障转移
- **Forwarder**: 核心转发逻辑，自动重试和冷却处理
- Rate Limiter**: 令牌桶限流（内存/Redis）
- **Concurrency Manager**: 并发控制（租约模式）
- **Credit Monitor**: 额度监控（智能刷新、本地估算）
- **Resource Binding**: 资源粘性绑定（同一资源使用同一 Key）
- **Idempotency**: 幂等性处理（Idempotency-Key 支持）

---

## 🐳 部署方式

### 1. 开发环境（SQLite）

适合本地开发和测试。

```bash
docker compose up --build
```

**特点**：
- 使用 SQLite 数据库
- 内存状态管理
- 单实例部署
- 快速启动

### 2. 生产环境（PostgreSQL + Redis）

适合生产环境，支持多实例部署。

```bash
docker compose --profile prod up --build postgres redis fcam_api fcam_admin
```

**特点**：
- PostgreSQL 数据库（支持并发写入）
- Redis 分布式状态（限流、并发、冷却）
- 数据面和控制面端口隔离
- 支持水平扩展

**端口配置**：
- 数据面（`/api/*`）：对外暴露 `:8000`
- 控制面（`/admin/*`）：仅绑定 `127.0.0.1:8001`

### 3. 公开部署（仅数据面）

适合对外提供 API 服务，隐藏管理界面。

```bash
docker compose --profile public up --build fcam_public
```

**特点**：
- 仅启用数据面（`server.enable_control_plane=false`）
- 控制面通过独立实例管理
- 更高的安全性

### 4. ClawCloud / 容器云部署

参考文档：[docs/deploy-clawcloud.md](docs/deploy-clawcloud.md)

**推荐配置**：
- 使用 PostgreSQL（避免 SQLite 在 PVC 上的问题）
- 固定版本 tag（避免 `latest` 漂移）
- 配置健康检查（`/healthz`, `/readyz`）

---

## ⚙️ 配置说明

### 环境变量（必需）

```bash
# 管理员 Token（用于访问控制面 /admin/*）
FCAM_ADMIN_TOKEN=your_secure_admin_token_here

# Master Key（用于加密存储 API Key，至少 32 字节）
FCAM_MASTER_KEY=your_32_bytes_master_key_here____
```

### 配置文件（可选）

主配置文件：`config.yaml`

```yaml
server:
  host: "0.0.0.0"
  port: 8000
  enable_docs: true          # 是否启用 Swagger 文档
  enable_data_plane: true    # 是否启用数据面
  enable_control_plane: true # 是否启用控制面

firecrawl:
  base_url: "https://api.firecrawl.dev"
  timeout: 30
  max_retries: 3

security:
  client_auth:
    enabled: true
    scheme: "bearer"
  request_limits:
    max_body_bytes: 1048576  # 1MB
    allowed_paths: ["scrape", "crawl", "search", "agent", "map", "extract", "batch", "browser", "team"]

quota:
  timezone: "UTC"
  count_mode: "success"      # success | attempt
  default_daily_limit: 5
  reset_time: "00:00"

rate_limit:
  cooldown_seconds: 60

credit_monitoring:
  enabled: true
  smart_refresh:
    enabled: true
    high_usage_interval: 15  # 高使用率时刷新间隔（秒）
    normal_usage_interval: 60

database:
  path: "./data/api_manager.db"  # SQLite
  # url: "postgresql+psycopg://user:pass@host:5432/db"  # PostgreSQL

state:
  mode: "memory"  # memory | redis
  redis:
    url: "redis://localhost:6379/0"
    key_prefix: "fcam"

observability:
  metrics_enabled: false
  metrics_path: "/metrics"
  retention:
    request_logs_days: 30
    audit_logs_days: 90

logging:
  level: "INFO"
  format: "json"  # json | plain
  redact_fields: ["authorization", "api_key", "token", "cookie"]
```

### 环境变量覆盖

使用 `FCAM_` 前缀覆盖配置文件中的值：

```bash
# 嵌套字段使用双下划线
FCAM_SERVER__PORT=8001
FCAM_SERVER__ENABLE_DOCS=false
FCAM_DATABASE__URL=postgresql+psycopg://user:pass@host:5432/db
FCAM_STATE__MODE=redis
FCAM_STATE__REDIS__URL=redis://localhost:6379/0
```

---

## 📚 API 文档

### 数据面 API（`/api/*`, `/v1/*`, `/v2/*`）

**鉴权方式**：Bearer Token（Client Key）

```bash
# 示例：抓取网页
curl -X POST http://localhost:8000/api/scrape \
  -H "Authorization: Bearer <client_key>" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com"}'
```

**支持的端点**：
- `POST /api/scrape` - 单页抓取
- `POST /api/crawl` - 网站爬取
- `POST /api/search` - 网页搜索
- `POST /api/map` - 网站地图
- `POST /api/agent` - AI Agent
- `POST /api/extract` - 数据提取
- `POST /api/batch` - 批量操作
- `POST /api/browser` - 浏览器操作
- 完整兼容 Firecrawl v1 和 v2 API

### 控制面 API（`/admin/*`）

**鉴权方式**：Bearer Token（Admin Token）

```bash
# 示例：获取所有 Key
curl -X GET http://localhost:8000/admin/keys \
  -H "Authorization: Bearer <admin_token>"
```

**主要端点**：
- **Key 管理**：
  - `GET /admin/keys` - 列出所有 Key
  - `POST /admin/keys` - 创建 Key
  - `PATCH /admin/keys/{key_id}` - 更新 Key
  - `DELETE /admin/keys/{key_id}` - 删除 Key
  - `POST /admin/keys/test` - 批量测试 Key
  - `POST /admin/keys/import` - 批量导入 Key

- **Client 管理**：
  - `GET /admin/clients` - 列出所有 Client
  - `POST /admin/clients` - 创建 Client
  - `PATCH /admin/clients/{client_id}` - 更新 Client
  - `DELETE /admin/clients/{client_id}` - 删除 Client
  - `PATCH /admin/clients/batch` - 批量操作（启用、禁用、删除）

- **额度监控**：
  - `GET /admin/credits/keys/{key_id}` - 获取 Key 额度
  - `GET /admin/credits/clients/{client_id}` - 获取 Client 总额度
  - `GET /admin/credits/keys/{key_id}/history` - 获取额度历史
  - `POST /admin/credits/keys/{key_id}/refresh` - 手动刷新额度

- **日志查询**：
  - `GET /admin/logs/requests` - 请求日志
  - `GET /admin/logs/audit` - 审计日志

- **统计数据**：
  - `GET /admin/stats/summary` - 统计摘要
  - `GET /admin/stats/requests/trend` - 请求趋势

详细 API 文档：[docs/MVP/Firecrawl-API-Manager-API-Contract.md](docs/MVP/Firecrawl-API-Manager-API-Contract.md)

---

## 🛠️ 开发指南

### 前置要求

- Python 3.11+
- Node.js 18+ (前端开发)
- Docker & Docker Compose (可选)

### 开发环境设置

```bash
# 1. 克隆仓库
git clone https://github.com/ZeroPointSix/firecrawl-manger.git
cd firecrawl-manger

# 2. 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate  # Windows

# 3. 安装依赖
pip install -r requirements.txt -r requirements-dev.txt

# 4. 配置环境变量
export FCAM_ADMIN_N=dev_admin_token
export FCAM_MASTER_KEY=dev_master_key_32_bytes_minimum____

# 5. 初始化数据库
alembic upgrade head

# 6. 启动后端服务
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 前端开发

```bash
cd webui

# 安装依赖
npm ci

# 开发模式（热重载）
npm run dev

# 类型检查
npm run type-check

# 构建生产版本
npm run build  # 输出到 app/ui2/
```

### 运行测试

```bash
# 运行所有测试（覆盖率要求 80%）
pytest --cov=app --cov-fail-under=80

# 运行单元测试
pytest tests/unit -v

# 运行集成测试
pytest tests/integration -v

# 运行 E2E 测试（需要设置环境变量）
export FCAM_E2E=1
pytest tests/e2e -v

# 生成覆盖率报告
pytest --cov=app --cov-report=html
# 查看报告：open htmlcov/index.html
```

### 代码规范

```bash
# 代码格式化
ruff format .

# 代码检查
ruff check .

# 自动修复
ruff check --fix .
```

### 数据库迁移

```bash
# 创建新迁移
alembic revision --autogenerate -m "描述"

# 应用迁移
alembic upgrade head

# 回滚一个版本
alembic downgrade -1

# 查看迁移历史
alembic history
```

---

## ❓ 常见问题

### 1. `ADMIN_UNAUTHORIZED: Missing or invalid admin token`

**原因**：调用 `/admin/*` 时未携带或携带了错误的 Admin Token。

**解决方案**：
1. 确认环境变量 `FCAM_ADMIN_TOKEN` 已正确设置
2. 在请求头中添加：`Authorization: Bearer <your_admin_token>`
3. 如果使用 WebUI，重新输入 Admin Token 并保存
4. 如果使用 Swagger，建议改用 WebUI 或命令行工具

**自检命令**：
```bash
# Linux/Mac
curl -H "Authorization: Bearer <your_admin_token>" \
  http://localhost:8000/admin/keys

# PowerShell
$headers = @{ Authorization = "Bearer <your_admin_token>" }
Invoke-RestMethod -Uri "http://localhost:8000/admin/keys" -Headers $headers
```

### 2. `sqlite3.OperationalError: unable to open database file`

**原因**：Docker 容器以非 root 用户（uid 10001）运行，宿主机目录权限不足。

**解决方案**：

**方案 1**：修改目录权限（Linux）
```bash
mkdir -p ./data
sudo chown -R 10001:10001 ./data
```

**方案 2**：使用 Docker 命名卷（推荐）
```bash
docker volume create fcam_data
docker run -v fcam_data:/app/data ...
```

**方案 3**：使用 PostgreSQL（生产环境推荐）
```bash
export FCAM_DATABASE__URL=postgresql+psycopg://user:pass@host:5432/db
```

### 3. WebUI 显示"UI2 静态文件尚未构建"

**原因**：前端未构建，`app/ui2/` 目录不存在。

**解决方案**：
```bash
cd webui
npm ci
npm run build
```

**注意**：`app/ui2/` 目录被 `.gitignore` 忽略，需要手动构建。

### 4. 测试覆盖率不足 80%

**解决方案**：
```bash
# 生成详细覆盖率报告
pytest --cov=app --cov-report=html

# 查看未覆盖的代码
open htmlcov/index.html  # Mac
# start htmlcov/index.html  # Windows
```

优先覆盖核心业务逻辑：
- `app/core/forwarder.py`
- `app/core/key_pool.py`
- `app/middleware.py`

### 5. Docker 镜像构建失败

**常见原因**：
- 网络问题（无法下载依赖）
- 磁盘空间不足
- Docker 版本过旧

**解决方案**：
```bash
# 清理 Docker 缓存
docker system prune -a

# 使用国内镜像源
docker build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple .

# 检查 Docker 版本
docker --version  # 建议 20.10+
```

### 6. 如何水平扩展？

**要求**：
1. 使用 PostgreSQL 数据库
2. 使用 Redis 分布式状态（`state.mode: redis`）
3. 配置负载均衡器（Nginx, HAProxy）

**示例配置**：
```yaml
# config.yaml
database:
  url: "postgresql+psycopg://user:pass@host:5432/db"

state:
  mode: "redis"
  redis:
    url: "redis://localhost:6379/0"
```

**启动多个实例**：
```bash
# 实例 1
docker run -p 8000:8000 --env-file .env guangshanshui/firecrawl-manager

# 实例 2
docker run -p 8001:8000 --env-file .env guangshanshui/firecrawl-manager

# 实例 3
docker run -p 8002:8000 --env-file .env guangshanshui/firecrawl-manager
```

**Nginx 负载均衡**：
```nginx
upstream fcam_backend {
    server localhost:8000;
    server localhost:8001;
    server localhost:8002;
}

server {
    listen 80;
    location / {
        proxy_pass http://fcam_backend;
    }
}
```

---

## 📖 文档导航

### 核心文档
- [技术方案与架构设计](docs/agent.md) - 单一事实来源
- [API 接口契约](docs/MVP/Firecrawl-API-Manager-API-Contract.md) - 完整 API 规范
- [API 使用指南](docs/API-Usage.md) - 快速上手指南
- [用户手册](docs/handbook.md) - 接入方/运维手册

### 部署文档
- [Docker 部署指南](docs/docker.md) - 详细部署说明
- [ClawCloud 部署指南](docs/deploy-clawcloud.md) - 容器云部署
- [部署经验总结](docs/Exp/README.md) - 排障经验

### 产品文档
- [产品需求文档](docs/MVP/Firecrawl-API-Manager-PRD.md) - 产品设计
- [技术设计文档](docs/MVP/Firecrawl-API-Manager-Technical-Design.md) - 技术架构
- [WebUI 设计方案](docs/MVP/Firecrawl-API-Manager-WebUI2-Frontend-Solution.md) - 前端设计

### 开发文档
- [仓库开发指南](AGENTS.md) - 贡献者指南
- [项目说明文档](CLAUDE.md) - 项目上下文
- [变更日志](docs/WORKLOG.md) - 实施记录
- [测试文档](tests/README.md) - 测试指南

---

## 🤝 贡献指南

我们欢迎所有形式的贡献！

### 如何贡献

1. **Fork 仓库**
2. **创建特性分支**：`git checkout -b feature/amazing-feature`
3. **提交变更**：`git commit -m 'feat: add amazing feature'`
4. **推送分支**：`git push origin feature/amazing-feature`
5. **提交 Pull Request**

### 提交规范

使用 [Conventional Commits](https://www.conventionalcommits.org/) 规范：

- `feat:` 新功能
- `fix:` Bug 修复
- `docs:` 文档更新
- `style:` 代码格式（不影响功能）
- `refactor:` 重构
- `test:` 测试相关
- `chore:` 构建/工具相关

### 代码规范

- Python: 使用 Ruff 格式化和检查（`ruff format .`, `ruff check .`）
- TypeScript: 使用 ESLint 和 Prettier
- 测试覆盖率要求 80%
- 所有 PR 必须通过 CI 检查

### 报告问题

使用 [GitHub Issues](https://github.com/ZeroPointSix/firecrawl-manger/issues) 报告 Bug 或提出功能请求。

---

## 📊 项目统计

- **代码行数**：~15,000 行（Python + TypeScript）
- **测试覆盖率**：80%+
- **测试用例**：200+ 个
- **Docker 镜像大小**：~200MB
- **启动时间**：<5 秒

---

## 🌟 Star History

如果这个项目对你有帮助，请给我们一个 ⭐️！

[![Star History Chart](https://api.star-history.com/svg?repos=ZeroPointSix/firecrawl-manger&type=Date)](https://star-history.com/#ZeroPointSix/firecrawl-manger&Date)

---

## 📄 许可证

本项目采用 [MIT License](LICENSE) 开源协议。

---

## 🔗 相关链接

- **GitHub**: https://github.com/ZeroPointSix/firecrawl-manger
- **Docker Hub**: https://hub.docker.com/r/guangshanshui/firecrawl-manager
- **Firecrawl 官网**: https://firecrawl.dev
- **问题反馈**: https://github.com/ZeroPointSix/firecrawl-manger/issues

---

## 💬 联系我们

- **GitHub Issues**: [提交问题](https://github.com/ZeroPointSix/firecrawl-manger/issues)
- **GitHub Discussions**: [参与讨论](https://github.com/ZeroPointSix/firecrawl-manger/discussions)

---

<div align="center">

**[⬆ 回到顶部](#firecrawl-api-manager-fcam)**

Made with ❤️ by the FCAM Team

</div>
