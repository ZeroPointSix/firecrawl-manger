# PRD：ClawCloud 部署故障记录（SQLite CrashLoop）与数据库迁移方案（Postgres）

> **创建时间**：2026-02-20  
> **状态**：Draft  
> **优先级**：P0（阻塞 ClawCloud 部署）  
> **影响范围**：ClawCloud Run / AppLaunchpad 部署与上线可用性

---

## 1. BUG 记录（现象为主）

### 1.1 现象摘要

- 在 ClawCloud Run / AppLaunchpad 部署 `guangshanshui/firecrawl-manager` 后，Pod 进入 `CrashLoopBackOff / BackOff`，持续重启（多次 `Restart`）。
- 启动日志中出现数据库迁移阶段报错：
  - `sqlite3.OperationalError: unable to open database file`
  - 触发点为启动阶段执行 `alembic upgrade head`。
- 曾出现过启动脚本不存在（entrypoint 丢失）的日志：
  - `/bin/sh: 0: cannot open /app/scripts/entrypoint.sh: No such file`

### 1.2 复现环境（当时配置）

- 平台：ClawCloud Run / AppLaunchpad（免费容器）
- 镜像：`guangshanshui/firecrawl-manager:latest` / `guangshanshui/firecrawl-manager:v0.1.x`（多次切换 tag 复现）
- 端口：`8000`
- 环境变量（示例，机密需用 Secret 注入）：
  - `FCAM_ADMIN_TOKEN=<redacted>`
  - `FCAM_MASTER_KEY=<redacted>`
  - `FCAM_CONFIG=/app/config.yaml`
  - `FCAM_SERVER__ENABLE_DOCS=false`（注意双下划线）
- ConfigMap：挂载到 `/app/config.yaml`
- Storage（PVC）：挂载到 `/app/data`（例如 1Gi）

### 1.3 复现步骤（黑盒）

1. 在 ClawCloud AppLaunchpad 创建应用，镜像选择 `guangshanshui/firecrawl-manager:<tag>`，暴露 `8000`。
2. 配置环境变量：至少 `FCAM_ADMIN_TOKEN`、`FCAM_MASTER_KEY`（以及可选的 `FCAM_CONFIG` 等）。
3. （可选）以 ConfigMap 方式写入 `/app/config.yaml`。
4. （可选）挂载 Storage 到 `/app/data` 以持久化 SQLite 文件。
5. 部署后查看 **Pod Events / Pod Logs**，观察启动阶段 `alembic` 报错并重启。

### 1.4 日志特征（摘录）

```text
/bin/sh: 0: cannot open /app/scripts/entrypoint.sh: No such file
```

```text
sqlite3.OperationalError: unable to open database file
...
sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) unable to open database file
```

### 1.5 期望结果

- Pod 稳定 Running（无持续重启）
- 启动阶段数据库迁移成功完成（`alembic upgrade head` 不报错）
- 探活通过：
  - `GET /healthz` 返回 `200`
  - `GET /readyz` 返回 `200`

### 1.6 结论（作为需求约束沉淀）

- **ClawCloud 环境不将 SQLite 作为可用的持久化数据库方案**（启动迁移阶段出现不可恢复的 `unable to open database file` 现象）。
- ClawCloud 部署的数据库默认方案调整为 **Postgres**（见第 2 节）。

---

## 2. 数据库迁移方案（SQLite → Postgres）

### 2.1 目标

- 在 ClawCloud 上以 **Postgres** 作为唯一持久化数据库，保证应用可启动、可迁移、可持久化。
- 本地/单机 MVP 仍可继续使用 SQLite（便于快速启动与演示）。

### 2.2 范围

- 在 ClawCloud 中新增 Postgres（平台托管或自建容器均可）。
- `firecrawl-manager` 通过环境变量/配置切换到 Postgres。
- 按“冒烟验证清单”验收启动与最小数据读写闭环。

### 2.3 非目标

