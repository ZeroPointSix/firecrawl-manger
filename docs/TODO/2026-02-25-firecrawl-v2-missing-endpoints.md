# TODO：Firecrawl API v2 缺失端点实现

> **PRD**：`docs/PRD/2026-02-25-firecrawl-v2-missing-endpoints.md`
> **FD**：`docs/FD/2026-02-25-firecrawl-v2-missing-endpoints-fd.md`
> **TDD**：`docs/TDD/2026-02-25-firecrawl-v2-missing-endpoints-tdd.md`

---

## 0. 范围与目标

- **目标**：实现 Firecrawl API v2 的 10 个缺失功能性端点（P0 3个 + P1 5个 + P2 2个），确保用户无缝切换
- **核心要求**：请求/响应格式与官方 API 完全一致，所有端点采用简单转发模式
- **非目标**：不添加资源绑定、幂等性等复杂业务逻辑（保持简单）
- **优先级说明**：P0 最高优先级，P1 重要，P2 低优先级但仍在本次范围内

---

## 1. P0：核心功能端点实现（优先级最高）

### 1.1 POST /v2/scrape - 单页抓取

- [ ] 在 `app/api/firecrawl_v2_compat.py` 中添加 scrape 端点
  - 位置：在现有端点之后、通配符路由之前
  - 函数签名：`def scrape(request, payload, client, db)`
  - 设置 `request.state.endpoint = "scrape"`
  - 使用 `_forward_with_fallback` 转发到 `/v2/scrape`
  - 验收：端点可访问，不返回 404，状态码正确，Content-Type 为 application/json，响应体包含上游返回的完整数据

- [ ] 添加 docstring 说明
  - 内容：`Scrape a single URL and optionally extract information using an LLM.`
  - 验收：docstring 清晰描述端点功能

- [ ] 验证路由注册顺序
  - 确保在通配符路由 `/{path:path}` 之前定义
  - 验收：显式端点优先匹配

**预计工作量**：15 分钟

---

### 1.2 POST /v2/search - 搜索

- [ ] 在 `app/api/firecrawl_v2_compat.py` 中添加 search 端点
  - 位置：在 scrape 端点之后
  - 函数签名：`def search(request, payload, client, db)`
  - 设置 `request.state.endpoint = "search"`
  - 使用 `_forward_with_fallback` 转发到 `/v2/search`
  - 验收：端点可访问，不返回 404，响应格式与官方 API 一致

- [ ] 添加 docstring 说明
  - 内容：`Search and optionally scrape search results.`
  - 验收：docstring 清晰描述端点功能

**预计工作量**：10 分钟

---

### 1.3 POST /v2/map - 网站地图

- [ ] 在 `app/api/firecrawl_v2_compat.py` 中添加 map 端点
  - 位置：在 search 端点之后
  - 函数名：`def map_urls(...)` （避免与 Python 内置 map 冲突）
  - 设置 `request.state.endpoint = "map"`
  - 使用 `_forward_with_fallback` 转发到 `/v2/map`
  - 验收：端点可访问，不返回 404，响应格式与官方 API 一致

- [ ] 添加 docstring 说明
  - 内容：`Map multiple URLs based on options.`
  - 验收：docstring 清晰描述端点功能

**预计工作量**：10 分钟

---

## 2. P1：账户管理端点实现（重要）

### 2.1 GET /v2/team/credit-usage - 剩余额度查询

- [ ] 在 `app/api/firecrawl_v2_compat.py` 中添加 team_credit_usage 端点
  - 位置：在 P0 端点之后
  - 函数签名：`def team_credit_usage(request, client, db)`
  - 设置 `request.state.endpoint = "team_credit_usage"`
  - 使用 `_forward_with_fallback` 转发到 `/v2/team/credit-usage`
  - 验收：端点可访问，返回额度信息，响应包含 remainingCredits 等关键字段

- [ ] 添加 docstring 说明
  - 内容：`Get remaining credits for the authenticated team.`
  - 说明返回字段：remainingCredits, planCredits, billingPeriodStart, billingPeriodEnd
  - 验收：docstring 包含返回字段说明

**预计工作量**：10 分钟

---

### 2.2 GET /v2/team/queue-status - 队列状态

- [ ] 在 `app/api/firecrawl_v2_compat.py` 中添加 team_queue_status 端点
  - 位置：在 team_credit_usage 之后
  - 函数签名：`def team_queue_status(request, client, db)`
  - 设置 `request.state.endpoint = "team_queue_status"`
  - 使用 `_forward_with_fallback` 转发到 `/v2/team/queue-status`
  - 验收：端点可访问，返回队列信息，响应包含 jobsInQueue 等关键字段

