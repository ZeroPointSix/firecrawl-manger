# Firecrawl API 密钥管理与转发服务 - MVP 产品需求文档

## 1. 产品概述

### 1.1 产品定位
一个可部署在服务器（Docker）的轻量级 HTTP 网关服务，用于集中管理多个 Firecrawl API 密钥，向多业务服务提供**统一鉴权、智能选 Key、请求转发、限流配额与审计日志**能力。

> 说明：该服务在服务器部署场景下，本质上是“付费能力代理/内部网关”。除非有明确需求与完善防护，不建议直接公网暴露。

### 1.2 核心价值
- **密钥池管理**：统一管理多个 Firecrawl API 密钥，避免单个密钥速率限制
- **透明转发**：业务服务无需关心密钥管理，只需调用网关服务
- **智能轮询**：自动选择可用密钥，处理速率限制（429 错误）
- **可观测与审计**：记录请求日志与管理操作审计，便于调试、统计与追责
- **统一安全边界**：密钥加密存储、调用方鉴权、限流与配额控制集中落地

### 1.3 技术架构
```
（数据面：高频转发）
┌─────────────────────────────────────┐
│  业务服务（多个）                    │
│  - Service A / B / C ...             │
│  - 统一调用 /api/*                   │
└──────────────┬──────────────────────┘
               │  (mTLS 或 Bearer Token)
┌──────────────▼──────────────────────┐
│  Firecrawl API Manager (Gateway)     │
│  - 调用方鉴权/限流/配额（Client 维度）│
│  - Key Pool 轮询/冷却/并发/配额（Key 维度）│
│  - 幂等键与可重试策略                 │
│  - 请求日志 / 指标                    │
│  - DB: SQLite(MVP)/Postgres(生产)    │
└──────────────┬──────────────────────┘
               │
┌──────────────▼──────────────────────┐
│  Firecrawl 官方 API                   │
│  - /scrape /crawl /search /agent     │
└─────────────────────────────────────┘

（控制面：低频管理，建议独立入口/端口）
┌─────────────────────────────────────┐
│  Admin UI / 运维脚本（可选）          │
└──────────────┬──────────────────────┘
               │  (Admin Token + IP Allowlist)
┌──────────────▼──────────────────────┐
│  /admin/* 管理接口（密钥/调用方/审计）│
└─────────────────────────────────────┘
```

---

## 2. 功能需求

### 2.1 核心功能（MVP 必须）

#### 2.1.1 API 密钥管理
- **添加密钥**：支持手动添加新的 Firecrawl API 密钥
  - 设置套餐类型（Free/Hobby/Standard/Growth）
  - 设置每日配额限制（如免费版每天 5 次）
- **查看密钥列表**：显示所有密钥及其状态（可用/不可用/速率限制中/配额已用完）
- **删除密钥**：移除无效或不需要的密钥
- **密钥安全**：
  - 密钥**加密后**存储到数据库（不落明文）
  - 列表与日志仅展示脱敏信息（如 `fc-****5678`）
  - 支持禁用/启用与轮换（Rotation）流程
- **密钥状态追踪**：
  - 套餐类型和限制（并发数、速率限制）
  - 今日已使用次数 / 每日配额
  - 累计请求次数
  - 最后使用时间
  - 当前速率限制状态
  - 速率限制重置时间
  - 配额重置时间（每日 00:00 重置）

#### 2.1.2 请求转发
支持 Firecrawl 的主要端点：
- `POST /api/scrape` → 转发到 Firecrawl `/scrape`
- `POST /api/crawl` → 转发到 Firecrawl `/crawl`
- `GET /api/crawl/{id}` → 转发到 Firecrawl `/crawl/{id}`
- `POST /api/search` → 转发到 Firecrawl `/search`
- `POST /api/agent` → 转发到 Firecrawl `/agent`

**转发逻辑**：
0. 校验调用方身份（数据面鉴权），并执行限流/配额检查（Client 维度）
1. 接收客户端请求（调用方不需要提供 Firecrawl API 密钥）
2. 从密钥池中选择可用密钥
3. 添加 `Authorization: Bearer {firecrawl_api_key}` 头（覆盖/忽略客户端同名头）
4. 转发请求到 Firecrawl 官方 API
5. 返回原始响应给客户端（必要时脱敏响应中的敏感字段）

