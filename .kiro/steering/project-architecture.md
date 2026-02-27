# Firecrawl API Manager - 项目架构

## 项目概述

Firecrawl API Manager (FCAM) 是一个轻量级 HTTP 网关，用于集中管理多个 Firecrawl API Key，提供统一的转发入口和控制面板。

**核心价值：**
- 统一管理多个 Firecrawl API Key
- 智能负载均衡和故障转移
- 细粒度的配额和限流控制
- 完整的审计日志和监控

## 技术栈

### 后端
- **框架**: FastAPI (Python 3.11+)
- **Web 服务器**: Uvicorn
- **ORM**: SQLAlchemy 2.x
- **数据库迁移**: Alembic
- **HTTP 客户端**: httpx
- **加密**: cryptography (Fernet)

### 前端
- **框架**: Vue 3 (Composition API)
- **语言**: TypeScript
- **构建工具**: Vite
- **UI 库**: Naive UI
- **路由**: Vue Router (Hash 模式)

### 数据存储
- **开发环境**: SQLite
- **生产环境**: PostgreSQL
- **状态存储**: 内存 (开发) / Redis (生产)

### 容器化
- **容器**: Docker
- **编排**: docker-compose

## 核心架构

### 1. 双平面架构

#### 数据面 (Data Plane)
- **路径**: `/api/*`, `/v1/*`, `/v2/*`
- **功能**: 业务请求转发
- **特性**:
  - Client 鉴权 (Bearer Token)
  - 限流控制 (令牌桶算法)
  - 配额管理 (每日限额)
  - 并发控制 (最大并发数)
  - Key 池轮询选择
  - 自动重试和故障转移
  - 幂等性保证
  - 资源粘性绑定

#### 控制面 (Control Plane)
- **路径**: `/admin/*`
- **功能**: Key/Client 管理、审计日志、统计查询
- **特性**:
  - Admin Token 鉴权
  - Key 管理 (CRUD, 批量测试, 导入)
  - Client 管理 (CRUD, 批量操作)
  - 请求日志查询
  - 审计日志查询
  - 统计数据查询
  - WebUI 管理界面

### 2. 目录结构

