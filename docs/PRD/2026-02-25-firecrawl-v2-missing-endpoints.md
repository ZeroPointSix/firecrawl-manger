# PRD：Firecrawl API v2 缺失端点实现

> **创建时间**：2026-02-25
> **状态**：Draft
> **优先级**：P0（核心功能完整性）
> **影响范围**：后端数据面 API（`app/api/firecrawl_v2_compat.py`）

---

## 1. 背景与问题

### 1.1 现状

当前项目已实现 Firecrawl API v2 的大部分端点，包括：
- ✅ Crawl 端点（6/6）：crawl, crawl status, crawl errors, crawl delete
- ✅ Batch Scrape 端点（5/5）：batch scrape, batch status, batch errors, batch delete
- ✅ Browser 端点（4/4）：browser create, browser execute, browser list, browser delete
- ✅ Agent 端点（3/3）：agent, agent status, agent delete
- ✅ Extract 端点（2/2）：extract, extract status
- ✅ 通配符路由：`GET/POST/DELETE /{path:path}` 可以转发未显式定义的端点

### 1.2 问题

根据 Firecrawl 官方 API 文档（`api-reference/firecrawl-docs/api-reference/v2-openapi.json`），以下 **10 个功能性端点** 缺少显式实现：

**P0 核心功能（3个）：**
1. `POST /v2/scrape` - 单页抓取（最常用的基础功能）
2. `POST /v2/search` - 搜索并抓取搜索结果
3. `POST /v2/map` - 网站地图生成

**P1 账户管理（5个）：**
4. `GET /v2/team/credit-usage` - 剩余额度查询
5. `GET /v2/team/queue-status` - 队列状态查询
6. `GET /v2/team/credit-usage/historical` - 历史额度使用
7. `GET /v2/team/token-usage` - Token 使用情况
8. `GET /v2/team/token-usage/historical` - 历史 Token 使用

**P2 辅助功能（2个）：**
9. `GET /v2/crawl/active` - 获取活跃爬取任务
10. `POST /v2/crawl/params-preview` - 爬取参数预览

### 1.3 影响

虽然通配符路由可以转发这些请求，但缺少显式实现会导致：
- ❌ 审计日志中 `endpoint` 字段不准确（显示为通配符路径）
- ❌ 代码可读性差，维护困难
- ❌ 无法针对特定端点添加特殊处理（如监控、限流等）
- ❌ 用户无法确认这些端点是否被正式支持

### 1.4 用户需求

> "我们需要确保 Firecrawl API v2 的所有核心功能都能无缝使用，特别是 scrape 和 search 这两个最常用的端点。"

> "希望能在管理面板中看到剩余额度和队列状态，方便监控配额使用情况。"

---

## 2. 目标（Goals）

- **G1：API 完整性**：显式实现所有 Firecrawl API v2 功能性端点，确保用户无缝切换
- **G2：审计可追溯**：每个端点设置正确的 `endpoint` 标识，便于日志分析和监控
- **G3：代码可维护**：显式定义端点，提升代码可读性和维护性
- **G4：功能对齐**：与 Firecrawl 官方 API 保持 100% 兼容

---

## 3. 非目标（Non-goals）

- **不添加额外业务逻辑**：这些端点采用简单转发模式，不添加资源绑定、幂等性等复杂逻辑
- **不修改现有端点**：仅新增缺失端点，不影响已有功 **不实现 V1 特有端点**：`/deep-research` 和 `/llmstxt` 是 V1 实验性功能，暂不实现

---

## 4. 方案概述（Approach）

### 4.1 技术方案

#### 4.1.1 实现模式

根据对现有代码的分析（`app/api/firecrawl_v2_compat.py`），项目中存在以下实现模式：

| 模式 | 适用场景 | 特殊处理 | 示例端点 |
|------|---------|---------|---------|
| **异步任务 + 资源绑定** | 返回 job_id 的 POST 端点 | `_maybe_bind_created_resource` | crawl, agent, batch |
| **异步任务 + 幂等性** | 长时间运行的任务 | `idempotency_start_or_replay` | crawl, agent |
| **查询绑定资源** | GET {id} 端点 | `lookup_bound_key_id` | GET /crawl/{id} |
| **简单转发** | 同步返回结果的端点 | 无 | 通配符路由 |

**根据 OpenAPI 规范分析，所有 10 个缺失端点都是同步返回结果，采用"简单转发"模式。**