> 可选增强：提供“路径兼容模式”，支持将 Firecrawl 原始路径（如 `/v1/*`）原样转发，仅需调用方替换 `base_url`。

#### 2.1.3 智能轮询策略
- **轮询算法**：Round-robin（轮流使用）+ 配额感知
  - 优先选择配额未用完的密钥
  - 在可用密钥中轮流使用
  - 跳过已达配额限制的密钥
- **配额管理**：
  - 每个密钥设置每日最大使用次数（如免费版 5 次/天）
  - 实时追踪今日使用次数
  - 达到配额后自动标记为"配额已用完"
  - 每日 00:00 自动重置配额计数
- **速率限制处理**：
  - 检测 429 错误（超过速率限制）
  - 自动标记该密钥为"冷却中"
  - 切换到下一个可用密钥重试
  - 如果响应包含 `Retry-After`，优先遵守；否则使用默认冷却时间（如 60 秒）
- **并发控制**：
  - 追踪每个密钥的当前并发请求数
  - 避免超过套餐的并发浏览器限制
  - 超过限制时排队或切换密钥
- **失败重试**：
  - 最多重试 3 次（使用不同密钥）
  - 仅对明确可重试场景重试（429、部分 5xx、网络超时等）；对 4xx 参数错误不重试
  - 对可能“创建任务/扣费”的接口（如 `crawl/agent`）必须配合幂等键，避免重复创建

#### 2.1.4 日志记录
记录以下信息到数据库（MVP 可用 SQLite，生产推荐 Postgres）：
- 请求时间
- 使用的 API 密钥（脱敏显示，如 `fc-****5678`）
- 调用方标识（client/service）
- 请求端点
- 请求参数（可选，用于调试）
- 响应状态码
- 响应时间（毫秒）
- 是否成功
- 幂等键（如有）
- 请求唯一 ID（request_id，用于链路追踪）

> 建议：增加日志保留策略（按天/按量清理），避免数据库无限增长。

#### 2.1.5 认证与授权（服务器部署必需）
将接口分为“数据面”和“控制面”，并采用不同强度的访问控制：

- **数据面（/api/\*)**：供业务服务调用
  - **必须鉴权**：MVP 使用 Bearer Token（每个服务一个 token），生产建议 mTLS（或 mTLS + token）
  - 支持按调用方启用/禁用、token 轮换与过期策略
  - 从请求中提取 `client_id`，用于限流、配额、日志归因与审计
- **控制面（/admin/\*)**：供运维/管理员使用
  - **必须鉴权**：独立 Admin Token（不与数据面共用）
  - 建议增加 IP allowlist / VPN / 零信任接入
  - 所有管理操作写入**审计日志**（谁在何时对什么资源做了什么）

#### 2.1.6 调用方（Client）配额与限流
为避免单个服务占用全部密钥池资源，网关需提供 Client 维度的治理能力：
- **限流**：按 client 设置每分钟请求上限（如 60 rpm），超限返回 429
- **配额**：按 client 设置每日预算/次数上限（可选），用尽后拒绝或降级
- **并发**：按 client 设置最大并发（避免单服务挤爆网关与下游）
- **统计**：提供 client 维度的成功率/耗时/用量统计

#### 2.1.7 幂等与重试规则（避免重复创建/重复扣费）
- 客户端可（或必须）传入 `X-Idempotency-Key`：
  - 对 `POST /api/crawl`、`POST /api/agent` 等可能创建任务的接口：**建议强制**
  - 网关记录 `client_id + idempotency_key` 的处理结果，重复请求直接返回已记录的结果
  - 幂等记录设置 TTL（如 24h），避免无限增长
- 重试策略：
  - 仅对 429/网络超时/部分 5xx 做重试；对 4xx 不重试
  - 对带幂等键的请求可安全重试；无幂等键时避免对“创建任务”接口自动重试