- [ ] 添加 docstring 说明
  - 内容：`Get metrics about your team's scrape queue.`
  - 说明返回字段：jobsInQueue, activeJobsInQueue, waitingJobsInQueue, maxConcurrency
  - 验收：docstring 包含返回字段说明

**预计工作量**：10 分钟

---

### 2.3 GET /v2/team/credit-usage/historical - 历史额度使用

- [ ] 在 `app/api/firecrawl_v2_compat.py` 中添加 team_credit_usage_historical 端点
  - 位置：在 team_queue_status 之后
  - 函数签名：`def team_credit_usage_historical(request, client, db)`
  - 设置 `request.state.endpoint = "team_credit_usage_historical"`
  - 使用 `_forward_with_fallback` 转发到 `/v2/team/credit-usage/historical`
  - 验收：端点可访问，响应格式与官方 API 一致

- [ ] 添加 docstring 说明
  - 内容：`Get historical credit usage for the authenticated team.`
  - 验收：docstring 清晰

**预计工作量**：8 分钟

---

### 2.4 GET /v2/team/token-usage - Token 使用情况

- [ ] 在 `app/api/firecrawl_v2_compat.py` 中添加 team_token_usage 端点
  - 位置：在 team_credit_usage_historical 之后
  - 函数签名：`def team_token_usage(request, client, db)`
  - 设置 `request.state.endpoint = "team_token_usage"`
  - 使用 `_forward_with_fallback` 转发到 `/v2/team/token-usage`
  - 验收：端点可访问，响应格式与官方 API 一致

- [ ] 添加 docstring 说明
  - 内容：`Get remaining tokens for the authenticated team (Extract only).`
  - 注明：仅适用于 Extract 功能
  - 验收：docstring 包含适用范围说明

**预计工作量**：8 分钟

---

### 2.5 GET /v2/team/token-usage/historical - 历史 Token 使用

- [ ] 在 `app/api/firecrawl_v2_compat.py` 中添加 team_token_usage_historical 端点
  - 位置：在 team_token_usage 之后
  - 函数签名：`def team_token_usage_historical(request, client, db)`
  - 设置 `request.state.endpoint = "team_token_usage_historical"`
  - 使用 `_forward_with_fallback` 转发到 `/v2/team/token-usage/historical`
  - 验收：端点可访问，响应格式与官方 API 一致

- [ ] 添加 docstring 说明
  - 内容：`Get historical token usage for the authenticated team (Extract only).`
  - 验收：docstring 清晰

**预计工作量**：8 分钟

---

## 3. P2：辅助功能端点实现（低优先级）

### 3.1 GET /v2/crawl/active - 活跃爬取任务

- [ ] 在 `app/api/firecrawl_v2_compat.py` 中添加 crawl_active 端点
  - 位置：在 P1 端点之后
  - 函数签名：`def crawl_active(request, client, db)`
  - 设置 `request.state.endpoint = "crawl_active"`
  - 使用 `_forward_with_fallback` 转发到 `/v2/crawl/active`
  - 验收：端点可访问，响应格式与官方 API 一致

- [ ] 添加 docstring 说明
  - 内容：`Get all active crawls for the authenticated team.`
  - 验收：docstring 清晰

**预计工作量**：8 分钟

---

### 3.2 POST /v2/crawl/params-preview - 参数预览

- [ ] 在 `app/api/firecrawl_v2_compat.py` 中添加 crawl_params_preview 端点
  - 位置：在 crawl_active 之后
  - 函数签名：`def crawl_params_preview(request, payload, client, db)`
  - 设置 `request.state.endpoint = "crawl_params_preview"`
  - 使用 `_forward_with_fallback` 转发到 `/v2/crawl/params-preview`
  - 验收：端点可访问，响应格式与官方 API 一致

- [ ] 添加 docstring 说明
  - 内容：`Preview crawl parameters generated from natural language prompt.`
  - 验收：docstring 清晰

**预计工作量**：8 分钟

---

## 4. 代码质量检查

### 4.1 代码风格检查

- [ ] 运行 ruff 检查代码风格
  - 命令：`ruff check app/api/firecrawl_v2_compat.py`
  - 验收：无 linting 错误

- [ ] 运行 ruff 格式化检查
  - 命令：`ruff format --check app/api/firecrawl_v2_compat.py`
  - 验收：代码格式符合规范

**预计工作量**：5 分钟

---

### 4.2 代码审查

- [ ] 检查所有端点的 endpoint 标识设置
  - 验证每个端点都设置了 `request.state.endpoint`
  - 验证标识符命名一致（使用下划线分隔）
  - 验收：所有端点标识正确

- [ ] 检查路由注册顺序
  - 验证所有显式端点在通配符路由之前
  - 验证端点按优先级分组（P0 → P1 → P2）
  - 验收：路由顺序正确

- [ ] 检查函数签名一致性
  - POST 端点：`(request, payload, client, db)`
  - GET 端点：`(request, client, db)`
  - 验收：所有函数签名一致