#### 4.1.2 实现细节

**模式 1：同步 POST 端点**（scrape, search, map, params-preview）

```python
@router.post("/scrape", dependencies=[Depends(enforce_client_governance)])
def scrape(
    request: Request,
    payload: Any = Body(...),
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    request.state.endpoint = "scrape"  # 设置审计日志标识
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="POST",
        primary_path=_with_query(request, "/v2/scrape"),
        fallback_path=None,
        payload=payload,
    )
```

**模式 2：简单 GET 端点**（team/*, crawl/active）

```python
@router.get("/team/credit-usage", dependencies=[Depends(enforce_client_governance)])
def team_credit_usage(
    request: Request,
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    request.state.endpoint = "team_credit_usage"  # 设置审计日志标识
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="GET",
        primary_path=_with_query(request, "/v2/team/credit-usage"),
        fallback_path=None,
        payload=None,
    )
```

#### 4.1.3 代码位置

- **文件**：`app/api/firecrawl_v2_compat.py`
- **位置**：在现有端点之后、通配符路由之前插入
- **顺序**：按优先级排列（P0 → P1 → P2）

### 4.2 端点详细说明

#### 4.2.1 P0 核心功能端点

##### 1. POST /v2/scrape - 单页抓取

**功能描述**：
- 抓取单个 URL 并可选使用 LLM 提取信息
- 同步返回抓取结果（markdown、html、screenshot 等格式）

**请求示例**：
```json
{
  "url": "https://example.com",
  "formats": ["markdown", "html"]
}
```

**响应示例**：
```json
{
  "success": true,
  "data": {
    "markdown": "# Example Domain...",
    "html": "<html>...</html>",
    "metadata": {
      "title": "Example Domain",
      "description": "..."
    }
  }
}
```

**参考文档**：
- OpenAPI 规范：`api-reference/firecrawl-docs/api-reference/v2-openapi.json` → `/scrape`
- 官方文档：https://docs.firecrawl.dev/api-reference/endpoint/scrape

---

##### 2. POST /v2/search - 搜索

**功能描述**：
- 搜索并可选抓取搜索结果
- 同步返回搜索结果和抓取内容

**请求示例**：
```json
{
  "query": "firecrawl api",
  "limit": 5
}
```

**响应示例**：
```json
{
  "success": true,
  "data": [
    {
      "url": "https://example.com",
      "title": "Firecrawl API",
      "markdown": "..."
    }
  ]
}
```

**参考文档**：
- OpenAPI 规范：`api-reference/firecrawl-docs/api-reference/v2-openapi.json` → `/search`
- 官方文档：https://docs.firecrawl.dev/api-reference/endpoint/search

---

##### 3. POST /v2/map - 网站地图

**功能描述**：
- 基于选项映射多个 URL，快速获取网站结构
- 同步返回 URL 列表

**请求示例**：
```json
{
  "url": "https://example.com",
  "search": "documentation"
}
```

**响应示例**：
```json
{
  "success": true,
  "links": [
    "https://example.com/docs",
    "https://example.com/api"
  ]
}
```

**参考文档**：
- OpenAPI 规范：`api-reference/firecrawl-docs/api-reference/v2-openapi.json` → `/map`
- 官方文档：https://docs.firecrawl.dev/api-reference/endpoin---

#### 4.2.2 P1 账户管理端点

##### 4. GET /v2/team/credit-usage - 剩余额度查询

**功能描述**：
- 获取团队剩余的 credits
- 返回剩余额度、计划额度、计费周期等信息

**响应示例**：
```json
{
  "success": true,
  "data": {
    "remainingCredits": 1000,
    "planCredits": 500000,
    "billingPeriodStart": "2025-01-01T00:00:00Z",
    "billingPeriodEnd": "2025-01-31T23:59:59Z"
  }
}
```

**使用场景**：
- 用户在发起大量请求前检查额度
- 管理面板显示额度使用情况
- 自动化脚本根据额度决定是否继续

**参考文档**：
- OpenAPI 规范：`api-reference/firecrawl-docs/api-reference/v2-openapi.json` → `/team/credit-usage`
- 官方文档：https://docs.firecrawl.dev/api-reference/endpoint/credit-usage

---

##### 5. GET /v2/team/queue-status - 队列状态

**功能描述**：
- 获取团队的抓取队列指标
- 返回队列任务数、活跃任务数、最大并发数等

**响应示例**：
```json
{
  "success": true,
  "jobsInQueue": 10,
  "activeJobsInQueue": 5,
  "waitingJobsInQueue": 5,
  "maxConcurrency": 10,
  "mostRecentSuccess": "2025-01-15T10:30:00Z"
}
```

**使用场景**：
- 了解当前任务负载
- 判断是否需要等待队列空闲
- 监控服务健康状态

**参考文档**：
- OpenAPI 规范：`api-reference/firecrawl-docs/api-reference/v2-openapi.json` → `/team/queue-status`
- 官方文档：https://docs.firecrawl.dev/api-reference/endpoint/queue-status

---

##### 6. GET /v2/team/credit-usage/historical - 历史额度使用

**功能描述**：
- 获取历史额度使用情况
- 按月统计，可选按 API Key 分组

**参考文档**：
- OpenAPI 规范：`api-reference/firecrawl-docs/api-reference/v2-openapi.json` → `/team/credit-usage/historical`
- 官方文档：https://docs.firecrawl.dev/api-reference/endpoint/credit-usage-historical

---

##### 7. GET /v2/team/token-usage - Token 使用情况

**功能描述**：
- 获取剩余 tokens（仅 Extract 功能）
- 返回剩余 token 数量

**参考文档**：
- OpenAPI 规范：`api-reference/firecrawl-docs/api-reference/v2-openapi.json` → `/team/token-usage`
- 官方文档：https://docs.firecrawl.dev/api-reference/endpoint/token-usage

---

##### 8. GET /v2/team/token-usage/historical - 历史 Token 使用

**功能描述**：
- 获取历史 token 使用情况
- 按月统计

**参考文档**：
- OpenAPI 规范：`api-reference/firecrawl-docs/api-reference/v2-openapi.json` → `/team/token-usage/historical`
- 官方文档：https://docs.firecrawl.dev/api-reference/endpoint/token-usage-historical

---

#### 4.2.3 P2 辅助功能端点

##### 9. GET /v2/crawl/active - 活跃爬取任务

**功能描述**：
- 获取当前团队所有活跃的爬取任务
- 返回任务列表

**参考文档**：
- OpenAPI 规范：`api-reference/firecrawl-docs/api-reference/v2-openapi.json` → `/crawl/active`
- 官方文档：https://docs.firecrawl.dev/api-reference/endpoint/crawl-active

---

##### 10. POST /v2/crawl/params-preview - 参数预览

**功能描述**：
- 从自然语言提示生成爬取参数预览
- 帮助用户理解爬取参数

**参考文档**：
- OpenAPI 规范：`api-reference/firecrawl-docs/api-reference/v2-openapi.json` → `/crawl/params-preview`
- 官方文档：https://docs.firecrawl.dev/api-reference/endpoint/crawl-params-preview

---

## 5. 实施计划（Implementation Plan）

### 5.1 开发阶段

#### 阶段 1：P0 核心功能（优先级最高）

**任务**：
- [ ] 实现 `POST /v2/scrape`
- [ ] 实现 `POST /v2/search`
- [ ] 实现 `POST /v2/map`

**预计工作量**：1 小时
**验收标准**：
- 端点可正常调用，返回与官方 API 一致的响应
- 审计日志中 `endpoint` 字段正确
- 通过集成测试

---

#### 阶段 2：P1 账户管理（重要）

**任务**：
- [ ] 实现 `GET /v2/team/credit-usage`
- [ ] 实现 `GET /v2/team/queue-status`
- [ ] 实现 `GET /v2/team/credit-usage/historical`
- [ ] 实现 `GET /v2/team/token-usage`
- [ ] 实现 `GET /v2/team/token-usage/historical`

**预计工作量**：1 小时
**验收标准**：
- 端点可正常调用，返回与官方 API 一致的响应
- 审计日志中 `endpoint` 字段正确

---

#### 阶段 3：P2 辅助功能（可选）

**任务**：
- [ ] 实现 `GET /v2/crawl/active`
- [ ] 实现 `POST /v2/crawl/params-preview`

**预计工作量**：30 分钟
**验收标准**：
- 端点可正常调用，返回与官方 API 一致的响应

---

### 5.2 测试阶段

#### 5.2.1 单元测试

**测试文件**：`tests/integration/test_firecrawl_v2_missing_endpoints.py`

**测试用例**：
- [ ] 测试 scrape 端点正常调用
- [ ] 测试 search 端点正常调用
- [ ] 测试 map 端点正常调用
- [ ] 测试 team/credit-usage 端点正常调用
- [ ] 测试 team/queue-sta点正常调用
- [ ] 测试审计日志中 endpoint 字段正确

#### 5.2.2 E2E 测试

**测试场景**：
- [ ] 使用真实 Firecrawl API Key 调用 scrape 端点
- [ ] 验证响应格式与官方 API 一致
- [ ] 验证错误处理（401、429、500 等）

---

## 6. 验收标准（Acceptance Criteria）

### 6.1 功能验收

- [ ] 所有 10 个端点均可正常调用
- [ ] 响应格式与 Firecrawl 官方 API 完全一致
- [ ] 支持所有请求参数和查询参数
- [ ] 错误响应与官方 API 一致

### 6.2 代码质量

- [ ] 代码风格符合项目规范（通过 ruff 检查）
- [ ] 所有端点设置正确的 `request.state.endpoint`
- [ ] 代码注释清晰，便于维护

### 6.3 测试覆盖

- [ ] 集成测试覆盖所有新增端点
- [ ] 测试覆盖率不低于 80%
- [ ] E2E 测试验证与官方 API 的兼容性

### 6.4 文档更新

- [ ] 更新 `docs/API-Usage.md`，添加新端点的使用示例
- [ ] 更新 `docs/agent.md`，补充端点列表
- [ ] 更新 `docs/MVP/Firecrawl-API-Manager-API-Contract.md`，添加新端点的契约说明

---

## 7. 风险与依赖（Risks & Dependencies）

### 7.1 风险

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| Firecrawl API 变更 | 端点行为不一致 | 定期同步官方 OpenAPI 规范 |
| 通配符路由冲突 | 路由优先级问题 | 显式端点定义在通配符之前 |

### 7.2 依赖

- **外部依赖**：Firecrawl 官方 API 稳定性
- **内部依赖**：现有转发逻辑（`_forward_with_fallback`）正常工作

---

## 8. 参考文档（References）

### 8.1 官方文档

- [Firecrawl API v2 Introduction](https://docs.firecrawl.dev/api-reference/v2-introduction)
- [Firecrawl API Reference](https://docs.firecrawl.dev/api-reference/endpoint/scrape)

### 8.2 项目文档

- **技术方案*cs/agent.md` - 架构设计与技术方案
- **API 契约**：`docs/MVP/Firecrawl-API-Manager-API-Contract.md` - API 接口契约
- **API 使用指南**：`docs/API-Usage.md` - API 使用示例
- **OpenAPI 规范**：`api-reference/firecrawl-docs/api-reference/v2-openapi.json` - 官方 API 规范

### 8.3 代码参考

- **现有实现**：`app/api/firecrawl_v2_compat.py` - V2 兼容层实现
- **转发逻辑**：`app/core/forwarder.py` - 核心转发逻辑
- **测试用例**：`tests/integration/test_firecrawl_v2_openapi_alignment.py` - V2 API 对齐测试

---

## 9. 附录（Appendix）

### 9.1 端点优先级总结

| 优先级 | 端点数量 | 端点列表 | 预计工作量 |
|--------|---------|---------|-----------|
| P0 | 3 | scrape, search, map | 1 小时 |
| P1 | 5 | team/* (5个) | 1 小时 |
| P2 | 2 | crawl/active, crawl/params-preview | 30 分钟 |
| **总计** | **10** | - | **2.5 小时** |

### 9.2 实现模式对比

| 端点 | 是否异步 | 是否需要资源绑定 | 是否需要幂等性 | 实现模式 |
|------|---------|----------------|--------------|---------|
| scrape | ❌ | ❌ | ❌ | 简单转发 |
| search | ❌ | ❌ | ❌ | 简单转发 |
| map | ❌ | ❌ | ❌ | 简单转发 |
| team/* | ❌ | ❌ | ❌ | 简单转发 |
| crawl/active | ❌ | ❌ | ❌ | 简单转发 |
| crawl/params-preview | ❌ | ❌ | ❌ | 简单转发 |

**结论**：所有 10 个端点都采用最简单的转发模式，无需额外业务逻辑。

---

## 10. 变更记录（Change Log）

| 日期 | 版本 | 变更内容 | 作者 |
|------|------|---------|------|
| 2026-02-25 | v1.0 | 初始版本 | Claude |
