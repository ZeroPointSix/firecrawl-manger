# 测试体系缺陷：pytest 收集冲突 + 测试金字塔倒挂（修复记录与后续计划）

> **创建时间**: 2026-02-25  
> **优先级**: P0（当时会阻塞测试执行）+ P1（长期拖慢反馈/维护成本）  
> **影响范围**: 本地/CI 测试可执行性、回归速度、测试可信度（是否覆盖真实业务代码）

---

## 1. 问题概述

近期在梳理测试体系时，发现并确认了两类核心问题：

1) **pytest 收集失败（P0）**  
`tests/integration/test_batch_clients.py` 与 `tests/unit/test_batch_clients.py` 文件同名，且子目录未声明为包，导致 pytest 在导入阶段发生模块名冲突，直接中断测试收集，进而阻塞全量测试运行。

2) **测试金字塔倒挂/重复覆盖（P1）**  
部分“单元测试”通过在测试文件内自建 Enum / Pydantic Model / 业务函数来断言行为，**并未覆盖 `app/` 真实实现**，造成：
- 覆盖率与信心虚高（测的是测试里写的“另一套实现”）
- 与集成测试形成重复（同一规则多处重复验证）
- 集成测试数量膨胀、执行时间变长

此外还存在一个“套件定义”问题：

3) **`regression` 标记语义不符合预期（P2）**  
`regression` 在仓库约定中应当“快且稳定”，但此前实现会将所有 `unit + integration` 默认纳入回归，导致日常回归不够轻量。

---

## 2. 影响与症状

### 2.1 pytest 收集阶段直接失败（P0）

典型复现命令：
```bash
.\.venv\Scripts\pytest.exe -q --collect-only
```

在修复前会出现 `import file mismatch`，并提示 “imported module … is not the same as the test file we want to collect”。

### 2.2 测试金字塔比例失衡（P1）

修复前（2026-02-25 初）收集统计快照：
- unit: 59
- integration: 174
- e2e: 42
- total: 275

`integration` 占比过高，日常反馈链路更容易被拖慢，也更难维护。

修复后（2026-02-25）收集统计快照：
- unit: 67
- integration: 119
- e2e: 42
- total: 228

---

## 3. 根因分析

### 3.1 pytest 收集失败的根因（P0）

- `tests/__init__.py` 使 `tests` 成为可导入包；
- 但 `tests/unit/` 与 `tests/integration/` 子目录缺少 `__init__.py`；
- 在 pytest 收集导入时，两份 `test_batch_clients.py` 可能以相同模块名被导入/缓存，导致后续收集到另一份同名文件时发生不一致并报错。

### 3.2 “伪单测”的根因（P1）

- 测试里“模拟一套业务对象/逻辑”，可写得很快，但失去了对真实业务代码的约束；
- 当 API/模型/错误策略变更时，测试仍可能绿灯，从而误导回归判断；
- 同时集成测试仍在覆盖真实链路，形成重复与倒挂。

---

## 4. 修复方案与落地结果（已完成）

### 4.1 修复 pytest 收集冲突（P0）

做法：为分层目录补齐包结构，确保模块全名唯一。

- `tests/unit/__init__.py`
- `tests/integration/__init__.py`
- `tests/e2e/__init__.py`

验收：`.\.venv\Scripts\pytest.exe -q --collect-only` 可正常完成收集。

### 4.2 将可单元化逻辑下沉到 `app/core`（P1）

新增核心逻辑模块：
- `app/core/batch_clients.py`
  - `deduplicate_client_ids(...)`：去重且保持顺序（避免 `set()` 乱序导致 failed_items 不稳定）
  - `apply_batch_action_to_client(...)`：enable/disable/delete 的状态映射（与 API 层 Enum 解耦）

控制面端点改为复用核心逻辑：
- `app/api/control_plane.py`（`PATCH /admin/clients/batch`）

### 4.3 合并/精简 batch client 的测试覆盖（P1）

- 重写单元测试：`tests/unit/test_batch_clients.py`  
  直接测试真实 `BatchClientRequest/Response` 与 `app/core/batch_clients.py` 中的逻辑。
- 精简集成测试：`tests/integration/test_batch_clients.py`  
  只保留链路级验证（鉴权、落库、部分失败、事务回滚、审计日志），并用参数化覆盖 enable/disable/delete。

