# TDD：ClawCloud 稳定部署与 SQLite→Postgres 后端直迁能力（P0）

> **PRD**：`docs/PRD/2026-02-20-clawcloud-sqlite-crashloop-and-postgres-migration.md`  
> **FD**：`docs/FD/2026-02-20-clawcloud-postgres-migration-fd.md`  
> **创建时间**：2026-02-20  
> **状态**：Draft  
> **优先级**：P0

---

## 0. 结论先行（我们要交付什么）

P0 后端交付物分两条线：

1) **启动期 DB 一致性与 ClawCloud 可用性**  
   - 迁移期（Alembic）与运行期（FastAPI）永远指向同一个数据库（即使只配置一个 DSN）
   - Postgres 模式不再强依赖 `/app/data` 可写
   - `alembic upgrade head` 支持“等待 DB 就绪”的重试（适配平台编排时序）

2) **后端“直迁”能力（SQLite → Postgres）**（最重要）  
   - 提供一个可复用的**一次性迁移命令**（CLI/模块），把现有 SQLite 文件的数据复制到 Postgres
   - 保留主键 ID，保证外键引用一致；迁移后修正 Postgres 序列
   - 支持 `--dry-run`、`--include/--exclude`、`--truncate` 等安全开关与校验输出

---

## 1. 约束与假设

- 代码基于 SQLAlchemy ORM + Alembic（见 `app/db/models.py`、`migrations/`）。
- SQLite 迁移来源主要是本地/单机（PRD 明确 ClawCloud 上 SQLite PVC 不稳定，不作为线上迁移来源保障）。
- 迁移工具必须显式触发（避免“服务启动时自动搬运数据”造成风险与不可控耗时）。
- 迁移过程中应处于维护窗口：建议停掉业务流量或停止 FCAM 实例写入（否则 SQLite 与 Postgres 写入并行会导致数据不一致）。

---

## 2. 当前实现要点（作为设计输入）

### 2.1 迁移期 DB URL（Alembic）

`migrations/env.py::_database_url()` 读取优先级：
1. `FCAM_DATABASE_URL`
2. `FCAM_DATABASE__URL`
3. `FCAM_DATABASE__PATH`
4. `load_config()` → `database.url` / `database.path`

### 2.2 运行期 DB URL（FastAPI）

`app/config.py` 仅支持 `FCAM_DATABASE__URL`（嵌套覆盖）进入 `config.database.url`。  
`FCAM_DATABASE_URL` 不会被识别为配置覆盖项（导致“迁移到 PG，运行却连 SQLite”的错配风险）。

### 2.3 启动脚本风险点

`scripts/entrypoint.sh` 当前无条件要求 `/app/data` 可写；但在 Postgres 模式下该目录并非必需，可能造成无意义的启动失败。

---

## 3. 技术设计（P0）：启动期 DB 一致性 + 可用性

### 3.1 统一 DB 配置（env aliasing）

目标：只设置一个 DSN，也能保证 Alembic 与应用运行期一致。

#### 设计方案（主方案：在 entrypoint 做镜像层统一）

在 `scripts/entrypoint.sh` 启动早期加入 alias 规则：

- 如果只设置了 `FCAM_DATABASE__URL`：自动 `export FCAM_DATABASE_URL="$FCAM_DATABASE__URL"`
- 如果只设置了 `FCAM_DATABASE_URL`：自动 `export FCAM_DATABASE__URL="$FCAM_DATABASE_URL"`

这样：
- `alembic upgrade head` 与 `app/config.py` 都会读到同一个 DSN
- PRD 要求的“双写”仍兼容（但不再是“必须”）

#### 可选增强（防止绕过 entrypoint 的场景）

在 `app/config.py` 中为 `FCAM_DATABASE_URL` 增加一个 alias：
- 若环境存在 `FCAM_DATABASE_URL` 且未配置 `FCAM_DATABASE__URL`，则将其映射到 `database.url`

> 是否要做可选增强：取决于我们是否允许平台直接运行 `uvicorn app.main:app` 绕过 entrypoint。为降低误配风险，建议也做。

### 3.2 Postgres 模式跳过 `/app/data` 写权限检查

目标：只有在“实际使用 SQLite 文件”时才检查数据目录写权限。

判定逻辑：
- 取最终 DB URL（alias 规则执行后）：
  - 若 `db_url` 为空或以 `sqlite` 开头：视为 SQLite 模式 → 需要检查 `/app/data` 可写
  - 否则（postgresql+psycopg 等）：视为 Postgres 模式 → 跳过 `/app/data` 可写检查

