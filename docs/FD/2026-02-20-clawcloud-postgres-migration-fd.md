# FD：ClawCloud 部署稳定性（SQLite CrashLoop）与 Postgres 迁移落地设计

> **对应 PRD**：`docs/PRD/2026-02-20-clawcloud-sqlite-crashloop-and-postgres-migration.md`  
> **创建时间**：2026-02-20  
> **状态**：Draft  
> **优先级**：P0（阻塞 ClawCloud 部署可用性）  
> **范围**：容器启动/迁移流程、数据库配置兼容、ClawCloud 部署指引

---

## 1. 背景与问题陈述

PRD 记录了在 **ClawCloud Run / AppLaunchpad** 上部署 `firecrawl-manager` 时出现的持续重启（CrashLoopBackOff），日志集中在启动阶段的迁移：

- `sqlite3.OperationalError: unable to open database file`（触发点：`alembic upgrade head`）
- 历史上也出现过 `/app/scripts/entrypoint.sh` 找不到（entrypoint 丢失/未打入镜像）

PRD 的结论是：**ClawCloud 环境不把 SQLite 视为稳定持久化方案**，平台默认落地应改为 **Postgres**。

本 FD 的目标是把这一结论“产品化”为：
- 代码层面的配置一致性与 fail-fast（避免“迁移用 PG、运行用 SQLite”的隐性错配）
- 启动脚本对 Postgres/SQLite 的行为分支更合理
- 文档与验收闭环

---

## 2. 目标 / 非目标

### 2.1 目标

1) **ClawCloud 默认以 Postgres 作为唯一持久化数据库**，服务可稳定 Running。  
2) **迁移期与运行期 DB 配置一致**：只要用户给了一个 DSN，不会出现“alembic 读到 A、应用读到 B”。  
3) 探活验收闭环：`/healthz` 200、`/readyz` 200，且可完成最小写入→重启→读回。

### 2.2 非目标

- 不保证从 ClawCloud 的 SQLite PVC 文件迁移历史数据（PRD 明确该环境 SQLite 不稳定）。
- 不在本 FD 引入“多副本并发迁移锁”（后续独立议题）。

---

## 3. 现状分析（基于代码）

### 3.1 启动与迁移入口

- 镜像入口：`Dockerfile` 的 `CMD ["/bin/sh", "/app/scripts/entrypoint.sh"]`
- 启动脚本：`scripts/entrypoint.sh`
  - 默认未显式配置 DB 时，使用 SQLite：`/app/data/api_manager.db`
  - 先执行 `alembic upgrade head`，再启动 `uvicorn app.main:app`

### 3.2 运行期 DB 配置来源（FastAPI）

- 配置系统：`app/config.py`
  - 环境变量覆盖规则：仅识别 `FCAM_<SECTION>__<FIELD>` 这种双下划线嵌套形式
  - 所以运行期 Postgres 连接串应来自：`FCAM_DATABASE__URL`
- DB 初始化：`app/db/session.py`
  - `config.database.url` 优先；否则根据 `config.database.path` 拼 SQLite URL

### 3.3 迁移期 DB 配置来源（Alembic）

- 迁移入口：`migrations/env.py::_database_url()`
  - 优先读 `FCAM_DATABASE_URL`
  - 其次读 `FCAM_DATABASE__URL`
  - 其次读 `FCAM_DATABASE__PATH`
  - 最后回退 `load_config()` + `build_database_url()`

### 3.4 关键风险：迁移期与运行期可能“读到不同 DB”

在当前实现下：
- **只设置 `FCAM_DATABASE_URL`（单下划线）**：
  - Alembic：会用它（迁移到 Postgres）
  - 应用运行期：`app/config.py` 不会把它映射到 `database.url`，因此可能仍走默认 SQLite（或 config.yaml 的 `database.path`）
  - 结果：出现“迁移对 PG 成功、应用却连 SQLite 并在 ClawCloud 上崩”的错配

PRD 里要求在 ClawCloud 上同时设置 `FCAM_DATABASE_URL` 与 `FCAM_DATABASE__URL`，本质是在规避这个错配。

### 3.5 关键风险：Postgres 模式下仍强依赖 `/app/data` 可写

`scripts/entrypoint.sh` 当前逻辑会无条件检查 `/app/data` 是否可写；当检测不可写时：
- 若认为“DB 已显式配置”（`DB_EXPLICIT=1`），会直接 `exit 1`

但在 **Postgres 模式** 下，服务并不依赖 `/app/data`。因此该检查在某些“只读根文件系统/特殊挂载策略”的平台上可能造成**不必要的启动失败**。

---

## 4. 功能设计（需要实现/修改的功能）

### 4.1 P0：DB 配置规范化（消除“只配一个变量就错配”）

目标：用户只要提供 **一个** Postgres DSN，就能保证迁移期与运行期一致；同时兼容 PRD 的“双变量写法”。

建议实现（两种方式二选一，推荐 A）：

**A. 在 `scripts/entrypoint.sh` 统一环境变量（推荐）**
- 若 `FCAM_DATABASE__URL` 已设置且 `FCAM_DATABASE_URL` 未设置：导出 `FCAM_DATABASE_URL=$FCAM_DATABASE__URL`（让 alembic 一定读到同一个 DSN）
- 若 `FCAM_DATABASE_URL` 已设置且 `FCAM_DATABASE__URL` 未设置：导出 `FCAM_DATABASE__URL=$FCAM_DATABASE_URL`（让运行期配置一定一致）
- 若使用 SQLite（默认或 fallback）：同时导出
  - `FCAM_DATABASE__PATH=/.../api_manager.db`
  - `FCAM_DATABASE__URL=sqlite:////...`（可选但建议，加速“运行期走 url 优先”与一致性）
  - `FCAM_DATABASE_URL=...`（保持迁移期兼容）

