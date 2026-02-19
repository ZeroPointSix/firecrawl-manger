# ClawCloud Run（免费容器）部署指引

本文面向 **ClawCloud Run / AppLaunchpad** 这种“直接跑容器镜像”的场景，重点解决：
- 如何挂载 SQLite 数据目录（持久化）
- 常见的 `sqlite3.OperationalError: unable to open database file`（卷权限/运行用户问题）

---

## 1. 最小可用配置（推荐）

### 1.1 镜像

- `guangshanshui/firecrawl-manager:latest`（或固定版本 tag）

### 1.2 环境变量

至少设置：
- `FCAM_ADMIN_TOKEN`：控制面 `/admin/*` 的访问令牌
- `FCAM_MASTER_KEY`：用于加密/解密存储的密钥（必须稳定，变更会导致历史数据无法解密）

可选：
- `FCAM_SERVER__ENABLE_DOCS=false`：关闭 OpenAPI 文档
- `FCAM_CONFIG=/app/config.yaml`：指定配置文件路径（若你用 ConfigMap 覆盖）
- `FCAM_DATABASE__PATH=/app/data/api_manager.db`：显式指定 SQLite 文件路径（建议）

> 注意：配置项通过环境变量覆盖时，嵌套字段使用双下划线，例如 `FCAM_SERVER__ENABLE_DOCS`（不是 `FCAM_SERVER_ENABLE_DOCS`）。

### 1.3 ConfigMap（可选）

如果你用 ConfigMap 生成 `/app/config.yaml`，确保 `database.path` 指向 `/app/data/...`：

```yaml
database:
  path: "/app/data/api_manager.db"
```

### 1.4 Storage（必须）

- **挂载点**：`/app/data`
- **容量**：按需（例如 1Gi）
- **只读**：关闭（必须可写）

---

## 2. 常见故障：SQLite unable to open database file

日志特征：
- `sqlite3.OperationalError: unable to open database file`
- 通常发生在镜像启动阶段执行 `alembic upgrade head` 时

根因：
- 平台挂载的持久化卷往往是 `root:root`，而容器进程以非 root 用户运行时对 `/app/data` 没有写权限。

解决方案（按优先级）：

1) **（推荐）设置安全上下文（SecurityContext）**
- `runAsUser: 10001`
- `fsGroup: 10001`

2) **临时验证：以 root 身份运行容器**
- 如果平台支持 “Run as root / runAsUser=0”，可先用它验证是否为权限问题。

3) **改用外部数据库**
- 配置 `FCAM_DATABASE__URL=postgresql+psycopg://...`，避免依赖本地卷写权限。

4) **临时兜底（不持久化）**
- 如果平台卷始终不可写且你只想先把服务跑起来：镜像会在检测到 `/app/data` 不可写且你未显式配置数据库时，自动降级到 `/tmp/api_manager.db`。
- 可通过 `FCAM_DB_FALLBACK_TMP=0` 禁用该行为（禁用后会直接启动失败并提示修复方式）。

---

## 3. 探活建议（避免被平台误杀）

- **Liveness**：`GET /healthz`（只表示进程存活）
- **Readiness**：`GET /readyz`（依赖密钥/DB/Redis，表示可接业务）