SQLite 模式下保留当前行为：
- `/app/data` 不可写且未显式配置 DB：回退 `/tmp/api_manager.db`（不持久化）并继续启动（用于“先跑起来”）
- `/app/data` 不可写且用户显式配置了 SQLite path：fail-fast（避免默默换位置）

### 3.3 Alembic upgrade 的 DB 就绪重试

目标：适配平台编排时序（Postgres 容器/服务可能先未 ready），避免启动即失败进入 CrashLoop。

在 `scripts/entrypoint.sh` 为 `alembic upgrade head` 增加可配置的重试：

- `FCAM_DB_MIGRATE_RETRIES`（默认例如 30）
- `FCAM_DB_MIGRATE_SLEEP_SECONDS`（默认例如 2）

策略：
- 仅对“连接不可用/超时/临时网络错误”重试
- 对“不可恢复错误”（语法错误、认证失败、数据库不存在、迁移脚本错误）快速失败

实现细节（shell 层）：
- 简化做法：对 `alembic upgrade head` 失败统一重试，直到超过次数（风险：对真实 migration bug 也会重试浪费时间）
- 更稳妥做法：先做一次轻量连接探测再迁移（例如 `python -c` 使用 SQLAlchemy `SELECT 1`），连接 OK 再运行 alembic

推荐：**连接探测 + alembic** 两段式（便于把“连不上 DB”与“迁移失败”区分开，日志更明确）。

### 3.4 日志与脱敏

要求：
- 必须打印最终 `db.backend`（sqlite|postgres）与 `db.source`（env|config|default）
- 打印 DSN 时必须脱敏密码（例如 `postgresql+psycopg://user:***@host/db`）
- 不要输出 `FCAM_ADMIN_TOKEN`、`FCAM_MASTER_KEY`

---

## 4. 技术设计（P0）：SQLite → Postgres 后端直迁工具

### 4.1 交付形态

新增一个显式运行的迁移命令（示例二选一）：

- 方案 A（推荐）：`python -m app.tools.migrate_sqlite_to_postgres ...`
- 方案 B：`scripts/migrate-sqlite-to-postgres.sh`（内部调用 Python 模块）

理由：迁移逻辑需要 ORM/SQLAlchemy，放 Python 更易测试与维护；shell 只负责参数与环境封装。

### 4.2 输入与参数（建议）

必选：
- `--sqlite-path /path/to/api_manager.db`
- `--postgres-url postgresql+psycopg://...`

可选（安全开关）：
- `--dry-run`：只做连通性检查、表存在性检查、行数统计，不写入
- `--truncate`：迁移前清空目标表（危险操作，必须显式）
- `--include clients,api_keys,...` / `--exclude request_logs,audit_logs`
- `--batch-size 1000`：批量写入大小（默认 1000）
- `--verify`：迁移后做行数对比与抽样校验（默认开启）

环境变量兼容（可选）：
- 若未传参，可读取 `FCAM_DATABASE__PATH`/`FCAM_DATABASE__URL`/`FCAM_DATABASE_URL` 作为默认值，但建议 CLI 参数优先。

### 4.3 迁移范围（表与依赖顺序）

依据 `app/db/models.py` 的外键关系：

1. `clients`
2. `api_keys`（FK→clients）
3. `idempotency_records`（FK→clients）
4. `request_logs`（FK→clients、api_keys）
5. `audit_logs`（无 FK 依赖）

> 默认建议 **包含** `clients`、`api_keys`、`idempotency_records`；  
> `request_logs`、`audit_logs` 可能很大，默认可迁移也可通过 `--exclude` 跳过（按团队偏好定默认）。

### 4.4 ID 保留与 Postgres 序列修正

必须保留源库 `id`，否则外键引用会失效。

写入 Postgres 后，需对每个自增主键表执行序列修正（示例）：

- `SELECT setval(pg_get_serial_sequence('clients','id'), (SELECT COALESCE(MAX(id), 1) FROM clients));`
- `api_keys`、`request_logs`、`idempotency_records`、`audit_logs` 同理

### 4.5 数据一致性与事务策略

#### SQLite 侧读取一致性

- 迁移应在维护窗口执行（首选），否则只能做到“尽力一致”
- 读取时使用一个只读连接并尽量在同一事务中读取（SQLite 对长事务/锁敏感，需平衡）

建议策略：
- 每张表独立事务读取 + 分批写入（降低锁持有时间）
- 迁移开始前记录源表行数（用于迁移后对比）