```
firecrawl-manger/
├── app/                          # 后端应用
│   ├── api/                      # API 路由层
│   │   ├── control_plane.py      # 控制面路由 (58KB, 核心管理接口)
│   │   ├── data_plane.py         # 数据面路由 (5KB, 转发入口)
│   │   ├── firecrawl_compat.py   # Firecrawl v1 兼容层
│   │   ├── firecrawl_v2_compat.py # Firecrawl v2 兼容层 (28KB)
│   │   ├── health.py             # 健康检查
│   │   └── deps.py               # 依赖注入
│   ├── core/                     # 核心业务逻辑 (约 2922 行)
│   │   ├── forwarder.py          # 核心转发逻辑
│   │   ├── key_pool.py           # Key 池管理
│   │   ├── concurrency.py        # 并发控制 (内存/Redis)
│   │   ├── rate_limit.py         # 限流控制 (令牌桶)
│   │   ├── idempotency.py        # 幂等性处理
│   │   ├── cooldown.py           # 冷却管理
│   │   ├── security.py           # 加密解密
│   │   ├── resource_binding.py   # 资源粘性绑定
│   │   ├── batch_clients.py      # 批量客户端操作
│   │   ├── key_import.py         # Key 导入
│   │   ├── credit_fetcher.py     # 信用额度获取
│   │   ├── credit_estimator.py   # 信用额度估算
│   │   ├── credit_aggregator.py  # 信用额度聚合
│   │   ├── credit_refresh.py     # 信用额度刷新
│   │   ├── credit_refresh_scheduler.py  # 信用额度刷新调度
│   │   ├── redact.py             # 数据脱敏工具
│   │   └── time.py               # 时间工具
│   ├── db/                       # 数据库层
│   │   ├── models.py             # 数据模型 (183 行)
│   │   ├── session.py            # 会话管理
│   │   └── cleanup.py            # 数据清理
│   ├── observability/            # 可观测性
│   │   ├── logging.py            # 结构化日志
│   │   └── metrics.py            # Prometheus 指标
│   ├── config.py                 # 配置管理 (227 行)
│   ├── errors.py                 # 错误处理
│   ├── middleware.py             # 中间件
│   └── main.py                   # 应用入口 (192 行)
├── webui/                        # 前端应用
│   ├── src/
│   │   ├── views/                # 页面组件
│   │   │   ├── DashboardView.vue # 仪表盘
│   │   │   ├── ClientsKeysView.vue # Client/Key 管理
│   │   │   ├── LogsView.vue      # 请求日志
│   │   │   └── AuditView.vue     # 审计日志
│   │   ├── api/                  # API 调用封装
│   │   │   ├── clients.ts
│   │   │   ├── keys.ts
│   │   │   ├── logs.ts
│   │   │   ├── dashboard.ts
│   │   │   ├── credits.ts        # 信用额度 API
│   │   │   └── http.ts           # HTTP 客户端封装
│   │   ├── components/           # 可复用组件
│   │   │   ├── ConnectModal.vue
│   │   │   ├── StatCard.vue
│   │   │   ├── RequestTrendChart.vue
│   │   │   ├── CreditDisplay.vue      # 信用额度显示
│   │   │   └── CreditTrendChart.vue   # 信用额度趋势图
│   │   ├── state/                # 全局状态
│   │   ├── router/               # 路由配置
│   │   └── utils/                # 工具函数
│   │       └── time.ts           # 时间工具
│   └── di2/          # 构建输出
├── migrations/                   # 数据库迁移
│   └── versions/                 # 迁移版本
│       ├── 0001_init.py
│       ├── 0002_add_retry_count_to_request_logs.py
│       ├── 0003_add_account_fields_to_api_keys.py
│       ├── 0004_add_client_id_to_api_keys.py
│       ├── 0005_add_error_details_to_request_logs.py
│       ├── 0006_add_upstream_resource_bindings.py
│       ├── 0007_add_status_to_clients.py
│       ├── 0008_add_credit_monitoring.py
│       └── 0009_add_credit_monitoring_indexes.py
├── tests/                        # 测试套件
│   ├── unit/                     # 单元测试 (核心逻辑单元测试)
│   ├── integration/              # 集成测试 (API 集成测试)
│   ├── e2e/                      # 端到端测试 (真实 HTTP 请求)
│   ├── conftest.py               # pytest 配置和 fixtures
│   ├── README.md                 # 测试文档
│   └── TEST_SUMMARY.md           # 测试总结
├── scripts/                      # 工具脚本
├── docs/                         # 项目文档
│   ├── MVP/                      # MVP 阶段文档
│   ├── BUG/                      # Bug 分析文档
│   ├── FD/                       # 功能设计文档
│   ├── PRD/                      # 产品需求文档
│   ├── TDD/                      # 测试驱动开发文档
│   ├── Opt/                      # 优化文档
│   └── TODO/                     # 待办事项文档
├── config.yaml                   # 配置文件
└── docker-compose.yml            # Docker 编排

```

### 3. 数据模型

#### ApiKey (API 密钥)
- **字段**: id, client_id, api_key_ciphertext, api_key_hash, api_key_last4
- **账户信息**: account_username, account_password_ciphertext, account_verified_at
- **配额**: daily_quota, daily_usage, quota_reset_at
- **限流**: rate_limit_per_min, max_concurrent, current_concurrent, cooldown_until
- **状态**: status (active/cooling/failed/disabled/quota_exceeded/decrypt_failed)
- **统计**: total_requests, last_used_at, created_at

#### Client (客户端)
- **字段**: id, name, token_hash, is_active
- **配额**: daily_quota, daily_usage, quota_reset_at
- **限流**: rate_limit_per_min, max_concurrent
- **状态**: status (active/disabled)
- **统计**: created_at, last_used_at

#### RequestLog (请求日志)
- **字段**: id, request_id, client_id, api_key_id
- **请求**: endpoint, method
- **响应**: status_code, response_time_ms, success
- **重试**: retry_count, error_message, error_details
- **幂等**: idempotency_key
- **时间**: created_at

#### IdempotencyRecord (幂等性记录)
- **字段**: id, client_id, idempotency_key, request_hash
- **状态**: status (processing/completed/failed)
- **响应**: response_status_code, response_body
- **时间**: created_at, expires_at

#### UpstreamResourceBinding (资源绑定)
- **字段**: id, client_id, api_key_id
- **资源**: resource_type, resource_id
- **时间**: created_at, expires_at
- **用途**: 实现资源粘性 (同一 Client 访问同一资源时使用同一 Key)

