# Firecrawl API Manager (FCAM)

[![CI](https://github.com/ZeroPointSix/firecrawl-manger/actions/workflows/ci.yml/badge.svg)](https://github.com/ZeroPointSix/firecrawl-manger/actions/workflows/ci.yml)
[![Docker Image](https://img.shields.io/docker/v/guangshanshui/firecrawl-manager?label=docker&logo=docker)](https://hub.docker.com/r/guangshanshui/firecrawl-manager)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Firecrawl API Manager 是一个轻量级 HTTP 网关，用于集中管理多个 Firecrawl API Key，提供统一的转发入口、智能负载均衡、配额控制和完整审计。

## 核心特性

- **Key 池管理**: 集中管理多个 API Key，自动负载均衡和故障转移
- **额度监控**: 智能刷新策略，实时追踪额度使用情况
- **流量控制**: 配额管理、限流保护、并发控制
- **安全审计**: 双平面架构，完整的操作日志和审计追踪
- **管理界面**: 现代化 WebUI，支持批量操作和可视化监控
- **容器化部署**: 开箱即用的 Docker 镜像，支持多种部署场景

## 快速开始

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
# WebUI: http://localhost:8000/ui2/
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

## 部署方式

### 开发环境（SQLite）

适合本地测试和开发。

```bash
docker compose up -d
```

### 生产环境（PostgreSQL + Redis）

适合生产环境，支持多实例部署和水平扩展。

```bash
docker compose --profile prod up -d postgres redis fcam_api fcam_admin
```

**特点**:
- PostgreSQL 数据库（支持并发写入）
- Redis 分布式状态（限流、并发、冷却）
- 数据面和控制面端口隔离
- 支持水平扩展

**端口配置**:
- 数据面（`/api/*`）: 对外暴露 `:8000`
- 控制面（`/admin/*`）: 仅绑定 `127.0.0.1:8001`

### 公开部署（仅数据面）

适合对外提供 API 服务，隐藏管理界面。

```bash
docker compose --profile public up -d fcam_public
```

### 容器云部署

参考文档: [docs/deploy-clawcloud.md](docs/deploy-clawcloud.md)

推荐配置:
- 使用 PostgreSQL（避免 SQLite 在 PVC 上的问题）
- 固定版本 tag（避免 `latest` 漂移）
- 配置健康检查（`/healthz`, `/readyz`）

## 配置说明

### 必需的环境变量

```bash
# 管理员 Token（用于访问控制面 /admin/*）
FCAM_ADMIN_TOKEN=your_secure_admin_token_here

# Master Key（用于加密存储 API Key，至少 32 字节）
FCAM_MASTER_KEY=your_32_bytes_master_key_here____
```

### 可选配置

主配置文件: `config.yaml`

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

database:
  path: "./data/api_manager.db"  # SQLite
  # url: "postgresql+psycopg://user:pass@host:5432/db"  # PostgreSQL

state:
  mode: "memory"  # memory | redis
  redis:
    url: "redis://localhost:6379/0"
```

### 环境变量覆盖

使用 `FCAM_` 前缀覆盖配置文件中的值（嵌套字段使用双下划线）:

```bash
FCAM_SERVER__PORT=8001
FCAM_SERVER__ENABLE_DOCS=false
FCAM_DATABASE__URL=postgresql+psycopg://user:pass@host:5432/db
FCAM_STATE__MODE=redis
```

## 使用指南

### 管理界面

访问 `http://localhost:8000/ui2/` 打开 WebUI 管理界面。

首次访问需要输入 Admin Token（即环境变量 `FCAM_ADMIN_TOKEN` 的值）。

**主要功能**:
- Key 管理: 添加、编辑、删除、测试 Firecrawl API Key
- Client 管理: 创建客户端、设置配额和限流
- 额度监控: 查看 Key 额度使用情况和历史趋势
- 请求日志: 查询和分析请求记录
- 统计数据: 仪表盘展示请求统计和趋势图

### API 使用

#### 数据面 API（`/api/*`, `/v1/*`, `/v2/*`）

用于转发 Firecrawl API 请求。

**鉴权方式**: Bearer Token（Client Key）

```bash
# 示例: 抓取网页
curl -X POST http://localhost:8000/api/scrape \
  -H "Authorization: Bearer <client_key>" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com"}'
```

**支持的端点**:
- `POST /api/scrape` - 单页抓取
- `POST /api/crawl` - 网站爬取
- `POST /api/search` - 网页搜索
- `POST /api/map` - 网站地图
- `POST /api/agent` - AI Agent
- `POST /api/extract` - 数据提取
- `POST /api/batch` - 批量操作
- `POST /api/browser` - 浏览器操作

完整兼容 Firecrawl v1 和 v2 API。

#### 控制面 API（`/admin/*`）

用于管理 Key、Client 和查询日志。

**鉴权方式**: Bearer Token（Admin Token）

```bash
# 示例: 获取所有 Key
curl -X GET http://localhost:8000/admin/keys \
  -H "Authorization: Bearer <admin_token>"
```

详细 API 文档: [docs/MVP/Firecrawl-API-Manager-API-Contract.md](docs/MVP/Firecrawl-API-Manager-API-Contract.md)

## 常见问题

### 1. `ADMIN_UNAUTHORIZED: Missing or invalid admin token`

**原因**: 调用 `/admin/*` 时未携带或携带了错误的 Admin Token。

**解决方案**:
1. 确认环境变量 `FCAM_ADMIN_TOKEN` 已正确设置
2. 在请求头中添加: `Authorization: Bearer <your_admin_token>`
3. 如果使用 WebUI，重新输入 Admin Token 并保存

### 2. `sqlite3.OperationalError: unable to open database file`

**原因**: Docker 容器以非 root 用户（uid 10001）运行，宿主机目录权限不足。

**解决方案**:

方案 1: 修改目录权限（Linux）
```bash
mkdir -p ./data
sudo chown -R 10001:10001 ./data
```

方案 2: 使用 Docker 命名卷（推荐）
```bash
docker volume create fcam_data
docker run -v fcam_data:/app/data ...
```

方案 3: 使用 PostgreSQL（生产环境推荐）
```bash
export FCAM_DATABASE__URL=postgresql+psycopg://user:pass@host:5432/db
```

### 3. WebUI 显示"UI2 静态文件尚未构建"

**原因**: 使用源码部署时，前端未构建。

**解决方案**:
```bash
cd webui
npm ci
npm run build
```

Docker 镜像已包含构建好的前端，无需手动构建。

### 4. 如何水平扩展？

**要求**:
1. 使用 PostgreSQL 数据库
2. 使用 Redis 分布式状态（`state.mode: redis`）
3. 配置负载均衡器（Nginx, HAProxy）

**示例配置**:
```yaml
# config.yaml
database:
  url: "postgresql+psycopg://user:pass@host:5432/db"

state:
  mode: "redis"
  redis:
    url: "redis://localhost:6379/0"
```

启动多个实例，并使用负载均衡器分发请求。

## 架构说明

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
- **Rate Limiter**: 令牌桶限流（内存/Redis）
- **Concurrency Manager**: 并发控制（租约模式）
- **Credit Monitor**: 额度监控（智能刷新、本地估算）
- **Resource Binding**: 资源粘性绑定（同一资源使用同一 Key）
- **Idempotency**: 幂等性处理（Idempotency-Key 支持）

## 文档

### 用户文档
- [API 使用指南](docs/API-Usage.md) - 快速上手指南
- [用户手册](docs/handbook.md) - 接入方/运维手册
- [API 接口契约](docs/MVP/Firecrawl-API-Manager-API-Contract.md) - 完整 API 规范

### 部署文档
- [Docker 部署指南](docs/docker.md) - 详细部署说明
- [ClawCloud 部署指南](docs/deploy-clawcloud.md) - 容器云部署
- [部署经验总结](docs/Exp/README.md) - 排障经验

### 开发文档
- [技术方案与架构设计](docs/agent.md) - 技术架构详解
- [仓库开发指南](AGENTS.md) - 贡献者指南
- [变更日志](docs/WORKLOG.md) - 实施记录

## 相关链接

- **GitHub**: https://github.com/ZeroPointSix/firecrawl-manger
- **Docker Hub**: https://hub.docker.com/r/guangshanshui/firecrawl-manager
- **CI/CD**: 查看 [GitHub Actions](https://github.com/ZeroPointSix/firecrawl-manger/actions) 了解构建状态
- **Firecrawl 官网**: https://firecrawl.dev
- **问题反馈**: https://github.com/ZeroPointSix/firecrawl-manger/issues

## 许可证

本项目采用 [MIT License](LICENSE) 开源协议。
