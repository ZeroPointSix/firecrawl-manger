# 技术栈详解

## 后端技术栈

### 核心框架
- **FastAPI 0.115+**: 现代化的 Python Web 框架
  - 自动 API 文档生成 (OpenAPI/Swagger)
  - 类型提示和自动验证
  - 异步支持
  - 依赖注入系统

- **Uvicorn**: ASGI 服务器
  - 高性能异步服务器
  - 支持 HTTP/1.1 和 WebSocket

### 数据库
- **SQLAlchemy 2.x**: ORM 框架
  - 声明式模型定义
  - 类型安全的查询
  - 关系映射
  - 连接池管理

- **Alembic**: 数据库迁移工具
  - 版本化的 schema 变更
  - 自动生成迁移脚本
  - 支持回滚

- **SQLite**: 开发环境数据库
  - 零配置
  - 单文件存储
  - 适合开发和测试

- **PostgreSQL**: 生产环境数据库
  - 支持并发写入
  - ACID 事务
  - 丰富的数据类型

### HTTP 客户端
- **httpx**: 现代化的 HTTP 客户端
  - 同步和异步支持
  - HTTP/2 支持
  - 连接池
  - 超时控制
  - 自动重试

### 加密
- **cryptography**: 加密库
  - Fernet 对称加密 (用于 API Key 加密)
  - SHA-256 哈希 (用于 Token 存储)

### 状态管理
- **Redis**: 分布式状态存储
  - 限流状态 (令牌桶)
  - 并发控制 (租约)
  - 冷却状态
  - 支持多实例部署

### 配置管理
- **PyYAML**: YAML 解析
- **Pydantic**: 配置验证
  - 类型验证
  - 默认值
  - 环境变量覆盖

### 日志和监控
- **Python logging**: 标准日志库
  - JSON 格式化
  - 结构化日志
  - 敏感字段脱敏

- **Prometheus**: 指标收集
  - 请求计数
  - 响应时间
  - 配额剩余

### 开发工具
- **pytest**: 测试框架
  - 单元测试
  - 集成测试
  - E2E 测试
  - 覆盖率报告

- **Ruff**: 代码检查和格式化
  - 快速的 Python linter
  - 自动修复

## 前端技术栈

### 核心框架
- **Vue 3**: 渐进式 JavaScript 框架
  - Composition API
  - 响应式系统
  - 组件化开发

- **TypeScript**: 类型安全的 JavaScript
  - 静态类型检查
  - IDE 智能提示
  - 重构支持

### 构建工具
- **Vite**: 下一代前端构建工具
  - 快速的冷启动
  - 即时的模块热更新
  - 优化的生产构建

### UI 库
- **Naive UI**: Vue 3 组件库
  - 丰富的组件
  - TypeScript 支持
  - 主题定制
  - 响应式设计

### 路由
- **Vue Router 4**: 官方路由库
  - Hash 模式 (适合静态部署)
  - 路由守卫
  - 懒加载

### HTTP 客户端
- **原生 fetch API**: 浏览器内置
  - 统一封装在 `api/` 目录
  - 自动添加 Authorization 头
  - 错误处理

### 状态管理
- **localStorage**: 持久化存储
  - Admin Token 存储
- **sessionStorage**: 会话存储
  - 临时 Token 存储

## 容器化

### Docker
- **多阶段构建**: 优化镜像大小
- **非 root 用户**: 安全运行 (uid 10001)
- **健康检查**: 容器健康监控

### docker-compose
- **开发环境**: SQLite + 内存状态
- **生产环境**: PostgreSQL + Redis
- **端口隔离**: 数据面和控制面分离

## 开发环境

### Python 环境
- **Python 3.11+**: 最新稳定版本
- **venv**: 虚拟环境隔离
- **pip**: 包管理器

### Node.js 环境
- **Node.js 18+**: LTS 版本
- **npm**: 包管理器

### 代码质量
- **Ruff**: Python 代码检查
- **pytest**: 测试覆盖率 80%+
- **TypeScript**: 严格模式

## 生产环境

### 反向代理
- **Nginx**: 推荐
  - 负载均衡
  - SSL 终止
  - 静态文件服务
  - 请求限流

- **HAProxy**: 备选
  - 高性能负载均衡
  - 健康检查
  - 会话保持

### 监控
- **Prometheus**: 指标收集
- **Grafana**: 可视化仪表盘
- **Loki**: 日志聚合 (可选)

### 日志
- **JSON 格式**: 结构化日志
- **ELK Stack**: 日志分析 (可选)
  - Elasticsearch: 存储和搜索
  - Logstash: 日志处理
  - Kibana: 可视化

## 依赖版本

### 后端核心依赖
```
fastapi>=0.115.0
uvicorn[standard]>=0.32.0
sqlalchemy>=2.0.0
alembic>=1.13.0
httpx>=0.27.0
cryptography>=43.0.0
pydantic>=2.9.0
pyyaml>=6.0.0
redis>=5.0.0
```

### 前端核心依赖
```
vue@^3.4.0
vue-router@^4.3.0
naive-ui@^2.38.0
typescript@^5.4.0
vite@^5.2.0
```

## 最后更新

- **日期**: 2026-02-27
- **版本**: 基于当前项目实际使用的技术栈
