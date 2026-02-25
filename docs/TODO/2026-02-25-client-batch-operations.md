# TODO：Client 批量管理功能（MVP）

> **PRD**：`docs/PRD/2026-02-25-client-batch-operations.md`
> **FD**：`docs/FD/2026-02-25-client-batch-operations-fd.md`
> **TDD**：`docs/TDD/2026-02-25-client-batch-operations-tdd.md`

---

## 0. 范围与目标

- **P2 目标**：实现 Client 批量管理功能（批量启用、批量禁用、批量删除），提升管理效率
- **非目标**：批量创建、批量修改配置（rate_limit、quota_limit、concurrency_limit）、批量修改名称和描述

---

## 1. P0：后端批量操作 API

### 1.1 数据模型定义

- [ ] 新增 `BatchAction` 枚举类型（`enable`、`disable`、`delete`）
  - 文件：`app/api/control_plane.py`
  - 验收：枚举类型定义正确，包含三种操作

- [ ] 新增 `BatchClientRequest` 请求模型
  - 文件：`app/api/control_plane.py`
  - 字段：`client_ids: list[int]`（1-100 个）、`action: BatchAction`
  - 验收：Pydantic 模型定义正确，参数验证生效

- [ ] 新增 `BatchClientResponse` 响应模型
  - 文件：`app/api/control_plane.py`
  - 字段：`success_count: int`、`failed_count: int`、`failed_items: list[dict]`
  - 验收：响应模型定义正确，包含成功/失败统计

### 1.2 API 端点实现

- [ ] 新增 `PATCH /admin/clients/batch` 端点
  - 文件：`app/api/control_plane.py`
  - 依赖：`require_admin` 中间件（Admin Token 鉴权）
  - 验收：端点注册成功，Swagger 文档显示正确

- [ ] 实现参数验证逻辑
  - 验证 `client_ids` 不为空
  - 验证 `client_ids` 长度 ≤ 100
  - 验证 `action` 为有效值
  - 验收：参数错误时返回 400，错误信息清晰

### 1.3 业务逻辑实现

- [ ] 实现批量启用逻辑
  - 查询所有目标 Client
  - 设置 `is_active = True`
  - 记录成功/失败结果
  - 验收：所有存在的 Client 状态正确更新

- [ ] 实现批量禁用逻辑
  - 查询所有目标 Client
  - 设置 `is_active = False`
  - 记录成功/失败结果
  - 验收：所有存在的 Client 状态正确更新

- [ ] 实现批量删除逻辑（软删除）
  - 查询所有目标 Client
  - 设置 `is_active = False`（软删除）
  - 记录成功/失败结果
  - 验收：所有存在的 Client 状态正确更新，记录仍存在

- [ ] 实现部分失败处理
  - 记录不存在的 Client ID
  - 记录操作失败的 Client ID 和错误信息
  - 返回详细的失败列表
  - 验收：部分失败时返回正确的统计和失败详情

### 1.4 事务处理

- [ ] 添加数据库事务
  - 使用 SQLAlchemy 事务
  - 批量操作在同一事务中执行
  - 验收：操作失败时正确回滚

- [ ] 实现去重逻辑
  - 对 `client_ids` 去重
  - 避免重复操作同一个 Client
  - 验收：重复 ID 只操作一次

### 1.5 审计日志

- [ ] 记录批量操作到审计日志
  - 记录操作类型（`batch_enable`、`batch_disable`、`batch_delete`）
  - 记录目标 Client ID 列表
  - 记录操作结果（成功数、失败数）
  - 记录操作时间和 request_id
  - 验收：审计日志正确记录所有批量操作

### 1.6 性能优化

- [ ] 使用批量更新语句
  - 使用 `db.query(Client).filter(...).update(...)` 替代逐个更新
  - 减少数据库往返次数
  - 验收：批量操作 100 个 Client 耗时 < 5 秒

---

## 2. P0：前端批量选择与操作 UI

### 2.1 UI 改动

- [ ] 添加复选框列到 Client 列表
  - 文件：`webui/src/views/ClientsKeysView.vue`
  - 使用 Naive UI 的 `n-data-table` 内置复选框功能
  - 配置：`{ type: 'selection' }`
  - 验收：表格显示复选框列，支持单选/多选

- [ ] 添加全选功能
  - 表头复选框用于全选/取消全选
  - 验收：点击表头复选框可全选/取消全选所有 Client

- [ ] 添加选择状态提示
  - 显示"已选择 X 个 Client"
  - 未选择时不显示
  - 验收：选择状态提示正确显示

- [ ] 添加批量操作按钮区域
  - 位置：表格上方
  - 按钮：批量启用、批量禁用、批量删除
  - 验收：按钮区域显示正确，样式符合设计

### 2.2 状态管理

- [ ] 实现选择状态管理
  - 使用 Vue 3 Composition API
  - 状态：`selectedClientIds: Ref<number[]>`
  - 验收：选择状态正确更新

