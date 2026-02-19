# 测试金字塔落地计划（方案 A：按目录分层）

目标：把现有测试体系按**测试金字塔**原则（单元测试最多、集成测试适中、端到端最少）进行“可执行、可演进”的分层改造，做到：
- 默认 PR/本地只跑**快速且稳定**的 `unit + integration`
- `e2e` 作为黑盒回归，**少而关键**，并且**可控启用**
- 真实上游（成本/波动）与外部兼容性（外部环境依赖）被明确隔离，不影响日常开发反馈

本计划采用 **方案 A：目录分层**：
```
tests/
  unit/
  integration/
  e2e/
```

---

## 0. 现状摘要（用于对齐口径）

- 后端测试：当前 `tests/` 平铺，包含大量 `fastapi.testclient.TestClient + SQLite + httpx.MockTransport`（稳定、快，但属于“进程内集成测试”更贴切）。
- 端到端测试：存在“起 uvicorn 进程”的黑盒 E2E；真实上游调用通过 `FCAM_E2E_ALLOW_UPSTREAM=1` 显式门禁；另有外部兼容性测试依赖 `FCAM_FC_*` 环境变量。
- pytest 配置：`pyproject.toml` 与 `pytest.ini` 同时配置 markers/addopts，存在未来漂移风险。
- 前端：`webui/` 有 `type-check`，暂无单测/E2E。

---

## 1. 分层定义（强约束，避免争议）

### 1.1 Unit（单元测试）
- **不启动 HTTP 服务**（不跑 uvicorn、不走网络）
- **不依赖真实 DB**（可用纯内存对象，或最小替身；尽量避免 SQLite 真实写入）
- 允许使用 mock/stub/fake，重点验证：
  - 纯逻辑：策略计算、错误映射、路径/头规范化、脱敏规则、配额/重试决策等
  - 边界条件与组合（参数化覆盖）
- 期望：执行极快（秒级），数量最多

### 1.2 Integration（集成测试）
- 允许使用 `TestClient` / `httpx.MockTransport`
- 允许使用 SQLite 临时库（`tmp_path`），用于验证：
  - FastAPI 路由、依赖注入、鉴权、限流/并发/配额、幂等、日志落库、迁移等模块协作
  - API 契约：状态码、错误体结构、分页字段等
- 不依赖真实上游与外部环境，保持确定性与可重复

### 1.3 E2E（端到端测试：黑盒）
- 必须通过 HTTP 请求驱动（起 uvicorn 或 docker-compose），不使用 `TestClient`
- 默认不跑（需要显式开关，例如 `FCAM_E2E=1`）
- 只保留“最值钱的少数链路”（5–15 条），用于验证：
  - 服务可启动与基础探活（`/healthz`、`/readyz`）
  - `/admin/*` 与 `/api/*` 的关键冒烟路径与契约

### 1.4 非金字塔层：Upstream / External（必须隔离）
> 这两类测试“必然不稳定/有成本/依赖外部环境”，不应混入日常反馈链路。

- **Upstream**：真实调用 Firecrawl 上游（费用/配额/波动风险）
- **External**：对“外部已部署网关/环境”的兼容性冒烟（依赖 `FCAM_FC_*`）

---

## 2. 目录与标记策略（方案 A 的落地细则）

### 2.1 目录落地
- 新建目录：
  - `tests/unit/`
  - `tests/integration/`
  - `tests/e2e/`
- 原 `tests/*.py` 逐步迁移到对应目录（支持增量迁移，见第 3 节）。

### 2.2 markers（用于筛选、CI 分组、报告）
即使采用目录分层，仍保留 markers（用于“同目录内的进一步切分”与强制门禁）：
- `unit`
- `integration`
- `e2e`
- `upstream`（真实上游）
- `external`（外部环境）

约束：
- `tests/unit/**`：必须 `@pytest.mark.unit`
- `tests/integration/**`：必须 `@pytest.mark.integration`
- `tests/e2e/**`：必须 `@pytest.mark.e2e`
- 真实上游额外加 `@pytest.mark.upstream`
- 外部环境额外加 `@pytest.mark.external`

---

## 3. 迁移执行计划（增量、安全、可回滚）

### 阶段 0：基线与准备（不改行为）
产出：
- 一份“测试清单映射表”（每个现有测试文件 → 目标分层目录）
- 一套统一的本地执行命令（见第 5 节）

动作：
- 统计当前测试数量、执行耗时、覆盖率基线
- 识别明显属于 `e2e/upstream/external` 的测试文件（先隔离顶层风险）

验收标准：
- 不改任何测试逻辑，只输出清单与命令；全量测试仍可执行

### 阶段 1：pytest 配置单一事实来源（降低漂移风险）
目标：把 pytest 配置收敛到一个位置（推荐 `pyproject.toml`）。

动作：
- 把 `pytest.ini` 中必要配置迁移到 `pyproject.toml`
- 统一 markers 定义（包含 unit/integration/e2e/upstream/external）
- 约束：启用严格 markers（避免拼写错误造成“筛选失效”）

验收标准：
- 本地 `pytest` 与现有行为一致（除“更严格 markers”外）
- 所有 markers 在配置中可见且一致

回滚策略：
- 保留 `pytest.ini`（仅回滚该阶段时使用），待阶段 3 结束再删除

### 阶段 2：建立共享 fixture（降低样板代码与重复）
目标：把重复的“建库/建 app/seed client+key/mock transport”等抽到 `tests/conftest.py`（或分层 conftest）。

动作（建议）：
- `tests/integration/conftest.py`：
  - `app_config(tmp_path)`、`secrets()`、`db_engine()`、`db_session()`、`test_app()`、`client()`
  - `mock_transport(handler)` 与注入 `Forwarder` 的工厂