**预计工作量**：10 分钟

---

## 5. 测试编写

### 5.1 集成测试 - 端点可访问性

- [ ] 创建测试文件 `tests/integration/test_firecrawl_v2_missing_endpoints.py`
  - 验收：文件创建成功

- [ ] 编写 P0 端点可访问性测试（3个）
  - `test_scrape_endpoint_exists`
  - `test_search_endpoint_exists`
  - `test_map_endpoint_exists`
  - 验收：所有测试通过，端点不返回 404，响应格式正确

- [ ] 编写 P1 端点可访问性测试（5个）
  - `test_team_credit_usage_endpoint_exists`
  - `test_team_queue_status_endpoint_exists`
  - `test_team_credit_usage_historical_endpoint_exists`
  - `test_team_token_usage_endpoint_exists`
  - `test_team_token_usage_historical_endpoint_exists`
  - 验收：所有测试通过，响应格式与官方 API 一致

- [ ] 编写 P2 端点可访问性测试（2个）
  - `test_crawl_active_endpoint_exists`
  - `test_crawl_params_preview_endpoint_exists`
  - 验收：所有测试通过

**预计工作量**：30 分钟

---

### 5.2 集成测试 - 鉴权测试

- [ ] 编写参数化鉴权测试
  - `test_endpoints_require_auth`
  - 覆盖所有 10 个端点
  - 验证无 token 时返回 401
  - 验收：所有端点都需要鉴权

- [ ] 编写无效 token 测试
  - `test_invalid_token_returns_401`
  - 验收：无效 token 返回 401

**预计工作量**：15 分钟

---

### 5.3 集成测试 - 转发逻辑

- [ ] 编写 scrape 端点转发测试
  - `test_scrape_forwards_correctly`
  - Mock 上游 API 响应
  - 验证请求正确转发
  - 验收：响应格式正确

- [ ] 编写 team/credit-usage 转发测试
  - `test_team_credit_usage_forwards_correctly`
  - Mock 上游 API 响应
  - 验证响应包含额度信息
  - 验收：响应格式正确

- [ ] 编写查询参数传递测试
  - `test_query_parameters_forwarded`
  - 验证查询参数正确传递到上游
  - 验收：上游请求包含查询参数

- [ ] 编写请求体传递测试
  - `test_request_body_forwarded`
  - 验证请求体完整传递
  - 验收：上游请求包含完整请求体

**预计工作量**：30 分钟

---

### 5.4 集成测试 - 错误处理

- [ ] 编写 401 错误测试
  - `test_401_error_forwarded`
  - Mock 上游 401 响应
  - 验收：错误正确透传

- [ ] 编写 402 错误测试
  - `test_402_payment_required_forwarded`
  - Mock 上游 402 响应
  - 验收：错误正确透传

- [ ] 编写 500 错误测试
  - `test_500_server_error_forwarded`
  - Mock 上游 500 响应
  - 验收：错误正确透传

**预计工作量**：20 分钟

---

### 5.5 E2E 测试（可选）

- [ ] 创建 E2E 测试文件 `tests/e2e/test_e2e_firecrawl_v2_compatibility.py`
  - 验收：文件创建成功

- [ ] 编写 scrape 端点 E2E 测试
  - `test_e2e_scrape_with_real_api`
  - 使用真实 Firecrawl API Key
  - 需要环境变量 `FCAM_E2E_ALLOW_UPSTREAM=1`
  - 验收：真实 API 调用成功

- [ ] 编写 team/credit-usage E2E 测试
  - `test_e2e_team_credit_usage`
  - 验证返回真实额度信息
  - 验收：响应格式与官方 API 一致

**预计工作量**：20 分钟（可选）

---

### 5.6 测试执行与验证

- [ ] 运行所有单元测试
  - 命令：`pytest tests/integration/test_firecrawl_v2_missing_endpoints.py -v`
  - 验收：所有测试通过

- [ ] 检查测试覆盖率
  - 命令：`pytest --cov=app.api.firecrawl_v2_compat --cov-report=html tests/integration/test_firecrawl_v2_missing_endpoints.py`
  - 验收：新增代码覆盖率 = 100%，整体覆盖率 ≥ 80%

- [ ] 运行 E2E 测试- 设置环境变量：`FCAM_E2E=1`, `FCAM_E2E_ALLOW_UPSTREAM=1`
  - 命令：`pytest tests/e2e/test_e2e_firecrawl_v2_compatibility.py -v`
  - 验收：E2E 测试通过（至少 P0 端点）

**预计工作量**：10 分钟

---

## 6. 文档更新

### 6.1 API 使用指南更新