#### AuditLog (审计日志)
- **字段**: id, actor_type, actor_id, action
- **资源**: resource_type, resource_id
- **上下文**: ip, user_agent
- **时间**: created_at

### 4. 核心流程

#### 请求转发流程 (forwarder.py)
1. **Client 鉴权**: 验证 Bearer Token
2. **限流检查**: 检查 Client 级别限流
3. **配额检查**: 检查 Client 每日配额
4. **并发控制**: 检查 Client 并发数
5. **幂等性检查**: 检查是否为重复请求
6. **Key 选择**: 从 Key 池中选择可用 Key
   - 检查 Key 状态 (active/cooling/failed)
   - 检查 Key 配额
   - 检查 Key 限流
   - 检查 K 并发数
   - 资源粘性绑定 (如果适用)
7. **请求转发**: 向上游 Firecrawl API 发送请求
8. **错误处理**:
   - 429 (限流): 标记 Key 为 cooling 状态，切换到下一个 Key
   - 401/403 (认证失败): 禁用 Key
   - 5xx (服务器错误): 记录失败，重试
   - 超时: 记录失败，重试
9. **配额消费**: 成功后更新 Client 和 Key 的配额
10. **日志记录**: 记录请求日志

#### Key 池管理 (key_pool.py)
- **选择策略**: 轮询 (Round-Robin)
- **过滤条件**:
  - is_active = True
  - status != 'disabled'
  - status != 'decrypt_failed'
  - 未超过配额
  - 未处于冷却期
- **冷却管理**: 429 响应后自动进入冷却期
- **故障检测**: 连续失败达到阈值后标记为 failed

#### 并发控制 (concurrency.py)
- **实现**: 内存 (ConcurrencyManager) / Redis (RedisConcurrencyManager)
- **机制**: 租约 (Lease) 模式
- **TTL**: 自动过期，防止死锁
- **粒度**: Client 级别 + Key 级别

#### 限流控制 (rate_limit.py)
- **算法**: 令牌桶 (Token Bucket)
- **实现**: 内存 (TokenBucketRateLimiter) / Redis (RedisTokenBucketRateLimiter)
- **粒度**: Client 级别 + Key 级别
- **配置**: rate_limit_per_min (每分钟请求数)

#### 幂等性保证 (idempotency.py)
- **机制**: Idempotency-Key 请求头
- **存储**: IdempotencyRecord 表
- **TTL**: 默认 24 小时
- **状态**: processing → completed/failed
- **响应缓存**: 缓存成功响应，直接返回

### 5. 配置管理 (config.yaml)

```yaml
server:
  host: 0.0.0.0
  port: 8000
  enable_docs: true
  enable_data_plane: true
  enable_control_plane: true

firecrawl:
  base_url: https://api.firecrawl.dev
  timeout  max_retries: 3
  failure_threshold: 3
  failure_window_seconds: 60
  failed_cooldown_seconds: 60

security:
  client_auth:
    enabled: true
  admin:
    token_env: FCAM_ADMIN_TOKEN
  key_encryption:
    master_key_env: FCAM_MASTER_KEY
  request_limits:
    max_body_bytes: 1048576
    allowed_paths: [scrape, crawl, search, agent, map, extract, batch, browser, team]

quota:
  timezone: UTC
  count_mode: success
  default_daily_limit: 5
  reset_time: "00:00"
  enable_quota_check: true

rate_limit:
  cooldown_seconds: 60

idempotency:
  enabled: true
  ttl_seconds: 86400
  max_response_bytes: 1048576

database:
  path: ./data/api_manager.db
  url: null  # 生产环境使用 PostgreSQL URL

logging:
  level: INFO
  format: json
  redact_fields: [authorization, api_key, token, cookie, set-cookie]

observability:
  metrics_enabled: false
  metrics_path: /metrics
  retention:
    request_logs_days: 30
    audit_logs_days: 90

state:
  mode: memory  # memory | redis
  redis:
    url: redis://localhost:6379/0
    key_prefix: fcam

control_plane:
  batch_key_test_max_workers: 10
```

### 6. 中间件栈

1. **RequestIdMiddleware**: 为每个请求生成唯一 request_id
2. **FcamErrorMiddleware**: 统一错误处理，转换为标准错误响应
3. **RequestLimitsMid**: 请求体大小限制，路径白名单检查

### 7. 安全机制

