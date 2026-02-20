# ClawCloud onebox（单容器单端口）部署经验总结（2026-02-20）

本文沉淀一次 ClawCloud Run 部署排障经验：为什么 “onebox（单容器、单端口）” 能稳定部署，SQLite/Postgres 分别如何配置，以及 `upstream connect error ... 111` 的快速定位方法。

> 适用镜像：`guangshanshui/firecrawl-manager:v0.1.7-onebox`（也可用 `:onebox` 作为滚动 tag，但生产不建议）  
> 本文不替代 PRD/FD/TDD：数据库迁移与直迁工具以 `docs/TODO/2026-02-20-clawcloud-postgres-migration.md` 对应文档为准。

---

## 1. 核心结论

### 1.1 数据库“镜像里用什么”？

`firecrawl-manager` 镜像**不内置 Postgres**。运行时数据库完全由配置决定：

- **默认：SQLite**
  - 默认配置：`database.path=./data/api_manager.db`
  - 容器内路径：`/app/data/api_manager.db`
- **显式配置 DSN：Postgres**
  - 只需设置其一（推荐只设一个）：
    - `FCAM_DATABASE__URL=postgresql+psycopg://...`（推荐）
    - `FCAM_DATABASE_URL=postgresql+psycopg://...`（兼容）

镜像启动日志会打印一行可直接确认当前后端（DSN 会脱敏）：
- `db.backend=sqlite ...`
- `db.backend=postgres ...`

### 1.2 为什么 onebox 更容易在容器云上“直起”？

容器云（ClawCloud）出现的 `upstream connect error ... delayed connect error: 111`，几乎都意味着：**平台网关连不到容器监听端口**（不是业务响应错误）。

onebox 的优势是把 UI 与 API 收敛到**同一个端口**：
- UI：`/ui2/`
- API：`/api/*`
- 端口：统一 `8000`

因此平台只需配置一个端口，不容易出现：
- Container Port 填错（80/8080/8001 等）
- 服务只监听 `127.0.0.1`（外部连不上）
- UI/API 分端口导致入口规则不一致

---

## 2. ClawCloud 推荐部署（onebox）

### 2.1 基本配置

- Image：`guangshanshui/firecrawl-manager:v0.1.7-onebox`
- Network：
  - Container Port：`8000`
  - Public Access：按需开启
- Command：**留空**（不要覆盖镜像默认启动命令；保持走 entrypoint）

### 2.2 必填环境变量

- `FCAM_ADMIN_TOKEN=<你的控制面 token>`
- `FCAM_MASTER_KEY=<至少 32 字节且长期不变>`

### 2.3 数据库建议（强烈推荐 Postgres）

容器云上 SQLite 容易因为卷/权限/文件锁在启动迁移阶段失败；推荐直接使用 Postgres：

- `FCAM_DATABASE__URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/DB`

> 说明：镜像启动时会自动执行 `alembic upgrade head`。我们做了 DB 就绪探测重试，降低“DB 尚未 ready”导致启动失败的概率。

### 2.4 如必须使用 SQLite（不推荐）

要点是保证 SQLite 文件在可写且持久化的目录：
- Local Storage 挂载到：`/app/data`
- 可选显式指定 DB 文件路径：
  - `FCAM_DATABASE__PATH=/app/data/api_manager.db`

---

## 3. 111 报错快速排障清单（只看 3 件事）

1) **容器状态是否 Active 且无持续重启**
   - 只要在 CrashLoop，网关必然连不上 → 111
2) **ClawCloud 的 Container Port 是否是 8000**
   - onebox 必须是 `8000`
3) **日志里 uvicorn 是否监听 0.0.0.0:8000**
   - 如果你覆盖了 Command 导致绑定 `127.0.0.1`，外部永远连不上

> 实操建议：优先贴出 Logs 最前 80 行（包含 entrypoint 的 `[fcam] ...` 与 uvicorn 启动行），比截图更容易定位。

---

## 4. 镜像 tag 发布策略（后续统一口径）

为减少容器云部署歧义，统一约定：

- **`latest` 永远等价于 onebox（单容器单端口）**  
  即 `guangshanshui/firecrawl-manager:latest` 默认就是 UI+API 同端口的 onebox 形态。

同时，为了避免容器云使用 `latest` 发生“无感漂移”，镜像 tag 采用以下约定：

- **固定发布 tag（推荐生产使用）**
  - `guangshanshui/firecrawl-manager:vX.Y.Z`
  - `guangshanshui/firecrawl-manager:vX.Y.Z-onebox`
  - 说明：`-onebox` 表示“单端口 onebox 部署口径”（UI+API 同端口），便于运维/文档明确选择；即便镜像内容与普通 tag 相同，也用 tag 表达“部署意图”。
- **滚动 tag（仅用于开发/试用，不建议生产；语义与 `latest` 相同）**
  - `guangshanshui/firecrawl-manager:onebox`

生产推荐写法（ClawCloud）：
- Image：`guangshanshui/firecrawl-manager:v0.1.7-onebox`
