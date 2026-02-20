# ClawCloud Run（免费容器）部署指引

本文面向 **ClawCloud Run / AppLaunchpad** 这种“直接跑容器镜像”的场景，重点覆盖：
- 推荐的持久化数据库配置（Postgres）
- SQLite 在部分存储类上的启动失败现象（`sqlite3.OperationalError: unable to open database file`）

---

## 1. 最小可用配置（推荐：Postgres）

### 1.1 镜像

- 推荐固定版本 tag（避免 `latest` 漂移）：
  - `guangshanshui/firecrawl-manager:v0.1.7-onebox`（推荐：onebox 单端口，UI+API 同端口）
  - （或）`guangshanshui/firecrawl-manager:v0.1.7`

> 约定：`guangshanshui/firecrawl-manager:latest` 永远等价于 onebox（单容器单端口）；但线上仍建议固定版本。

### 1.2 环境变量

至少设置：
- `FCAM_ADMIN_TOKEN`：控制面 `/admin/*` 的访问令牌
- `FCAM_MASTER_KEY`：用于加密/解密存储的密钥（必须稳定，变更会导致历史数据无法解密）

数据库（推荐 Postgres，避免 SQLite 在 PVC 上不稳定）：
- 推荐只设置 **一个** DSN（系统会自动做 alias，保证迁移期/运行期一致）：
  - `FCAM_DATABASE__URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/DB`
  - （兼容）`FCAM_DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/DB`

> 兼容说明：你也可以双写 `FCAM_DATABASE_URL` 与 `FCAM_DATABASE__URL`，但两者必须完全一致；不一致会 fail-fast 以避免“迁移到 A、运行期连 B”的隐性错配。

可选（功能开关）：
- `FCAM_SERVER__ENABLE_DOCS=false`：关闭 OpenAPI 文档
- `FCAM_CONFIG=/app/config.yaml`：指定配置文件路径（若你用 ConfigMap 覆盖）

> 注意：配置项通过环境变量覆盖时，嵌套字段使用双下划线，例如 `FCAM_SERVER__ENABLE_DOCS`（不是 `FCAM_SERVER_ENABLE_DOCS`）。

### 1.3 ConfigMap（可选）

如果你用 ConfigMap 生成 `/app/config.yaml`：
- 推荐直接写 `database.url`（Postgres）
- 如仍要用 SQLite，建议写绝对 `database.path`（但不推荐在 ClawCloud PVC 上使用 SQLite）

```yaml
database:
  url: "postgresql+psycopg://USER:PASSWORD@HOST:5432/DB"
```

### 1.4 Storage（必须）

- 对于 **firecrawl-manager**：Postgres 模式下不依赖 `/app/data`，一般不需要挂载该目录。
- 对于 **Postgres 容器**：需要给数据库数据目录挂载存储（例如 `/var/lib/postgresql/data`）。

---

## 2. 常见故障：SQLite unable to open database file（现象）

日志特征：
- `sqlite3.OperationalError: unable to open database file`
- 通常发生在镜像启动阶段执行 `alembic upgrade head` 时

在 ClawCloud 上的建议处置：

1) **（推荐）切换到 Postgres**
- 设置 `FCAM_DATABASE__URL`（或 `FCAM_DATABASE_URL`）为 Postgres 连接串（见上文）。

2) **临时兜底（不持久化）**
- 如只为先把服务跑起来：把 SQLite 放到 `/tmp`（容器本地可写层）
  - `FCAM_DATABASE_URL=sqlite:////tmp/api_manager.db`
  - `FCAM_DATABASE__PATH=/tmp/api_manager.db`

---

## 4. onebox（单端口）说明：/ui2 与 /api 是否在同一端口？

- **onebox 模式**：UI（`/ui2/`）与 API（`/api/*`）在同一端口（`8000`），最适合容器云减少端口/路由配置错误。
- 如你需要把控制面与数据面拆到不同端口/不同实例，请参考 `docs/docker.md` 的生产示例（`prod` profile）。

---

## 3. 探活建议（避免被平台误杀）

- **Liveness**：`GET /healthz`（只表示进程存活）
- **Readiness**：`GET /readyz`（依赖密钥/DB/Redis，表示可接业务）