**B. 在 `app/config.py` 支持 `FCAM_DATABASE_URL` 作为 `database.url` 的 alias**
- 在 `_env_overrides()` 中对 `FCAM_DATABASE_URL` 做特殊映射（写入 `database.url`）
- 好处：即使不走 entrypoint（例如某些 PaaS 自定义启动命令），运行期也不会错配

> 若只做 A：依赖入口脚本必须被使用。  
> 若只做 B：Alembic 仍可能优先读 `FCAM_DATABASE_URL`，但运行期一致；迁移期一致性需额外保证。  
> 因此优先推荐 **A +（可选）B**。

### 4.2 P0：启动脚本对 Postgres/SQLite 做正确分支（避免无意义失败）

目标：只有在“确实使用 SQLite 文件”时，才要求 `/app/data` 可写；Postgres 模式跳过该检查。

建议实现：
- 解析“实际 DB backend”：
  - 若 `FCAM_DATABASE_URL` 或 `FCAM_DATABASE__URL` 以 `postgresql`/`postgres` 开头，则 backend=postgres
  - 若以 `sqlite` 开头或未设置且走默认 path，则 backend=sqlite
- `is_writable_dir /app/data` 的检查只在 backend=sqlite 时执行

### 4.3 P0：Fail-fast 与可观测性（面向排障）

目标：让“配置错了导致用了 SQLite”这件事在日志里可立即识别，并且不要泄露机密。

建议实现（示例行为）：
- 启动打印：`db.backend=postgres|sqlite`、`db.source=env|config|default`、`db.sqlite_path=...`（仅在 sqlite）
- 若检测到仅设置了 `FCAM_DATABASE_URL`（或仅设置 `FCAM_DATABASE__URL`）：打印 WARN 并自动补齐 alias（见 4.1）
- 打印 DSN 时必须脱敏（隐藏密码）

### 4.4 P1：文档对齐与收敛

目标：把 PRD 的“必须设置两份 DSN”升级为“设置一个也不会错”，并把推荐写法固化。

建议修改：
- `docs/deploy-clawcloud.md`：明确推荐只用 `FCAM_DATABASE__URL`（或只用 `FCAM_DATABASE_URL`），系统会自动做 alias；保留“双写”作为兼容说明
- `docs/docker.md`：同上，避免读者只设置 `FCAM_DATABASE_URL` 导致线上跑到 SQLite

---

## 5. 验收标准（DoD）

在 ClawCloud（或等价环境）满足以下条件：

1) Pod 连续运行 ≥ 30 分钟，重启次数为 0  
2) 启动日志无 `sqlite3.OperationalError: unable to open database file`  
3) `GET /healthz` 返回 200  
4) `GET /readyz` 返回 200（在启用控制面时要求 `FCAM_ADMIN_TOKEN`；任何模式下要求 `FCAM_MASTER_KEY`）  
5) 最小数据闭环：写入一条会落库的资源 → 重启 → 仍可读回  
6) 只配置 **一个** Postgres DSN（`FCAM_DATABASE_URL` 或 `FCAM_DATABASE__URL` 任意一种）也能通过验收

---

## 6. 回滚策略

- 仍支持 SQLite 本地/临时兜底（不持久化）：
  - `FCAM_DATABASE_URL=sqlite:////tmp/api_manager.db`
  - `FCAM_DATABASE__PATH=/tmp/api_manager.db`
- 若生产 Postgres 出现问题：回滚到上一个可用镜像 tag / digest，并保持 DSN 不变

---

## 7. 代码审查结论（按严重程度排序）

### 7.1 总体评价

现有实现已经具备“启动自动迁移 + /healthz /readyz + 文档指引”的基础闭环；但在 ClawCloud 这种平台环境下，**DB 配置的命名兼容与启动脚本分支条件**存在高风险错配点，容易让用户误配后无提示地落回 SQLite 并 CrashLoop。

### 7.2 具体问题列表

**P0**
1) 运行期配置仅识别 `FCAM_DATABASE__URL`，而迁移期优先读 `FCAM_DATABASE_URL`：只配置一个变量会导致迁移期/运行期 DB 不一致（见 `app/config.py` 与 `migrations/env.py`）。  
2) `scripts/entrypoint.sh` 在 Postgres 模式下仍强制要求 `/app/data` 可写，可能导致“不需要的数据目录”阻塞启动。  

**P1**
3) 启动日志中对 Postgres 模式缺少“最终使用了哪个 DB”的可观测性提示（同时要避免泄露密码）。  
4) 文档目前用“双变量”作为约束是可行的 workaround，但长期会增加误配概率（用户只抄一个变量时仍会踩坑）。

### 7.3 改进建议与示例（伪代码）

**建议 1（entrypoint 做 alias）**

```sh
# if only one is set, mirror to the other
if [ -n "${FCAM_DATABASE__URL:-}" ] && [ -z "${FCAM_DATABASE_URL:-}" ]; then
  export FCAM_DATABASE_URL="${FCAM_DATABASE__URL}"
fi
if [ -n "${FCAM_DATABASE_URL:-}" ] && [ -z "${FCAM_DATABASE__URL:-}" ]; then
  export FCAM_DATABASE__URL="${FCAM_DATABASE_URL}"
fi
```

**建议 2（仅在 sqlite 时检查 /app/data 可写）**

```sh
db_url="${FCAM_DATABASE_URL:-${FCAM_DATABASE__URL:-}}"
case "$db_url" in
  sqlite*|"") need_data_dir=1 ;;
  *) need_data_dir=0 ;;
esac
```