- **Key 加密**: Fernet 对称加密，Master Key 从环境变量读取
- **Token 哈希**: SHA-256 哈希存储，防止明文泄露
- **敏感字段脱敏**: 日志中自动脱敏 (authorization, api_key, token 等)
- **请求体限制**: 默认 1MB，防止 DoS 攻击
- **路径白名单**: 只允许转发特定路径，防止滥用

### 8. 可观测性

#### 日志
- **格式**: JSON 结构化日志
- **字段**: timestamp, level, message, request_id, fields
- **脱敏**: 自动脱敏敏感字段

#### 指标 (Prometheus)
- **请求指标**: 请求总数、成功率、响应时间
- **Key 指标**: Key 选择次数、冷却次数
- **配额指标**: 剩余配额

#### 审计日志
- **记录**: 所有管理操作 (创建、更新、删除)
- **字段**: actor_type, actor_id, actionsource_type, resource_id, ip, user_agent

### 9. 部署架构

#### 开发环境
- **数据库**: SQLite (单文件)
- **状态**: 内存 (单实例)
- **端口**: 8000 (数据面 + 控制面)

#### 生产环境
- **数据库**: PostgreSQL (支持并发写入)
- **状态**: Redis (分布式状态共享)
- **端口隔离**:
  - 8000: 数据面 (enable_data_plane=true, enable_control_plane=false)
  - 8001: 控制面 (enable_data_plane=false, enable_control_plane=true)
- **负载均衡**: Nginx/HAProxy
- **健康检查**: `/healthz`, `/readyz`

#### Docker 部署
- **开发**: `docker compose up` (SQLite + 内存)
- **生产**: `docker compose --profile prod up` (Postgres + Redis)
- **公开**: `docker compose --profile public up` (仅数据面)

### 10. 前端架构

#### 路由
- `/dashboard`: 仪表盘 (统计概览)
- `/clients`: Client 和 Key 管理
- `/logs`: 请求日志查询
- `/audit`: 审计日志查询

#### 状态管理
- **adminToken**: localStorage (持久化) / sessionStorage (仅本次)
- **API 调用**: 统一封装在 `api/` 目录

#### 组件
- **ConnectModal**: Admin Token 输入弹窗
- **StatCard**: 统计卡片
- **RequestTrendChart**: 请求趋势图

## 关键特性

### 1. 智能负载均衡
- 轮询选择可用 Key
- 自动跳过冷却中的 Key
- 自动跳过配额耗尽的 Key
- 自动跳过禁用的 Key

### 2. 故障转移
- 429 响应自动切换 Key
- 401/403 响应自动禁用 Key
- 5xx 响应自动重试
- 超时自动重试

### 3. 资源粘性
- 同一 Client 访问同一资源时使用同一 Key
- 适用于有状态的资源 (如 crawl jo理

### 4. 幂等性保证
- 支持 Idempotency-Key 请求头
- 自动缓存成功响应
- 防止重复扣费

### 5. 细粒度控制
- Client 级别配额、限流、并发
- Key 级别配额、限流、并发
- 灵活的配置策略

## 测试策略

### 单元测试
- `tests/unit/`: 核心逻辑单元测试
- 覆盖率要求: 80%

### 集成测试
- `tests/integration/`: API 集成测试
- 使用 TestClient (不发起真实 HTTP 请求)

### E2E 测试
- `tests/e2e/`: 端到端测试
- 使用真实 HTTP 请求
- 可选连接真实上游 API (需环境变量启用)

## 维护和运维

### 数据库迁移
```bash
alembic upgrade head  # 应用迁移
alembic revision --autogenerate -m "描述"  # 创建迁移
```

### 日志清理
```bash
python scripts/cleanup.py  # 根据保留策略清理过期日志
```

### 监控指标
- `/metrics`: Prometheus 指标端点
- `/healthz`: 健康检查
- `/readyz`: 就绪检查

### 故障排查
- 查看结构化日志 (JSON 格式)
- 管理操作记录)
- 查看请求日志 (业务请求记录)
- 查看 Prometheus 指标

## 扩展性

### 水平扩展
- 使用 Redis 共享状态
- 使用 PostgreSQL 支持并发写入
- 使用负载均衡器分发请求
- 无状态设计，支持多实例部署

### 垂直扩展
- 调整数据库连接池大小
- 调整并发控制参数
- 调整限流参数

## 最后更新

- **日期**: 2026-02-27
- **版本**: 基于当前代码库实际架构
- **主要更新**: 添加信用额度监控系统、完善测试体系、优化文档结构