- [ ] 实现按钮启用/禁用逻辑
  - 未选择任何 Client 时，所有批量操作按钮禁用
  - 选择 Client 后，按钮启用
  - 验收：按钮状态正确切换

### 2.3 API 封装

- [ ] 新增批量操作 API 类型定义
  - 文件：`webui/src/api/clients.ts`
  - 类型：`BatchClientRequest`、`BatchClientResponse`
  - 验收：类型定义正确，与后端一致

- [ ] 新增批量操作 API 函数
  - 函数：`batchUpdateClients(payload: BatchClientRequest)`
  - 调用：`http.patch('/admin/clients/batch', payload)`
  - 验收：API 调用成功，返回正确的响应

### 2.4 交互逻辑实现

- [ ] 实现批量启用逻辑
  - 点击"批量启用"按钮
  - 调用批量操作 API
  - 显示成功/失败提示
  - 刷新列表，清空选择
  - 验收：批量启用流程完整，提示正确

- [ ] 实现批量禁用逻辑
  - 点击"批量禁用"按钮
  - 弹出二次确认弹窗
  - 确认后调用批量操作 API
  - 显示成功/失败提示
  - 刷新列表，清空选择
  - 验收：批量禁用流程完整，二次确认生效

- [ ] 实现批量删除逻辑
  - 点击"批量删除"按钮
  - 弹出二次确认弹窗（红色危险按钮）
  - 确认后调用批量操作 API
  - 显示成功/失败提示
  - 刷新列表，清空选择
  - 验收：批量删除流程完整，二次确认生效

- [ ] 实现部分失败处理
  - 显示详细的失败信息（成功 X 个，失败 Y 个）
  - 显示失败的 Client ID 和错误原因
  - 保持失败的 Client 选中状态，便于重试
  - 验收：部分失败时提示清晰，失败项保持选中

- [ ] 实现 Loading 状态
  - 操作进行中显示 Loading
  - 禁用所有批量操作按钮
  - 验收：Loading 状态正确显示，防止重复点击

---

## 3. P0：测试

### 3.1 后端单元测试

- [ ] 测试参数验证
  - `test_batch_clients_empty_ids`：client_ids 为空返回 400
  - `test_batch_clients_invalid_action`：action 无效返回 400
  - `test_batch_clients_exceed_limit`：超过 100 个返回 400
  - 文件：`tests/unit/test_batch_operations.py`
  - 验收：所有参数验证测试通过

- [ ] 测试鉴权
  - `test_batch_clients_no_auth`：未提供 Token 返回 401
  - `test_batch_clients_invalid_auth`：Token 无效返回 401
  - 验收：鉴权测试通过

### 3.2 后端集成测试

- [ ] 测试批量启用
  - `test_batch_enable_clients_success`：全部成功
  - `test_batch_enable_clients_partial_success`：部分成功
  - `test_batch_enable_clients_all_failed`：全部失败
  - 文件：`tests/integration/test_batch_clients.py`
  - 验收：批量启用测试通过，数据库状态正确

- [ ] 测试批量禁用
  - `test_batch_disable_clients_success`：全部成功
  - 验收：批量禁用测试通过，数据库状态正确

- [ ] 测试批量删除
  - `test_batch_delete_clients_success`：全部成功（软删除）
  - 验收：批量删除测试通过，记录仍存在但已禁用

- [ ] 测试边界情况
  - `test_batch_enable_single_client`：操作 1 个 Client
  - `test_batch_enable_max_clients`：操作 100 个 Client
  - `test_batch_enable_duplicate_ids`：重复的 Client ID
  - 验收：边界测试通过

- [ ] 测试并发操作
  - `test_concurrent_batch_enable`：并发批量启用
  - `test_concurrent_batch_delete`：并发批量删除
  - 验收：并发测试通过，数据一致性正确

- [ ] 测试事务回滚
  - `test_batch_rollback_on_error`：数据库错误时回滚
  - 验收：事务测试通过，回滚正确

- [ ] 测试审计日志
  - `test_batch_enable_audit_log`：批量操作记录审计日志
  - 验收：审计日志测试通过

### 3.3 前端组件测试

- [ ] 测试复选框选择
  - 测试单选功能
  - 测试全选功能
  - 文件：`webui/src/views/__tests__/ClientsKeysView.spec.ts`
  - 验收：复选框测试通过

- [ ] 测试批量操作按钮
  - 测试按钮启用/禁用状态
  - 验收：按钮状态测试通过

- [ ] 测试交互逻辑
  - 测试批量禁用的二次确认
  - 测试批量删除的二次确认
  - 测试部分失败处理
  - 验收：交互逻辑测试通过

### 3.4 E2E 测试

- [ ] 测试批量启用流程
  - `test_e2e_batch_enable_flow`：完整的批量启用流程
  - 文件：`tests/e2e/test_batch_clients_e2e.py`
  - 验收：E2E 测试通过

- [ ] 测试批量删除流程
  - `test_e2e_batch_delete_flow`：完整的批量删除流程
  - 验收：E2E 测试通过

### 3.5 测试覆盖率