- `tests/e2e/conftest.py`：
  - 复用/抽象 env file loader（`.env.e2e`）与 server process 管理

验收标准：
- 迁移 1–2 个典型测试文件以验证 fixture 可用
- 测试代码行数下降，且语义更清晰

### 阶段 3：目录分层迁移（核心工作）
目标：将测试文件移动到 `unit/integration/e2e`，并加上对应 marker。

动作：
- 先迁移最容易确定分层的文件：
  - 纯逻辑优先进入 `unit/`
  - 依赖 TestClient/SQLite 的进入 `integration/`
  - 起进程/走 HTTP 的进入 `e2e/`
- 每迁移一个文件：
  - 修正相对路径/导入
  - 补齐 marker
  - 保证 `pytest -q` 可通过

验收标准：
- `pytest -m "unit or integration"` 在默认环境下稳定通过
- `pytest -m e2e` 在 `FCAM_E2E=1` 条件下通过（不启用 upstream）

回滚策略：
- 迁移以小批次进行（每批次 1–3 个文件），任何批次出问题可直接回退该批次移动

### 阶段 4：隔离 Upstream 与 External（让日常链路更稳）
目标：把真实上游与外部环境测试从“普通 e2e”里剥离出来，并提供明确跑法。

动作：
- 对真实上游用例：添加 `@pytest.mark.upstream`，并在测试内保留显式 env gate
- 对外部环境用例：添加 `@pytest.mark.external`
- 补齐文档（第 5 节命令矩阵）

验收标准：
- 默认不设置任何 env 时：`pytest -m "not e2e and not upstream and not external"` 通过
- 只开 `FCAM_E2E=1`：`pytest -m e2e and not upstream and not external` 可跑
- 只在显式开关时：`upstream/external` 才运行

### 阶段 5：建立 CI 执行矩阵（确保金字塔原则长期有效）
目标：把“先快后慢、先稳后不稳”的策略固化到 CI。

动作（建议的 pipeline 结构）：
- PR 必跑：`unit + integration`（含覆盖率门禁）
- 可选（nightly/手动）：`e2e`（本地黑盒、不连真实上游）
- 手动：`upstream`（真实 Firecrawl）
- 手动/专门环境：`external`（对外部部署环境）

验收标准：
- PR 的反馈时间可控且稳定
- 不稳定/有成本的测试不影响 PR merge

### 阶段 6：前端测试补齐（让金字塔覆盖到 UI）
目标：为 `webui/` 建立最小但有效的测试闭环。

动作（低成本优先）：
- 引入 Vitest：对 API 封装、状态存储、关键格式化/校验做少量 unit 测试
- 引入 Playwright：做 1–3 条 UI 冒烟（输入 token、列表页加载、关键按钮可用）

验收标准：
- `npm run type-check` 仍保持为必跑
- 前端测试默认快、稳定；E2E 允许单独 job 执行

---

## 4. 覆盖率与质量指标（可执行口径）

覆盖率不是唯一目标，但需要可度量的底线：
- PR 门禁：保持 `pytest --cov=app --cov-fail-under=80`（当前已在 README/规范中出现）
- 重点模块的“语义覆盖”目标（示例，可按实际调整）：
  - `app/core/forwarder.py`：重试/错误映射/冷却/禁用等分支覆盖
  - `app/core/key_pool.py`：选择策略与边界覆盖
  - `app/middleware.py`：请求限制、request_id、错误包装覆盖
- 对 `upstream/external`：不计入覆盖率门禁（避免不可控波动影响质量门禁）

---

## 5. 统一命令矩阵（本地与 CI 统一口径）

约定：以下命令以 pytest markers 为准，目录只是物理组织。

- 默认（本地/PR）：只跑稳定层
  - `pytest -m "unit or integration" --cov=app --cov-fail-under=80`
- 只跑单元：
  - `pytest -m unit`
- 只跑集成：
  - `pytest -m integration`
- 黑盒 E2E（本地起服务，不连真实上游）：
  - 设置 `FCAM_E2E=1`
  - `pytest -m "e2e and not upstream and not external"`
- 真实上游（有成本风险，手动）：
  - 设置 `FCAM_E2E=1`、`FCAM_E2E_ALLOW_UPSTREAM=1`、`FCAM_E2E_FIRECRAWL_API_KEY=...`
  - `pytest -m upstream`
- 外部环境（手动/专门环境）：
  - 设置 `FCAM_FC_BASE_URL`、`FCAM_FC_CLIENT_TOKEN`（可选 `FCAM_FC_ADMIN_TOKEN`）
  - `pytest -m external`

---

## 6. 风险与对策

- 风险：迁移目录导致 import/相对路径问题、pytest 收集行为改变  
  对策：增量迁移、小批次验证；保留临时兼容期（阶段 1–3）。

- 风险：把真实上游与外部测试隔离后，“看起来覆盖少了”  
  对策：用清晰的命令矩阵与 CI job 解释；并把黑盒 E2E 保留为关键链路回归。

- 风险：过度依赖集成测试，单元测试仍薄  
  对策：阶段 3 同步推进“可单测化重构”（不改变外部行为），把策略/映射提炼为纯函数。

---

## 7. 完成定义（Definition of Done）

当满足以下条件时，认为方案 A 落地完成：
- `tests/` 已按 `unit/integration/e2e` 分层，且每个测试都有正确 marker
- PR 默认只跑 `unit + integration`，并通过覆盖率门禁
- `e2e` 默认不跑，必须 `FCAM_E2E=1` 才跑
- `upstream` 与 `external` 必须显式环境变量才会执行，且不阻塞 PR
- 文档中的命令矩阵与 CI 配置一致、可复用