#### 2.1.8 安全网关约束（MVP 建议）
- 仅允许转发到 `firecrawl.base_url` 且路径在白名单内（scrape/crawl/search/agent）
- 严格限制请求体大小、超时与内容类型，避免被用作通用代理或 DoS 放大器
- 丢弃/覆盖敏感或不可信头（如客户端传入的 `Authorization`、`Host` 等）

### 2.2 管理界面（MVP 可选）

#### 2.2.1 密钥管理页面
- 表格展示所有密钥，包含以下列：
  - 密钥名称/别名
  - 套餐类型（Free/Hobby/Standard/Growth）
  - 今日使用情况（如 3/5）
  - 当前并发数（如 1/2）
  - 状态指示器
    - 🟢 绿色 = 可用
    - 🟡 黄色 = 冷却中
    - 🔴 红色 = 失败
    - ⚫ 灰色 = 配额已用完
  - 最后使用时间
  - 操作按钮（编辑/删除/测试）
- 添加密钥表单：
  - API 密钥输入
  - 套餐类型选择
  - 每日配额设置（默认 5 次）
  - 别名（可选）
  - 说明：提交后仅保存加密密钥，页面不再展示明文
- 批量操作：
  - 批量导入密钥（CSV/JSON）
  - 批量重置配额
  - 批量测试健康状态

#### 2.2.2 日志查看页面
- 最近 100 条请求日志
- 筛选功能（按时间、状态码、密钥）
- 导出日志（CSV 格式）

#### 2.2.3 统计页面
- **总体统计**：
  - 总请求数
  - 成功率
  - 平均响应时间
  - 今日总使用次数
- **密钥统计**：
  - 各密钥使用次数（今日/累计）
  - 各密钥配额使用率（如 60% = 3/5）
  - 各密钥成功率
  - 配额利用率排行
- **趋势图表**：
  - 每日请求量趋势
  - 配额使用趋势
  - 错误率趋势
- **配额预警**：
  - 显示即将用完配额的密钥（如已用 4/5）
  - 显示今日配额已用完的密钥数量

---

## 3. 技术实现

### 3.1 技术栈
- **后端框架**：FastAPI（Python 3.9+）
- **数据库**：
  - MVP：SQLite（单副本、挂载 volume）
  - 生产推荐：PostgreSQL（更好的并发写与一致性）
- **HTTP 客户端**：httpx（支持异步）
- **鉴权**：
  - MVP：Bearer Token（每个 client 一个 token，数据库仅存 token_hash）
  - 生产推荐：mTLS（或 mTLS + token）
- **前端**（可选）：
  - 简单方案：FastAPI 自带的 Swagger UI
  - 完整方案：HTML + Vanilla JavaScript + Tailwind CSS

### 3.2 数据模型

#### API 密钥表 (api_keys)
```sql
CREATE TABLE api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- 机密：密钥加密存储（不落明文）
    api_key_ciphertext BLOB NOT NULL,
    api_key_hash TEXT UNIQUE NOT NULL,  -- 用于去重/查找（如 SHA-256）
    api_key_last4 TEXT NOT NULL,        -- 用于展示脱敏信息（如 5678）
    name TEXT,  -- 密钥别名（可选）
    plan_type TEXT DEFAULT 'free',  -- free, hobby, standard, growth
    is_active BOOLEAN DEFAULT 1,
    
    -- 配额管理
    daily_quota INTEGER DEFAULT 5,  -- 每日配额（免费版默认 5 次）
    daily_usage INTEGER DEFAULT 0,  -- 今日已使用次数
    quota_reset_at DATE,  -- 配额重置日期
    
    -- 并发控制
    max_concurrent INTEGER DEFAULT 2,  -- 最大并发数（根据套餐）
    current_concurrent INTEGER DEFAULT 0,  -- 当前并发数
    
    -- 速率限制
    rate_limit_per_min INTEGER DEFAULT 10,  -- 每分钟请求限制
    rate_limit_reset_at TIMESTAMP,  -- 速率限制重置时间
    cooldown_until TIMESTAMP,  -- 冷却到期时间（429 后）
    
    -- 统计信息
    total_requests INTEGER DEFAULT 0,
    last_used_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- 状态
    status TEXT DEFAULT 'active'  -- active, cooling, quota_exceeded, failed, disabled
);
```

