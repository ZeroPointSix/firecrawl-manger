# PRD：清理测试运行中的弃用告警（FastAPI on_event / python-multipart / httpx）

> **创建时间**：2026-02-23  
> **状态**：Draft  
> **优先级**：P1（工程质量/升级风险）  
> **影响范围**：后端 Python（测试输出、CI 可维护性、未来依赖升级）

---

## 1. 背景与问题

在本仓库运行测试（`pytest --cov=app --cov-fail-under=80`）时，当前用例全部通过，但会打印大量 **DeprecationWarning / PendingDeprecationWarning**。

### 1.1 现状（基线）

- **基线时间**：2026-02-23
- **结果**：测试通过，覆盖率门禁通过（≥80%），但产生约 **232 条告警**。
- **影响**：
  - CI 日志噪声过大，掩盖真实问题（真正的警告/错误更难被发现）。
  - 依赖升级时更容易“突然爆炸”（弃用最终会变成 breaking change）。
  - 团队对“告警是否需要处理”的共识容易漂移。

### 1.2 告警类型（按占比/风险排序）

1) **FastAPI `on_event` 弃用**
   - 触发点：`app/main.py` 使用 `@app.on_event("shutdown")`（测试运行时会重复触发并输出告警）。
   - 风险：未来 FastAPI/Starlette 版本可能移除或改变行为；需要迁移到 **lifespan**。

2) **`python-multipart` 模块导入路径弃用（PendingDeprecation）**
   - 表现：Starlette 的 form 解析路径触发 `import multipart` 相关告警，提示改用 `import python_multipart`。
   - 风险：依赖链升级后可能变为硬错误；需要通过 **升级 FastAPI/Starlette** 或 **调整依赖版本策略** 消除。

3) **httpx 原始内容上传参数弃用（Deprecation）**
   - 触发点：`tests/integration/test_middleware.py` 中 `TestClient.post(..., data="x")` 触发告警（建议改为 `content=`）。
   - 风险：未来版本可能移除旧行为；属于低风险、低成本修复项。

---

## 2. 目标（Goals）

- **G1：告警清零**：在默认测试命令 `pytest --cov=app --cov-fail-under=80` 下，Deprecation/PendingDeprecation 告警数量为 **0**（或仅保留明确记录且不可控的第三方告警，并给出短期处理策略）。
- **G2：对齐最佳实践**：将 FastAPI 生命周期管理从 `on_event` 迁移到 **lifespan**，保持行为一致（释放 DB/Redis 资源）。
- **G3：防回归**：建立轻量门禁，避免未来改动再次引入弃用告警（尤其是我们自有代码触发的部分）。

---

## 3. 非目标（Non-goals）

- 不引入与“清理弃用告警”无关的重构/功能改动。
- 不在本 PRD 中推进大版本框架升级（除非为消除 `python-multipart` 告警所必需）。
- 不改变对外 API 契约与错误体语义（以 `docs/agent.md` 与接口契约为准）。

---

## 4. 方案概述（Approach）

### 4.1 FastAPI 生命周期迁移（核心）

- 将 `app/main.py` 中 `@app.on_event("shutdown")` 迁移为 `lifespan`（`FastAPI(lifespan=...)`）。
- 要求：
  - 保持当前 shutdown 行为：DB engine `dispose()`；Redis `close()`。
  - 异常处理保持“尽力释放 + 不阻断 shutdown”的策略（现有逻辑已是 try/except + log）。

### 4.2 消除 `python-multipart` PendingDeprecation（依赖策略）

优先路径：
- **升级 FastAPI/Starlette** 到不再触发该告警的组合（保持 pin 版本，确保可复现）。

备选路径（仅在升级成本过高时采用）：
- 通过 `pytest` 的 warning filter 临时抑制特定第三方告警，并在 TODO 中明确期限与升级计划（避免“永久忽略”）。

### 4.3 修复 httpx `data=` 弃用（低成本）

- 将测试中 `data="x"` 改为 `content="x"`（语义更贴近“原始 body”）。

### 4.4 防回归门禁（推荐）

在告警清零后，新增一个“弱门禁 → 强门禁”的演进策略：
- 第 1 阶段：CI/本地新增检查，统计并展示告警数（只提醒不失败）。
- 第 2 阶段：对 **DeprecationWarning/PendingDeprecationWarning** 设为 error（必要时对白名单第三方告警做临时豁免）。

---

## 5. 验收标准（Definition of Done）

- `pytest --cov=app --cov-fail-under=80`：
  - 测试全通过；
  - 覆盖率门禁通过；
  - **无 Deprecation/PendingDeprecation 告警输出**（或有且仅有已记录豁免项）。
- 资源释放行为不变：服务 shutdown 时能正常释放 DB/Redis（至少保持当前 best-effort 策略）。
- 文档落地：对应 TODO 清单可执行、可验收。

---

## 6. 风险与对策

- **升级依赖引入行为变化**：通过 pin 版本 + 全量测试 + 关键路径集成测试回归降低风险。
- **lifespan 迁移导致释放顺序变化**：在实现中保持现有释放顺序与异常处理策略；必要时补充小型回归测试。
- **第三方告警无法完全清零**：允许短期白名单，但必须在 TODO 中给出“解除豁免”的里程碑。

---

## 7. 回滚策略

- 若升级依赖或 lifespan 改动导致异常：
  - 先回滚到上一组可运行的依赖 pin（`requirements.txt`）；
  - 再回滚 lifespan 相关改动（恢复 `on_event`），确保功能可用后再迭代清理策略。

