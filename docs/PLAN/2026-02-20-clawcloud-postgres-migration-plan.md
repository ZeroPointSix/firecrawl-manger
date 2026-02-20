# PLAN：ClawCloud Postgres 稳定部署 + SQLite→Postgres 后端直迁（P0）

> **PRD**：`docs/PRD/2026-02-20-clawcloud-sqlite-crashloop-and-postgres-migration.md`  
> **FD**：`docs/FD/2026-02-20-clawcloud-postgres-migration-fd.md`  
> **TDD**：`docs/TDD/2026-02-20-clawcloud-postgres-migration-tdd.md`  
> **TODO**：`docs/TODO/2026-02-20-clawcloud-postgres-migration.md`

---

## 0. 实施策略（先后顺序）

1) **先做“启动期一致性 + 可用性”**（避免 ClawCloud CrashLoop、避免迁移期/运行期错配）。  
2) **再做“直迁工具”**（P0 核心交付），并补齐最小但有效的自动化测试。  
3) 最后做文档收敛与发布（固定 tag、上线验收）。

---

## 1. Phase 1：启动期一致性与 ClawCloud 稳定启动（P0）

### 1.1 改动点

- `scripts/entrypoint.sh`
  - 做 `FCAM_DATABASE_URL` ↔ `FCAM_DATABASE__URL` alias（只配一个 DSN 也一致）
  - 识别 backend：Postgres 模式跳过 `/app/data` 可写性检查
  - 增加 DB 就绪等待/重试，再执行 `alembic upgrade head`
  - 输出关键启动日志（脱敏）
- （建议）`app/config.py`
  - 支持 `FCAM_DATABASE_URL` alias 到 `database.url`（防止绕过 entrypoint）

### 1.2 Gate（通过才进入 Phase 2）

- 本地 `docker compose --profile prod up`（或等价）启动成功
- 只配置 **一个** DSN（任意一种 env 命名）也能通过：
  - `alembic upgrade head`
  - `/readyz`=200

---

## 2. Phase 2：SQLite → Postgres 直迁工具（P0 核心）

### 2.1 改动点

- 新增 Python 迁移模块（可作为 CLI 运行）
- 实现：
  - 前置检查（SQLite/PG 连通、schema 到 head、目标表为空/可 truncate）
  - 按依赖顺序迁移并保留 `id`
  - Postgres 序列修正 `setval`
  - 迁移后校验（行数 + 抽样）
  - 可靠的错误信息与 summary 输出

### 2.2 Gate（通过才进入 Phase 3）

- 以一个最小 SQLite 样本库为输入，迁移到空 Postgres 后：
  - 行数一致
  - 外键引用一致
  - 后续插入不发生主键冲突（序列已修正）

---

## 3. Phase 3：测试与文档收敛（P0）

### 3.1 自动化测试

- 单测：DB env alias 行为
- 集成测试：SQLite→Postgres 迁移闭环（推荐用临时 Postgres 容器）

### 3.2 文档更新

- `docs/deploy-clawcloud.md`：推荐 DSN 单变量写法（自动 alias），双写作为兼容说明
- `docs/docker.md`：同上
- 增加迁移工具使用说明（放在 TDD 或单独短文档均可）

### 3.3 Gate（发布前）

- `pytest` 通过（覆盖率门禁保持现状）
- 文档能按步骤复现：ClawCloud Postgres 部署 + 直迁（本地/自建环境）

---

## 4. Phase 4：发布与上线验收（P0）

- 发布固定镜像 tag（不要用 `latest` 作为上线版本）
- ClawCloud 验收：
  - Pod 连续运行 ≥ 30 分钟，重启次数 0
  - `/healthz`=200、`/readyz`=200
  - 最小数据闭环：写入 → 重启 → 读回