#### 调用方表 (clients)
```sql
CREATE TABLE clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,  -- service 名称
    token_hash TEXT UNIQUE NOT NULL,  -- 不存明文 token
    is_active BOOLEAN DEFAULT 1,

    -- Client 维度治理：限流/配额/并发
    daily_quota INTEGER,  -- 可选：每日预算/次数上限
    daily_usage INTEGER DEFAULT 0,
    quota_reset_at DATE,
    rate_limit_per_min INTEGER DEFAULT 60,
    max_concurrent INTEGER DEFAULT 10,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used_at TIMESTAMP
);
```

#### 请求日志表 (request_logs)
```sql
CREATE TABLE request_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,  -- 用于链路追踪
    client_id INTEGER,
    api_key_id INTEGER,
    endpoint TEXT NOT NULL,
    method TEXT NOT NULL,
    status_code INTEGER,
    response_time_ms INTEGER,
    success BOOLEAN,
    error_message TEXT,
    idempotency_key TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (client_id) REFERENCES clients(id),
    FOREIGN KEY (api_key_id) REFERENCES api_keys(id)
);
```

#### 幂等记录表 (idempotency_records)
```sql
CREATE TABLE idempotency_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,  -- 防止同 key 不同请求体
    status TEXT NOT NULL,  -- in_progress, completed, failed
    response_status_code INTEGER,
    response_body TEXT,  -- 可按需裁剪/仅存任务 id
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,
    UNIQUE(client_id, idempotency_key),
    FOREIGN KEY (client_id) REFERENCES clients(id)
);
```

#### 审计日志表 (audit_logs)
```sql
CREATE TABLE audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_type TEXT NOT NULL,  -- admin, system
    actor_id TEXT,
    action TEXT NOT NULL,
    resource_type TEXT,
    resource_id TEXT,
    ip TEXT,
    user_agent TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 3.3 核心 API 端点

#### 管理端点
```
GET  /admin/keys              # 获取所有密钥
POST /admin/keys              # 添加新密钥
PUT  /admin/keys/{id}         # 更新密钥配置（配额、套餐等）
DELETE /admin/keys/{id}       # 删除密钥
POST /admin/keys/{id}/test    # 测试密钥健康状态
POST /admin/keys/reset-quota  # 手动重置所有密钥配额

GET  /admin/clients                 # 获取所有调用方
POST /admin/clients                 # 创建调用方（生成 token，仅返回一次）
PUT  /admin/clients/{id}            # 更新调用方限额/状态
DELETE /admin/clients/{id}          # 删除/禁用调用方
POST /admin/clients/{id}/rotate     # 轮换调用方 token（仅返回一次）

GET  /admin/logs              # 获取请求日志
GET  /admin/audit-logs        # 获取审计日志
GET  /admin/stats             # 获取统计信息
GET  /admin/stats/quota       # 获取配额使用统计
```

#### 转发端点（兼容 Firecrawl API）
```
POST /api/scrape              # 抓取单页
POST /api/crawl               # 爬取网站
GET  /api/crawl/{id}          # 查询爬取状态
POST /api/search              # 搜索
POST /api/agent               # AI 代理

GET  /healthz                 # 健康检查（容器探活）
GET  /readyz                  # 就绪检查（依赖可用）
```

### 3.4 配置文件
```yaml
# config.yaml
server:
  host: "0.0.0.0"  # 容器内监听地址；对外暴露由反向代理/防火墙控制
  port: 8000
  # 可选：将控制面独立端口/入口（更安全）
  # admin_port: 8001
  
firecrawl:
  base_url: "https://api.firecrawl.dev/v1"
  timeout: 30  # 秒
  max_retries: 3
  
security:
  # 数据面鉴权：服务器部署必开
  client_auth:
    enabled: true
    scheme: "bearer"  # bearer | mtls
  # 控制面鉴权：与数据面隔离
  admin:
    token_env: "FCAM_ADMIN_TOKEN"
    # 建议：仅允许内网/堡垒机访问
    # ip_allowlist: ["10.0.0.0/8", "192.168.0.0/16"]
  # Firecrawl API Key 加密主密钥（32 bytes，建议通过 Docker secret 注入）
  key_encryption:
    master_key_env: "FCAM_MASTER_KEY"
  request_limits:
    max_body_bytes: 1048576  # 1MB
    allowed_paths: ["scrape", "crawl", "search", "agent"]

