# TODO：ClawCloud 稳定部署（SQLite CrashLoop）与 SQLite→Postgres 后端直迁（P0）

> **PRD**：`docs/PRD/2026-02-20-clawcloud-sqlite-crashloop-and-postgres-migration.md`  
> **FD**：`docs/FD/2026-02-20-clawcloud-postgres-migration-fd.md`  
> **TDD**：`docs/TDD/2026-02-20-clawcloud-postgres-migration-tdd.md`

---

## 0. 范围与目标

- **P0 目标**：后端具备“直迁”能力（SQLite → Postgres），并确保 ClawCloud 上默认 Postgres 部署可稳定运行（避免 SQLite CrashLoop）。
- **非目标**：不承诺从 ClawCloud PVC 上的 SQLite 文件迁移历史数据（PRD 已说明该环境 SQLite 不稳定）。

---

## 1. P0：启动期 DB 一致性与 ClawCloud 可用性

### 1.1 DB 配置一致性（迁移期 vs 运行期）

- [x] `scripts/entrypoint.sh`：`FCAM_DATABASE_URL` 与 `FCAM_DATABASE__URL` 双向 alias（只配置一个 DSN 也能一致）
- [x] `scripts/entrypoint.sh`：输出 `db.backend/db.source` 启动日志（DSN 必须脱敏）
- [x] （建议）`app/config.py`：支持 `FCAM_DATABASE_URL` → `database.url` 的 alias（防止绕过 entrypoint 直接跑 uvicorn 时错配）

### 1.2 Postgres 模式不依赖 /app/data

- [x] `scripts/entrypoint.sh`：识别最终 `db_url` 的 backend；Postgres 模式跳过 `/app/data` 可写性检查
- [x] 保留 SQLite 模式原行为：不可写时可回退 `/tmp`（仅当未显式配置 DB 且允许 fallback）

### 1.3 Alembic upgrade 等待 DB 就绪

- [x] `scripts/entrypoint.sh`：为启动迁移增加 DB 就绪等待/重试（`FCAM_DB_MIGRATE_RETRIES`、`FCAM_DB_MIGRATE_SLEEP_SECONDS`）
- [x] 日志区分：连不上 DB（可重试）vs migration 逻辑错误（快速失败）

---

## 2. P0：SQLite → Postgres 直迁工具（最高优先级）

### 2.1 工具形态与参数

- [x] 新增迁移命令（推荐）：`python -m app.tools.migrate_sqlite_to_postgres`
- [x] 参数：`--sqlite-path`、`--postgres-url`
- [x] 安全开关：`--dry-run`、`--truncate`、`--include/--exclude`、`--batch-size`、`--verify`

### 2.2 前置检查（必须）

- [x] SQLite 可读 + `SELECT 1`
- [x] Postgres 可连 + `SELECT 1`
- [x] 目标库 schema 已到 `head`（要求先执行 `alembic upgrade head`）
- [x] 目标表为空（除非显式 `--truncate`）

### 2.3 迁移实现（顺序/一致性/序列）

- [x] 迁移顺序：`clients` → `api_keys` → `idempotency_records` → `request_logs` → `audit_logs`
- [x] 保留源 `id`（保持 FK 引用一致）
- [x] Postgres：每个表迁移后修正序列 `setval(...)`（避免后续插入冲突）
- [x] 默认 fail-fast：任一表迁移失败立即停止并给出可行动错误信息

### 2.4 迁移后校验（建议默认开启）

- [x] 行数对比（源表 vs 目标表）
- [x] 抽样校验：核心字段一致（密文字段不解密，只校验非空/长度/类型）
- [x] 输出 summary（每表：源行数/迁移行数/耗时）

---

## 3. P0：测试与验收

### 3.1 自动化测试

- [x] 单测：DB env alias 行为（只配 `FCAM_DATABASE_URL` / 只配 `FCAM_DATABASE__URL` 都能一致）
- [x] 集成测试：临时 Postgres + 临时 SQLite → 运行迁移工具 → 校验行数、外键一致、序列修正后可继续插入

### 3.2 ClawCloud 验收清单（上线前必做）

- [ ] Pod 连续运行 ≥ 30 分钟，重启次数 0
- [ ] 启动迁移成功（`alembic upgrade head` 无报错）
- [ ] `GET /healthz`=200
- [ ] `GET /readyz`=200
- [ ] 最小数据闭环：写入 → 重启 → 读回（数据仍在）

---

## 4. 文档与发布

- [x] 更新 `docs/deploy-clawcloud.md`：推荐 Postgres + DSN 单变量写法（系统自动 alias），双写作为兼容说明
- [x] 更新 `docs/docker.md`：同上，避免读者只复制 `FCAM_DATABASE_URL` 导致运行期落回 SQLite
- [x] 发布固定 tag（避免 ClawCloud 使用 `latest` 漂移）；Release note 写清迁移工具用法与风险提示（Git tag：`v0.1.7`；Docker：`guangshanshui/firecrawl-manager:v0.1.7`）