- [ ] 确保测试覆盖率 ≥ 80%
  - 运行：`pytest --cov=app --cov-fail-under=80`
  - 验收：覆盖率达标

- [ ] 确保核心模块覆盖率 ≥ 90%
  - 核心模块：`app/api/control_plane.py`（批量操作部分）
  - 验收：核心模块覆盖率达标

---

## 4. P1：文档与发布

### 4.1 API 文档更新

- [ ] 更新 API 契约文档
  - 文件：`docs/MVP/Firecrawl-API-Manager-API-Contract.md`
  - 添加 `PATCH /admin/clients/batch` 端点说明
  - 添加请求/响应示例
  - 验收：API 契约文档更新完整

### 4.2 使用指南更新

- [ ] 更新 API 使用指南
  - 文件：`docs/API-Usage.md`
  - 添加批量操作使用示例
  - 添加常见问题解答
  - 验收：使用指南更新完整

### 4.3 变更日志更新

- [ ] 更新变更日志
  - 文件：`docs/WORKLOG.md`
  - 记录批量管理功能的添加
  - 记录 API 变更
  - 验收：变更日志更新完整

### 4.4 前端构建

- [ ] 构建前端静态文件
  - 命令：`cd webui && npm run build`
  - 输出：`app/ui2/`
  - 验收：前端构建成功，静态文件生成

---

## 5. 验收清单

### 5.1 功能验收

- [ ] Client 列表显示复选框列
- [ ] 支持单选、多选、全选
- [ ] 选中 Client 后显示"已选择 X 个 Client"
- [ ] 批量启用按钮可用，点击后成功启用
- [ ] 批量禁用按钮可用，点击后弹出确认弹窗，确认后成功禁用
- [ ] 批量删除按钮可用，点击后弹出确认弹窗，确认后成功删除
- [ ] 操作完成后清空选择状态，刷新列表
- [ ] 部分失败时显示详细提示，失败的 Client 保持选中状态

### 5.2 性能验收

- [ ] 批量操作 100 个 Client 耗时 < 5 秒
- [ ]不一致
- [ ] 数据库事务正确回滚

### 5.3 测试验收

- [ ] 所有单元测试通过
- [ ] 所有集成测试通过
- [ ] 所有 E2E 测试通过
- [ ] 测试覆盖率 ≥ 80%
- [ ] 核心模块覆盖率 ≥ 90%

### 5.4 文档验收

- [ ] API 契约文档更新完整
- [ ] API 使用指南更新完整
- [ ] 变更日志更新完整

---

## 6. 实施计划

### 6.1 第一阶段：后端 API（预计 1-2 天）

**Day 1**：
- 数据模型定义
- API 端点实现
- 参数验证
- 业务逻辑实现（批量启用、禁用、删除）

**Day 2**：
- 事务处理
- 审计日志
- 性能优化
- 单元测试
- 集成测试

### 6.2 第二阶段：前端 UI（预计 1-2 天）

**Day 3**：
- UI 改动（复选框、按钮）
- 状态管理
- API 封装

**Day 4**：
- 交互逻辑实现
- 部分失败处理
- Loading 状态
- 组件测试

### 6.3 第三阶段：测试与文档（预计 1 天）

**Day 5**：
- E2E 测试
- 测试覆盖率检查
- 文档更新
- 前端构建
- 最终验收

---

## 7. 风险与对策

### 7.1 技术风险

**风险 1：并发操作导致数据不一致**
- 对策：使用数据库事务和行锁
- 验收：并发测试通过

**风险 2：批量操作性能问题**
- 对策：使用批量更新语句，限制操作数量（最多 100 个）
- 验收：性能测试通过

**风险 3：部分失败处理复杂**
- 对策：详细记录失败信息，保持失败项选中状态
- 验收：部分失败测试通过

### 7.2 进度风险

**风险：开发时间超出预期**
- 对策：优先实现核心功能（批量启用、禁用、删除），性能优化和审计日志可后续补充
- 验收：核心功能按时完成

---

## 8. 后续版本规划

### 8.1 第二版：批量修改配置

- [ ] 批量修改 `rate_limit`
- [ ] 批量修改 `quota_limit`
- [ ] 批量修改 `concurrency_limit`
- [ ] 批量修改配置的 UI 和 API

### 8.2 第三版：批量导出/导入

- [ ] 批量导出 Client 配置（JSON/CSV）
- [ ] 批量导入 Client 配置
- [ ] 导入/导出的 UI 和 API

---

## 9. 参考资料

- **PRD**：`docs/PRD/2026-02-25-client-batch-operations.md`
- **FD**：`docs/FD/2026-02-25-client-batch-operations-fd.md`
- **TDD**：`docs/TDD/2026-02-25-client-batch-operations-tdd.md`
- **API 契约**：`docs/MVP/Firecrawl-API-Manager-API-Contract.md`
- **数据模型**：`app/db/models.py`
- **控制面 API**：`app/api/control_plane.py`
- **前端组件**：`webui/src/views/ClientsKeysView.vue`
- **前端 API**：`webui/src/api/clients.ts`