quota:
  default_daily_limit: 5  # 免费版默认每日 5 次
  reset_time: "00:00"  # 每日重置时间
  enable_quota_check: true  # 是否启用配额检查
  
rate_limit:
  cooldown_seconds: 60  # 429 错误后的冷却时间
  
concurrent:
  # 不同套餐的并发限制
  free: 2
  hobby: 5
  standard: 50
  growth: 100
  
database:
  path: "./data/api_manager.db"
  
logging:
  level: "INFO"
  file: "./logs/app.log"
  redact_fields: ["api_key", "authorization"]
```

---

## 4. 使用场景

### 4.1 场景一：爬虫脚本调用
```python
# 原来的代码（需要管理密钥）
from firecrawl import FirecrawlApp
app = FirecrawlApp(api_key="fc-YOUR-API-KEY")
result = app.scrape_url("https://example.com")

# 使用本服务后（无需管理密钥）
import requests
response = requests.post(
    "http://localhost:8000/api/scrape",
    headers={"Authorization": "Bearer <CLIENT_TOKEN>"},
    json={"url": "https://example.com"}
)
result = response.json()
```

### 4.2 场景二：批量爬取（配额感知）
```python
# 本服务会自动轮询多个密钥，避免速率限制和配额限制
urls = ["https://site1.com", "https://site2.com", ...]
for url in urls:
    response = requests.post(
        "http://localhost:8000/api/scrape",
        headers={"Authorization": "Bearer <CLIENT_TOKEN>"},
        json={"url": url}
    )
    # 服务内部自动处理：
    # 1. 选择配额未用完的密钥
    # 2. 处理 429 错误和密钥切换
    # 3. 更新配额计数
    # 4. 达到配额后自动切换到下一个密钥
```

### 4.3 场景三：多密钥配额管理
```python
# 假设你有 10 个免费密钥，每个每天 5 次
# 总共每天可以使用 50 次

