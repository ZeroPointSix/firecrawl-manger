# 测试指南（tests/）

目标：在保证质量与信心的前提下，**尽量把可单元化的逻辑下沉到单测**，把集成/E2E 控制在“少而关键”，避免重复覆盖与测试金字塔倒挂。

## 1. 测试分层（测试金字塔）

- **unit（单元测试）**：快、稳定、隔离。只测纯函数/类方法/规则映射/边界条件；不启动 HTTP server；不访问真实 DB/网络。
- **integration（集成测试）**：测“链路正确性”。覆盖 FastAPI 路由 + 依赖注入 + DB 事务 + 审计/日志写入等组合行为；上游 HTTP 用 `httpx.MockTransport`。
- **e2e（端到端）**：黑盒验证真实部署形态（进程/端口/迁移/探活/关键路径）。默认 **opt-in**，并对真实上游调用做二次门禁。

经验比例（可按阶段调整）：
- unit：~70%
- integration：~25%
- e2e：~5%

## 2. 目录结构与约定

```
tests/
  unit/         # 纯逻辑 / 规则 / helper 的单测
  integration/  # TestClient + SQLite/事务 + MockTransport 的链路测试
  e2e/          # 可选：真实进程/远端环境的黑盒测试
```

约定：
- `tests/unit/`、`tests/integration/`、`tests/e2e/` 均为包（包含 `__init__.py`），避免 pytest 导入阶段的同名模块冲突。
- **不要在测试里“另写一套业务实现”**（例如自建 Enum/Model/函数再去断言），单测应直接覆盖 `app/` 的真实代码；否则会造成“绿灯但业务坏了”的虚假信心。

## 3. markers 与套件语义

markers 定义在 `pyproject.toml` 的 `[tool.pytest.ini_options]`：
- `unit`：快速、隔离单测
- `integration`：集成测试（无真实上游）
- `e2e`：端到端（需 `FCAM_E2E=1`）
- `smoke`：冒烟（用于回归集合的少量集成用例）
- `regression`：回归集合（应当快且稳定）
- `upstream`：真实上游调用（成本/波动；需 `FCAM_E2E_ALLOW_UPSTREAM=1`）
- `external`：外部环境兼容（需要 `FCAM_FC_*` 等环境）
- `admin`：需要管理面 token 的用例

回归集合规则（关键）：
- `tests/conftest.py` 会在收集阶段自动为 **所有 unit** 添加 `regression`；
- 对 **integration**，仅当用例显式标记了 `smoke` 才会被加入 `regression`。

## 4. 推荐运行方式

### 4.1 日常回归（推荐）

```bash
pytest -m regression
```

### 4.2 全量（默认跳过 E2E）

```bash
pytest
```

### 4.3 只跑某一层

```bash
pytest tests/unit
pytest tests/integration
```

### 4.4 E2E（opt-in）

PowerShell：
```powershell
$env:FCAM_E2E="1"
pytest -m e2e
```

允许真实上游（有成本/更不稳定）：
```powershell
$env:FCAM_E2E="1"
$env:FCAM_E2E_ALLOW_UPSTREAM="1"
pytest -m e2e
```

### 4.5 覆盖率门禁（CI/合并前）

```bash
pytest --cov=app --cov-fail-under=80
```

## 5. 如何减少重复测试（实践清单）

按收益从高到低：

1) **把可纯函数化/可规则化的部分下沉到 `app/core/*`，写 unit**  
   - 典型：去重/映射/校验/路径解析/错误细节裁剪等。

2) **集成测试只保留“链路级断言”**  
   - 重点验证：鉴权、事务、落库、审计、关键错误映射；避免在多处重复断言同一规则。

3) **参数化/循环替代复制粘贴**  
   - 同一端点的多 action（enable/disable/delete）优先 `@pytest.mark.parametrize(...)`。

4) **复用 integration 共享 fixture，避免重复造 App/DB**  
   - `tests/integration/conftest.py` 提供 `make_app/make_db/seed_*/*_headers` 等工厂。

5) **测试文件拆分按“业务能力”而非按“实现细节”**  
   - 让失败信息更聚焦，减少跨文件重复准备数据的成本。

## 6. 稳定性与安全

- 上游交互默认使用 `httpx.MockTransport`；禁止在 unit/integration 里打真实网络。
- 日志/错误详情注意脱敏：不得输出 `Authorization`、API key、token 明文。
- Windows 运行稳定性：`tests/conftest.py` 会将 `--basetemp .pytest_tmp` 改写为每次运行唯一子目录，规避 WinError 32/权限抖动。

## 7. 参考资料

- `docs/BUG/2026-02-25-testing-system-pytest-collection-and-test-pyramid.md`：收集冲突与测试金字塔优化的修复记录
- `tests/TEST_SUMMARY.md`：以 “Client 批量管理” 为例的测试下沉/精简案例