### 4.4 让 `regression` 真的“快且稳定”（P2）

调整回归套件选择策略：
- `tests/conftest.py`：默认只给 `unit` 添加 `regression`；
- `integration` 仅对显式标记为 `smoke` 的用例添加 `regression`（当前为 `tests/integration/test_smoke.py`）。

验收：
```bash
.\.venv\Scripts\pytest.exe -q -m regression
```
应为“秒级反馈”的稳定回归集合。

### 4.5 合并重复的集成用例、补齐 unit 覆盖（P1）

做法：
- 将 `tests/integration/test_api_data_plane.py` 中高度重复的参数化（转发路径 + 429/5xx/timeout passthrough）
  合并为更少的循环式链路测试，减少重复的建库/建 app 开销。
- 将 `tests/integration/test_firecrawl_v2_missing_endpoints.py` 从“端点存在/转发/日志/参数传递”等大量重复断言，
  精简为 3 个更有信息量的测试：鉴权、转发（含 query）、request_logs.endpoint 显式写入。
- 补充 `tests/unit/test_middleware_helpers.py` 覆盖 `_infer_api_endpoint` 与 `_dump_error_details`，
  并修复 `_dump_error_details` 在超长 message 场景下无法保证长度上限的问题，避免 request_logs.error_details 过大。

验收：
```bash
.\.venv\Scripts\pytest.exe -q --collect-only
.\.venv\Scripts\pytest.exe -q -m regression
.\.venv\Scripts\pytest.exe -q
```

### 4.6 修复 Alembic 迁移触发的日志捕获抖动（P2）

现象：在执行部分 integration（尤其是 migration 相关）后再运行带 `caplog` 的 unit，用例会出现顺序相关失败，
表现为 logger 被意外 `disabled`，从而无法捕获期望的 warning。

根因：`migrations/env.py` 使用 `fileConfig(...)` 默认行为会 `disable_existing_loggers=True`，
导致 `app.*` logger 在同一 pytest session 内被禁用。

修复：
- `migrations/env.py`：改为 `fileConfig(..., disable_existing_loggers=False)`，避免禁用现有 logger。

验收：
```bash
.\.venv\Scripts\pytest.exe -q tests\integration tests\unit\test_resource_binding.py::test_bind_resource_conflict_different_key_keeps_existing_and_logs_warning
```

### 4.7 建立 integration 共享 fixture（P1，对应 6.2 第一阶段落地）

做法：
- 新增 `tests/integration/conftest.py`，提供 `make_app/make_db/admin_headers/client_headers/seed_*` 等共享工厂与样板。
- 重构重点 integration 测试文件以复用共享 fixture，减少重复的 `AppConfig/Secrets/create_app/Base.metadata.create_all`。
  - `tests/integration/test_admin_control_plane.py`
  - `tests/integration/test_forwarder.py`
  - `tests/integration/test_middleware.py`
  - `tests/integration/test_readyz.py`

---

## 5. 验收口径（建议固化到团队习惯）

### 5.1 日常回归（推荐）
```bash
.\.venv\Scripts\pytest.exe -q -m regression
```

### 5.2 全量测试（E2E 默认门禁跳过）
```bash
.\.venv\Scripts\pytest.exe -q
```

---

## 6. 后续优化计划（未完成，建议按收益排序）

### 6.1 将 “integration 里可纯函数化的规则”继续下沉到 unit（高收益）

候选方向（优先挑最大文件/最高频变更点）：
- `tests/integration/test_api_data_plane.py`：把路径规范化、错误映射、限流/并发/配额决策拆到 `app/core/*` 后做 unit；
- `tests/integration/test_forwarder.py`：继续把纯字符串/头处理、重试策略判定下沉到 unit；

目标：让“多数规则变更”只需跑 unit 就能得到反馈。

### 6.2 建立 integration 共享 fixture（中收益）

第一阶段已完成（见 4.7）。下一步是继续把其余 integration 文件逐步迁移到共享 fixture，避免重复造轮子。

### 6.3 给关键场景建立少量 E2E（低频但高价值）

保持 E2E “少而关键”，用于验证真实部署形态（进程/端口/迁移/探活）。