# 查看当前配额使用情况
response = requests.get(
    "http://localhost:8000/admin/stats/quota",
    headers={"Authorization": "Bearer <ADMIN_TOKEN>"}
)
print(response.json())
# {
#   "total_quota": 50,
#   "used_today": 23,
#   "remaining": 27,
#   "keys_exhausted": 4,  # 4 个密钥已用完
#   "keys_available": 6   # 6 个密钥还有配额
# }
```

---

## 5. MVP 开发计划

### 阶段 1：核心后端（2-3 天）
- [ ] FastAPI 项目搭建
- [ ] 数据库初始化（SQLite MVP / Postgres 生产）
- [ ] API 密钥 CRUD 接口（加密存储）
- [ ] 调用方（Client）管理与鉴权（token 生成/校验/轮换）
- [ ] 控制面（/admin/*）鉴权 + 审计日志
- [ ] 配额管理系统
  - [ ] 每日配额追踪
  - [ ] 自动重置（定时任务或惰性重置）
  - [ ] 配额检查逻辑
- [ ] Client 维度限流/配额/并发控制
- [ ] 请求转发逻辑
- [ ] 轮询算法实现（配额感知）
- [ ] 速率限制处理
- [ ] 并发控制
- [ ] 幂等键支持（crawl/agent）

### 阶段 2：日志与监控（1 天）
- [ ] 请求日志记录
- [ ] 统计接口实现
- [ ] 日志查询接口

### 阶段 3：前端界面（可选，1-2 天）
- [ ] 密钥管理页面
  - [ ] 密钥列表（含配额显示）
  - [ ] 添加/编辑密钥表单
  - [ ] 配额进度条
- [ ] 日志查看页面
- [ ] 统计仪表板
  - [ ] 配额使用统计
  - [ ] 趋势图表
  - [ ] 配额预警

### 阶段 4：测试与优化（1 天）
- [ ] 单元测试
- [ ] 集成测试
- [ ] 性能优化
- [ ] 文档编写

---

## 6. 非功能需求

### 6.1 性能
- 请求转发延迟 < 100ms（不含 Firecrawl API 响应时间）
- 支持并发请求（至少 50 个并发）

### 6.2 安全
- **数据面必须鉴权**：所有 `/api/*` 请求必须携带合法身份（Bearer Token 或 mTLS）
- **控制面强隔离**：`/admin/*` 使用独立 Admin Token，并建议 IP allowlist/VPN/堡垒机访问
- **密钥加密存储（必须）**：Firecrawl API Key 加密落库；密钥主密钥通过 Docker secret/环境变量注入并可轮换
- **最小暴露面**：仅允许转发到 Firecrawl 预设 base_url + 白名单路径；限制请求体大小与超时
- **日志脱敏**：禁止在日志/响应中输出明文密钥与 token；对敏感字段统一打码
- **限流与配额**：按 client 限流/配额，避免被刷爆额度；异常流量可快速封禁 client
- **审计可追溯**：管理操作记录审计日志（谁、何时、对什么做了什么）

### 6.3 可维护性
- 清晰的代码结构
- 完整的错误处理
- 详细的日志记录

---

## 7. 未来扩展（非 MVP）

### 7.1 高级功能
- **智能密钥选择**：根据历史成功率和响应时间选择最优密钥
- **成本追踪**：记录每个密钥的 credit 消耗（如果 API 提供）
- **告警通知**：密钥失效、配额即将用完或速率限制时发送通知
- **缓存机制**：对相同 URL 的请求进行缓存（可配置 TTL）
- **配额预测**：基于历史使用量预测配额耗尽时间
- **动态配额调整**：根据实际需求自动调整每个密钥的配额分配

### 7.2 部署方案
- Docker 容器化
- 支持远程部署（添加认证机制）
- 多实例负载均衡
- ClawCloud Run / AppLaunchpad：数据库方案必须采用 Postgres（SQLite 在该平台存在启动迁移失败现象）。详见：`docs/PRD/2026-02-20-clawcloud-sqlite-crashloop-and-postgres-migration.md`

### 7.3 集成功能
- 支持其他爬虫 API（ScrapingBee、Apify 等）
- Webhook 支持
- 定时任务调度

---

## 8. 风险与限制

### 8.1 技术风险
- **Firecrawl API 变更**：官方 API 可能更新，需要及时适配
- **速率限制不可预测**：不同套餐的速率限制不同

### 8.2 使用限制
- 本服务不改变 Firecrawl 的速率限制总量，只是分散到多个密钥
- 需要用户自行获取多个 API 密钥
- 推荐仅在内网/私有网络使用；如需公网暴露，必须启用强认证（mTLS/OIDC）、WAF/网关限流与更严格的审计与监控

---

## 9. 成功指标

### MVP 验收标准
- ✅ 能够添加/删除/查看 API 密钥
- ✅ 能够设置每个密钥的每日配额限制
- ✅ `/api/*` 必须鉴权（按 client 隔离）
- ✅ `/admin/*` 必须鉴权（与数据面隔离）并具备审计日志
- ✅ Firecrawl API Key 加密落库，日志/接口不泄露明文
- ✅ 能够成功转发请求到 Firecrawl
- ✅ 遇到 429 错误时自动切换密钥
- ✅ 达到配额限制时自动切换到下一个密钥
- ✅ 每日自动重置配额计数
- ✅ 记录所有请求日志
- ✅ 提供配额使用统计信息

### 用户体验目标
- 爬虫脚本无需修改密钥管理逻辑
- 速率限制问题显著减少
- 配额自动均衡使用，避免浪费
- 请求成功率 > 95%
- 每个免费密钥每天稳定使用 5 次，不多不少

---

## 10. 参考资料

- [Firecrawl 官方文档](https://docs.firecrawl.dev/)
- [Firecrawl API 参考](https://docs.firecrawl.dev/api-reference/introduction)
- [Firecrawl 速率限制说明](https://docs.firecrawl.dev/rate-limits)

---

**文档版本**：v1.1  
**创建日期**：2026-02-10  
**最后更新**：2026-02-10
