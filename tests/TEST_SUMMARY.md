# Client 批量管理功能测试总结（按测试金字塔优化版）

> 目标：把“可单元化的逻辑”尽量下沉到单元测试，集成测试只保留端到端链路（HTTP + DB + 审计 + 事务）验证。

## 相关代码
- 核心逻辑：`app/core/batch_clients.py`（去重 + action 映射）
- API：`PATCH /admin/clients/batch`（见 `app/api/control_plane.py`）

## 测试文件

### 1) 单元测试（更快、更稳定）
**文件**：`tests/unit/test_batch_clients.py`

**覆盖点**：
- ✅ `BatchClientRequest` 入参校验（min/max length、action Enum）
- ✅ 去重保持顺序：`deduplicate_client_ids`
- ✅ action → 状态映射：`apply_batch_action_to_client`（enable/disable/delete）
- ✅ `BatchClientResponse` 结构

### 2) 集成测试（保留必要链路）
**文件**：`tests/integration/test_batch_clients.py`

**覆盖点**：
- ✅ RequestValidationError 被统一转换为 400（本端点校验失败）
- ✅ 鉴权：缺失/无效 Admin Token -> 401
- ✅ enable/disable/delete 三种操作落库正确
- ✅ 部分失败：包含不存在的 client_id 时 `failed_items` 返回且成功项仍落库
- ✅ 事务：commit 抛错时回滚并返回 503
- ✅ 审计日志：批量操作写入 audit logs

## 测试统计（本功能）
- 单元测试：10 个 case
- 集成测试：9 个 case
- 总计：19 个 case

> 说明：本功能仍有进一步“下沉空间”（例如：将 DB 查询/结果组装再拆出 service 层做更细粒度单测），但已移除“测模拟代码不测业务代码”的冗余用例，并显著减少集成测试用例数量。

## 运行测试

### 运行所有批量操作测试
```bash
# 运行集成测试
pytest tests/integration/test_batch_clients.py -v

# 运行单元测试
pytest tests/unit/test_batch_clients.py -v

# 运行所有批量操作测试
pytest tests/integration/test_batch_clients.py tests/unit/test_batch_clients.py -v

# 运行测试并生成覆盖率报告
pytest tests/integration/test_batch_clients.py tests/unit/test_batch_clients.py --cov=app --cov-report=html
```

### 运行特定测试
```bash
# 运行参数验证测试
pytest tests/integration/test_batch_clients.py::test_batch_clients_empty_ids -v

# 运行并发测试
pytest tests/integration/test_batch_clients.py::test_concurrent_batch_enable -v

# 运行幂等性测试
pytest tests/unit/test_batch_clients.py::test_batch_enable_idempotent -v
```

---

## 测试覆盖率目标

### 当前状态
- ⏳ 等待后端 API 实现完成后运行测试
- ⏳ 等待测试覆盖率报告

### 目标
- 整体覆盖率：≥ 80%
- 核心模块覆盖率：≥ 90%
  - `app/api/control_plane.py`（批量操作部分）

---

## 下一步

### 1. 实现后端 API
- [x] 实现 `PATCH /admin/clients/batch` 端点
- [x] 实现批量操作核心逻辑
- [x] 实现事务处理
- [x] 实现审计日志

### 2. 运行测试
- [ ] 运行所有测试用例
- [ ] 修复失败的测试
- [ ] 确保测试覆盖率达标

### 3. 前端测试
- [ ] 编写前端组件测试
- [ ] 编写 E2E 测试

---

## 测试设计原则

### 1. 测试金字塔
- 单元测试（70%）：快速、隔离、专注于逻辑
- 集成测试（25%）：测试完整流程、数据库交互
- E2E 测试（5%）：测试用户完整操作流程

### 2. 测试覆盖
- ✅ 正常场景：功能正常工作
- ✅ 异常场景：错误处理正确
- ✅ 边界场景：边界条件处理
- ✅ 并发场景：数据一致性
- ✅ 性能场景：性能达标

### 3. 测试质量
- ✅ 测试独立：每个测试独立运行
- ✅ 测试清晰：测试意图明确
- ✅ 测试可维护：易于理解和修改
- ✅ 测试快速：快速反馈

---

## 参考文档

- **PRD**：`docs/PRD/2026-02-25-client-batch-operations.md`
- **FD**：`docs/FD/2026-02-25-client-batch-operations-fd.md`
- **TDD**：`docs/TDD/2026-02-25-client-batch-operations-tdd.md`
- **TODO**：`docs/TODO/2026-02-25-client-batch-operations.md`