#### Postgres 侧写入策略

每张表迁移使用一个事务：
- 写入失败则回滚该表，输出错误并停止（默认 fail-fast）
- 若启用 `--truncate`，truncate 与插入放在同一事务

插入方式建议使用 SQLAlchemy Core bulk insert（比 ORM add_all 更高效且内存占用更低）：
- 从 SQLite 读取 row → 转 dict → `target_conn.execute(table.insert(), batch)`
- 注意 `LargeBinary`/`DateTime`/`Date` 类型的兼容（SQLAlchemy 会处理大部分；需要在测试中覆盖）

冲突策略：
- 默认不支持“增量合并”（避免隐性覆盖）
- 若目标库非空且未指定 `--truncate`：直接失败并提示（防止误覆盖线上 PG）
- 后续如需要“merge/upsert”，另开设计（会涉及唯一键与冲突解决策略）

### 4.6 迁移前置检查（必须）

在写入前必须检查：

1) SQLite 文件存在且可读；能 `SELECT 1`  
2) Postgres 可连接；能 `SELECT 1`  
3) Postgres schema 已是最新（`alembic upgrade head` 先执行）  
4) 两端表结构兼容（至少表存在、必要列存在；可通过 SQLAlchemy metadata/反射对比或简单的“必需列集合”校验）  
5) 若未 `--truncate`：目标表必须为空（或至少关键表为空）

### 4.7 迁移后校验（建议默认开启）

- 行数对比：源表 vs 目标表（对 `--exclude` 的表跳过）
- 抽样校验：
  - `clients`：抽样比对 `id/name/is_active/...`
  - `api_keys`：抽样比对 `api_key_hash/api_key_last4/is_active/...` 与密文字段长度（不解密，不触碰 master key）
- 序列校验：插入一条新记录验证自增不冲突（可选）

输出：
- 打印迁移 summary（每表源行数、迁移行数、耗时）
- 错误时打印可行动建议（例如“目标库非空请加 --truncate”）

### 4.8 性能与资源

瓶颈通常来自 `request_logs` 这类大表：
- 必须提供 `--exclude request_logs`（或把它设为默认 exclude）
- 写入采用 batch（默认 1000，可调）
- 每批提交次数与事务范围要平衡：建议每表一个事务 + 分批 execute（同一事务）

---

## 5. 测试设计（最小但有效）

### 5.1 单元测试（pytest）

1) DB URL 解析一致性（如果实现了 Python 层 alias）  
   - 仅设 `FCAM_DATABASE_URL` 也能让 `load_config().database.url` 生效  

2) 迁移工具的“前置检查逻辑”  
   - SQLite 文件缺失/不可读 → 友好报错  
   - Postgres DSN 不可连 → 重试/失败策略符合预期  

### 5.2 集成测试（建议，需有 Postgres 测试依赖）

用 docker/testcontainer 启一个临时 Postgres：
- 准备一个临时 SQLite，插入少量 `clients/api_keys/idempotency_records`
- 运行迁移工具到 Postgres
- 校验行数、外键引用、序列 setval 后可继续插入

> 如果当前 CI 不方便引入 Postgres：至少在 `scripts/acceptance/` 提供一键本地验证脚本。

---

## 6. 实施任务清单（Backend 为主）

P0（必须）：
- 修改 `scripts/entrypoint.sh`：DB env alias + Postgres 跳过 `/app/data` 检查 + migrate retry
- （建议）修改 `app/config.py`：`FCAM_DATABASE_URL` alias 到 `database.url`（避免绕过 entrypoint）
- 新增迁移工具模块：`app/tools/migrate_sqlite_to_postgres.py`（或等价路径）
- 文档更新：`docs/deploy-clawcloud.md`、`docs/docker.md`（把“双写 DSN”从必须降级为兼容）

P1（可选）：
- 迁移工具支持 `--exclude request_logs` 默认策略调整
- 增加更强的 schema 对比（列类型/nullable/unique）

---

## 7. 发布与回滚

发布：
- 出一个固定 tag（避免 ClawCloud 使用 latest 漂移）
- 在 release note 写清：DB env alias 行为、迁移工具用法、默认不自动搬运数据

回滚：
- 仍可通过环境变量切回 SQLite（建议仅本地/临时）：`sqlite:////tmp/api_manager.db`
- 迁移工具不改源 SQLite，回滚不影响源数据（前提：未 `--truncate` PG 或已做好 PG 备份）