- [ ] 更新 `docs/API-Usage.md`
  - 添加 scrape 端点使用示例
  - 添加 search 端点使用示例
  - 添加 map 端点使用示例
  - 添加 team/credit-usage 使用示例
  - 添加 team/queue-status 使用示例
  - 验收：文档包含所有新端点的使用示例

**预计工作量**：20 分钟

---

### 6.2 API 契约更新

- [ ] 更新 `docs/MVP/Firecrawl-API-Manager-API-Contract.md`
  - 添加 10 个新端点的契约说明
  - 包含请求格式、响应格式、错误码
  - 验收：契约文档完整

**预计工作量**：15 分钟

---

### 6.3 技术文档更新

- [ ] 更新 `docs/agent.md`
  - 在端点列表中添加 10 个新端点
  - 更新端点统计（从 18 个增加到 28 个）
  - 验收：技术文档准确反映当前实现

**预计工作量**：10 分钟

---

## 7. 本地验证

### 7.1 本地服务启动

- [ ] 启动本地服务
  - 命令：`uvicorn app.main:app --reload`
  - 验收：服务正常启动，无错误

- [ ] 检查 Swagger 文档
  - 访问：`http://localhost:8000/docs`
  - 验证所有 10 个新端点显示在 Swagger 中
  - 验收：Swagger 文档包含所有新端点

**预计工作量**：5 分钟

---

### 7.2 手动测试

- [ ] 测试 scrape 端点
  - 使用 curl 或 Postman 发送请求
  - 验证响应格式正确
  - 验收：端点正常工作

- [ ] 测试 team/credit-usage 端点
  - 发送 GET 请求
  - 验证返回额度信息
  - 验收：端点正常工作

- [ ] 测试鉴权
  - 不带 token 发送请求
  - 验证返回 401
  - 验收：鉴权正常工作

**预计工作量**：15 分钟

---

## 8. 部署准备

### 8.1 代码提交

- [ ] 提交代码到 Git
  - 提交信息：`feat: add 10 missing Firecrawl API v2 endpoints`
  - 包含文件：
    - `app/api/firecrawl_v2_compat.py`（修改）
    - `tests/integration/test_firecrawl_v2_missing_endpoints.py`（新增）
    - `docs/API-Usage.md`（修改）
    - `docs/MVP/Firecrawl-API-Manager-API-Contract.md`（修改）
    - `docs/agent.md`（修改）
  - 验收：代码提交成功

**预计工作量**：5 分钟

---

### 8.2 部署验证

- [ ] 部署到测试环境
  - 使用 Docker 构建镜像
  - 部署到测试环境
  - 验收：服务正常运行

- [ ] 测试环境验证
  - 测试所有 10 个新端点
  - 验证与官方 API 的兼容性
  - 验收：所有端点正常工作

- [ ] 监控指标检查
  - 检查新端点的请求量
  - 检查响应时间
  - 检查错误率
  - 验收：指标正常

**预计工作量**：20 分钟

---

## 9. 总结与检查清单

### 9.1 功能完整性检查

- [ ] 所有 10 个端点已实现
  - P0：scrape, search, map ✓
  - P1：5 个 team/* 端点 ✓
  - P2：crawl/active, crawl/params-preview ✓

- [ ] 所有端点设置正确的 endpoint 标识
- [ ] 所有端点需要鉴权
- [ ] 路由注册顺序正确

---

### 9.2 测试完整性检查

- [ ] 单元测试覆盖所有端点（可访问性、鉴权）
- [ ] 集成测试覆盖转发逻辑和错误处理
- [ ] E2E 测试验证无缝切换（可选）
- [ ] 测试覆盖率 ≥ 80%

---

### 9.3 文档完整性检查

- [ ] API 使用指南已更新
- [ ] API 契约已更新
- [ ] 技术文档已更新
- [ ] PRD/FD/TDD 文档已完成

---

### 9.4 部署就绪检查

- [ ] 代码风格检查通过
- [ ] 类型检查通过
- [ ] 所有测试通过
- [ ] 本地验证通过
- [ ] 代码已提交

---

## 10. 预计总工作量

| 阶段 | 任务数 | 预计时间 |
|------|--------|---------|
| P0 端点实现 | 3 | 35 分钟 |
| P1 端点实现 | 5 | 44 分钟 |
| P2 端点实现 | 2 | 16 分钟 |
| 代码质量检查 | 3 | 15 分钟 |
| 测试编写 | 6 | 125 分钟 |
| 文档更新 | 3 | 45 分钟 |
| 本地验证 | 2 | 20 分钟 |
| 部署准备 | 2 | 25 分钟 |
| **总计** | **26** | **约 5 小时** |

**注意**：
- 如果跳过 E2E 测试，可节省约 20 分钟
- 如果已熟悉代码库，实际时间可能更短
- 建议分阶段完成：先 P0，再 P1，最后 P2

---

**TODO 文档编写完成** ✅
