# TODO：清理测试运行中的弃用告警（FastAPI lifespan / python-multipart / httpx）（P1）

> **PRD**：`docs/PRD/2026-02-23-deprecation-warnings-cleanup.md`

---

## 0. 范围与目标

- 目标：在 `pytest --cov=app --cov-fail-under=80` 下，Deprecation/PendingDeprecation 告警 **清零**（或仅保留短期豁免项并记录到 PRD/TODO）。
- 范围：后端 Python（FastAPI/Starlette/httpx 相关用法与依赖 pin）。

---

## 1. 基线与度量（先确定“清零”口径）

- [ ] 固化基线：记录当前告警类型与触发点（以 2026-02-23 跑测输出为准，约 232 条）。
- [ ] 增加一个可重复的本地命令（或脚本）用于复测“告警数”（避免口口相传导致漂移）。

---

## 2. 处理 FastAPI `on_event` 弃用（核心）

- [x] 将 `app/main.py` 的 `@app.on_event("shutdown")` 迁移为 lifespan（保持资源释放行为一致）。
- [x] 跑全量测试：`pytest --cov=app --cov-fail-under=80`，确认告警数量显著下降/清零。

---

## 3. 处理 `python-multipart` PendingDeprecation（依赖策略）

- [x] 评估并选择方案：
  - [x] 优先：升级 FastAPI/Starlette（保持 pin 版本）以消除告警
  - [ ] 备选：短期 warning filter + 明确解除豁免的里程碑
- [x] 更新 `requirements.txt`（如需要）并跑全量测试验证兼容性。

---

## 4. 处理 httpx `data=` 弃用（低成本）

- [x] 修改触发点测试：将 `TestClient.post(..., data="x")` 改为 `content="x"`（不改变断言语义）。
- [x] 跑相关测试文件/全量测试确认告警消失。

---

## 5. 防回归（门禁）

- [x] 在告警清零后，增加一个“告警门禁”策略（弱门禁→强门禁逐步演进）：
  - [ ] 阶段 1：统计展示（不失败）
  - [x] 阶段 2：Deprecation/PendingDeprecation 视为 error（必要时维护短期白名单）

---

## 6. 验收清单

- [x] `pytest --cov=app --cov-fail-under=80`：通过且无弃用告警
- [x] 关键行为不变：shutdown 能释放 DB/Redis（best-effort）
- [x] 文档更新：在 PRD/TODO 中记录“做了什么、如何验证”