- 不保证从 ClawCloud 上的 SQLite PVC 文件迁移历史数据（该环境下 SQLite 不稳定/不可用）。
- 不在本 PRD 中引入多副本并发迁移锁（后续独立拆分）。

### 2.4 配置约定（必须）

在 ClawCloud 上部署时，必须显式设置 **同一份** Postgres DSN 到以下环境变量（避免迁移期/运行期读到不同来源）：

- `FCAM_DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/DB`
- `FCAM_DATABASE__URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/DB`

机密（必须用 Secret，不写入仓库/ConfigMap）：
- `FCAM_ADMIN_TOKEN`
- `FCAM_MASTER_KEY`

### 2.5 ClawCloud 实施步骤（操作清单）

#### A. 创建 Postgres（同集群内）

- 镜像建议：`postgres:16-alpine`（或平台提供的 Postgres 服务）
- 必要环境变量（示例）：
  - `POSTGRES_DB=firecrawl_manager`
  - `POSTGRES_USER=firecrawl_manager`
  - `POSTGRES_PASSWORD=<secret>`
- Storage：挂载到 `/var/lib/postgresql/data`（例如 1Gi+）
- 网络：优先仅集群内访问（不对公网暴露）

#### B. 配置 firecrawl-manager 使用 Postgres

- 镜像：`guangshanshui/firecrawl-manager:<tag>`（建议固定版本 tag 或 digest）
- 环境变量：
  - `FCAM_DATABASE_URL=postgresql+psycopg://...`
  - `FCAM_DATABASE__URL=postgresql+psycopg://...`
  - `FCAM_ADMIN_TOKEN=<secret>`
  - `FCAM_MASTER_KEY=<secret>`
  - `FCAM_SERVER__ENABLE_DOCS=false`（可选）
  - `FCAM_CONFIG=/app/config.yaml`（如使用 ConfigMap 覆盖配置，可选）
- Storage：
  - Postgres 容器必须挂载数据盘
  - `firecrawl-manager` 容器在 Postgres 模式下通常 **不需要** 挂载 `/app/data`

#### C. 冒烟验证（上线前必做）

1. Pod logs 中不再出现 `sqlite3.OperationalError`；启动阶段 `alembic upgrade head` 正常完成。
2. 探活：
   - `GET /healthz` 返回 `200`
   - `GET /readyz` 返回 `200`
3. 最小数据闭环（控制面）：
   - 新增一条 Key/Client（或任一会落库的资源）
   - 重启 Pod（或滚动更新）后数据仍存在
4. 连续观察 ≥ 10 分钟无 `CrashLoopBackOff`

### 2.6 （可选）本地 SQLite 数据迁移到 Postgres

适用：**本地/自建服务器**从 SQLite 迁移到 Postgres（非 ClawCloud 线上）。

建议流程（高层步骤）：
1. 备份 SQLite 文件（`api_manager.db`）。
2. 准备 Postgres 空库。
3. 运行 `alembic upgrade head` 初始化表结构。
4. 使用数据迁移工具导入（例如 `pgloader` 或自写导入脚本）。
5. 切换 `FCAM_DATABASE_URL / FCAM_DATABASE__URL` 到 Postgres 并验证关键表行数抽样一致。

### 2.7 验收标准（Definition of Done）

- ClawCloud 上 `firecrawl-manager` Pod 连续运行 ≥ 30 分钟、重启次数为 0
- 探活 `GET /healthz` 与 `GET /readyz` 均返回 `200`
- 启动迁移成功（`alembic upgrade head` 不报错）
- 最小数据闭环通过（写入→重启→读回）
- 文档可复现（按本 PRD 操作清单配置即可跑通）

### 2.8 回滚策略

- 若 Postgres 暂不可用，可临时切换到非持久化 SQLite（仅用于演示/调试）：
  - `FCAM_DATABASE_URL=sqlite:////tmp/api_manager.db`
  - `FCAM_DATABASE__PATH=/tmp/api_manager.db`
- 镜像与配置以环境变量切换为主，支持一键回滚到上一个可运行版本（或上一个可用配置）。

