# FD：Firecrawl API v2 缺失端点实现落地设计

> **对应 PRD**：`docs/PRD/2026-02-25-firecrawl-v2-missing-endpoints.md`
> **创建时间**：2026-02-25
> **状态**：Draft
> **优先级**：P0（核心功能完整性）
> **范围**：后端数据面 API（`app/api/firecrawl_v2_compat.py`）

---

## 1. 背景与问题陈述

PRD 记录了 Firecrawl API v2 缺失 10 个功能性端点的问题，虽然通配符路由可以转发这些请求，但缺少显式实现导致审计日志不准确、代码可读性差、维护困难。

本 FD 的目标是将 PRD 的需求"工程化"为：
- 显式定义 10 个缺失端点
- 使用简单转发模式保持与现有架构一致
- 确保审计日志准确记录端点类型
- 提供完整的测试覆盖

---

## 2. 目标 / 非目标

### 2.1 目标

1. **API 完整性**：显式实现所有 10 个功能性端点
2. **架构一致性**：使用与现有端点相同的实现模式
3. **审计可追溯**：每个端点设置正确的 `request.state.endpoint`
4. **测试覆盖**：提供完整的单元测试和集成测试

### 2.2 非目标

- **不添加复杂业务逻辑**：采用简单转发模式，不添加资源绑定、幂等性等
- **不修改现有端点**：仅新增端点，不影响已有功能
- **不实现 V1 特有端点**：`/deep-research` 和 `/llmstxt` 暂不实现

---
## 3. 现状分析（基于代码）

### 3.1 路由架构（app/api/firecrawl_v2_compat.py）

**当前文件结构**：
```python
from fastapi import APIRouter, Body, Depends, Request
from sqlalchemy.orm import Session

router = APIRouter(prefix="/v2", tags=["firecrawl-compat-v2"])

# 辅助函数
def _forwarder(request: Request) -> Forwarder: ...
def _with_query(request: Request, path: str) -> str: ...
def _path_and_query(request: Request) -> str: ...
def _extract_id_from_response(response: Any) -> str | None: ...
def _maybe_bind_created_resource(...) -> None: ...
def _forward_with_fallback(...) -> Any: ...

# 显式端点定义（按功能分组）
@router.post("/crawl") ...
@router.get("/crawl/{job_id}") ...
@router.post("/agent") ...
@router.post("/batch/scrape") ...
@router.post("/browser") ...
@router.post("/extract") ...

# 通配符路由（最后定义）
@router.get("/{path:path}") ...
@router.post("/{path:path}") ...
@router.delete("/{path:path}") ...
```

**关键特点**：
- 使用 FastAPI 的 `APIRouter` 进行路由管理
- 路由按定义顺序匹配（显式端点优先于通配符）
- 所有端点都依赖 `enforce_client_governance` 进行鉴权

### 3.2 转发机制（app/core/forwarder.py）

**Forwarder 类核心功能**：
```python
class Forwarder:
    def forward(
        self,
        db: Session,
        request_id: str,
        client: Client,
        method: str,
        upstream_path: str,
        json_body: Any | None,
        inbound_headers: dict[str, str],
        pinned_api_key_id: int | None = None,
    ) -> ForwardResult:
        # 1. 选择 API Key（从 Key 池或使用 pinned_api_key_id）
        # 2. 构建上游请求（添加 Authorization 头）
        # 3. 发送请求到 Firecrawl API
        # 4. 处理 429 错误（冷却机制）
        # 5. 重试逻辑（切换 Key）
        # 6. 返回响应
```

**关键特性**：
- 自动处理 429 限流（Key 冷却机制）
- 支持重试和 Key 切换
- 记录请求指标（延迟、状态码等）
- 过滤敏感请求/响应头

### 3.3 现有端点实现模式

根据代码分析，项目中存在 4 种端点实现模式：

#### 模式 1：异步任务 + 资源绑定

**适用场景**：返回 job_id 的 POST 端点（crawl, agent, batch_scrape, browser, extract）

**实现示例**：
```python
@router.post("/extract", dependencies=[Depends(enforce_client_governance)])
def extract(
    request: Request,
    payload: Any = Body(...),
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    request.state.endpoint = "extract"  # 设置审计日志标识
    response = _forward_with_fallback(
        request, db=db, client=client, method="POST",
        primary_path=_with_query(request, "/v2/extract"),
        fallback_path=None, payload=payload,
    )
    # 绑定资源：确保后续查询使用相同的 API Key
    _maybe_bind_created_resource(
        request, db=db, client=client,
        resource_type="extract", response=response,
    )
    return response
```

**特殊处理**：
- 调用 `_maybe_bind_created_resource` 提取响应中的 `id` 并绑定到 API Key
- 后续查询该资源时，使用 `lookup_bound_key_id` 找到绑定的 Key

#### 模式 2：异步任务 + 幂等性

**适用场景**：长时间运行的任务（crawl, agent）

**实现示例**：
```python
@router.post("/crawl", dependencies=[Depends(enforce_client_governance)])
def crawl(
    request: Request,
    payload: Any = Body(...),
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    request.state.endpoint = "crawl"
    # 幂等性检查：如果已存在相同请求，直接返回缓存结果
    ctx, replay = idempotency_start_or_replay(
        db=db, config=request.app.state.config,
        client_id=client.id,
        idempotency_key=request.headers.get("x-idempotency-key"),
        endpoint="crawl", method="POST", payload=payload,
    )
    if replay is not None:
        request.state.retry_count = 0
        return replay
    
    # 转发请求
    response = _forward_with_fallback(...)
    
    # 绑定资源 + 记录幂等性结果
    _maybe_bind_created_resource(...)
    idempotency_complete(db=db, config=..., ctx=ctx, response=response)
    return response
```

**特殊处理**：
- 支持 `x-idempotency-key` 头，防止重复提交
- 使用 `idempotency_start_or_replay` 和 `idempotency_complete` 管理幂等性

#### 模式 3：查询绑定资源

**适用场景**：GET {id} 端点（查询异步任务状态）

**实现示例**：
```python
@router.get("/extract/{job_id}", dependencies=[Depends(enforce_client_governance)])
def extract_status(
    request: Request,
    job_id: str,
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    request.state.endpoint = "extract"
    # 查找绑定的 API Key
    pinned_api_key_id = lookup_bound_key_id(
        db, client_id=client.id,
        resource_type="extract", resource_id=job_id,
    )
    # 使用绑定的 Key 转发请求
    return _forward_with_fallback(
        request, db=db, client=client, method="GET",
        primary_path=_with_query(request, f"/v2/extract/{job_id}"),
        fallback_path=None, payload=None,
        pinned_api_key_id=pinned_api_key_id,
    )
```

**特殊处理**：
- 使用 `lookup_bound_key_id` 查找创建该资源时使用的 API Key
- 确保查询和创建使用同一个 Key（避免跨 Key 查询失败）

#### 模式 4：简单转发

**适用场景**：同步返回结果的端点（通配符路由）

**实现示例**：
```python
@router.post("/{path:path}", dependencies=[Depends(enforce_client_governance)])
def passthrough_post(
    request: Request,
    path: str,
    payload: Any | None = Body(None),
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    return _forward_with_fallback(
        request, db=db, client=client, method="POST",
        primary_path=_path_and_query(request),
        fallback_path=None, payload=payload,
    )
```

**特殊处理**：无，直接转发

---

### 3.4 缺失端点的实现模式选择

根据 OpenAPI 规范分析（`api-reference/firecrawl-docs/api-reference/v2-openapi.json`），所有 10 个缺失端点都是**同步返回结果**，应采用**模式 4：简单转发**。

| 端点 | 是否异步 | 是否需要资源绑定 | 是否需要幂等性 | 选择模式 |
|------|---------|----------------|--------------|---------|
| POST /scrape | ❌ | ❌ | ❌ | 模式 4 |
| POST /search | ❌ | ❌ | ❌ | 模式 4 |
| POST /map | ❌ | ❌ | ❌ | 模式 4 |
| GET /team/* (5个) | ❌ | ❌ | ❌ | 模式 4 |
| GET /crawl/active | ❌ | ❌ | ❌ | 模式 4 |
| POST /crawl/params-preview | ❌ | ❌ | ❌ | 模式 4 |

**结论**：所有端点都使用简单转发模式，但需要显式定义以设置正确的 `request.state.endpoint`。

---

### 3.5 通配符路由的局限性

**当前问题**：
1. **审计日志不准确**：通配符路由无法设置具体的 `endpoint` 标识
2. **代码可读性差**：无法直观看出支持哪些端点
3. **无法添加特殊处理**：未来如需针对特定端点添加监控、限流等，需要显式定义

**解决方案**：
- 显式定义所有功能性端点
- 保留通配符路由作为兜底（处理未知端点）
- 确保显式端点在通配符之前定义（路由优先级）

---

## 4. 功能设计（需要实现/修改的功能）

### 4.1 整体架构

**实现位置**：`app/api/firecrawl_v2_compat.py`

**插入位置**：在现有端点之后、通配符路由之前

**代码结构**：
```
# 现有端点（不修改）
@router.post("/crawl") ...
@router.post("/agent") ...
...

# ========== 新增：P0 核心功能端点 ==========
@router.post("/scrape") ...
@router.post("/search") ...
@router.post("/map") ...

# ========== 新增：P1 账户管理端点 ==========
@router.get("/team/credit-usage") ...
@router.get("/team/queue-status") ...
@router.get("/team/credit-usage/historical") ...
@router.get("/team/token-usage") ...
@router.get("/team/token-usage/historical") ...

# ========== 新增：P2 辅助功能端点 ==========
@router.get("/crawl/active") ...
@router.post("/crawl/params-preview") ...

# 通配符路由（保持在最后）
@router.get("/{path:path}") ...
@router.post("/{path:path}") ...
@router.delete("/{path:path}") ...
```

---

### 4.2 P0 核心功能端点实现

#### 4.2.1 POST /v2/scrape - 单页抓取

**功能描述**：抓取单个 URL 并可选使用 LLM 提取信息，同步返回结果。

**实现代码**：
```python
@router.post("/scrape", dependencies=[Depends(enforce_client_governance)])
def scrape(
    request: Request,
    payload: Any = Body(...),
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    """
    Scrape a single URL and optionally extract information using an LLM.
    
    Synchronous endpoint - returns results immediately.
    """
    request.state.endpoint = "scrape"
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

**关键点**：
- 设置 `request.state.endpoint = "scrape"` 用于审计日志
- 使用 `_with_query` 保留查询参数
- 同步返回，无需资源绑定

---

#### 4.2.2 POST /v2/search - 搜索

**功能描述**：搜索并可选抓取搜索结果，同步返回。

**实现代码**：
```python
@router.post("/search", dependencies=[Depends(enforce_client_governance)])
def search(
    request: Request,
    payload: Any = Body(...),
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    """
    Search and optionally scrape search results.
    
    Synchronous endpoint - returns results immediately.
    """
    request.state.endpoint = "search"
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="POST",
        primary_path=_with_query(request, "/v2/search"),
        fallback_path=None,
        payload=payload,
    )
```

---

#### 4.2.3 POST /v2/map - 网站地图

**功能描述**：基于选项映射多个 URL，快速获取网站结构，同步返回。

**实现代码**：
```python
@router.post("/map", dependencies=[Depends(enforce_client_governance)])
def map_urls(
    request: Request,
    payload: Any = Body(...),
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    """
    Map multiple URLs based on options.
    
    Synchronous endpoint - returns URL list immediately.
    """
    request.state.endpoint = "map"
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="POST",
        primary_path=_with_query(request, "/v2/map"),
        fallback_path=None,
        payload=payload,
    )
```

**注意**：函数名使用 `map_urls` 而非 `map`，避免与 Python 内置函数冲突。

---

### 4.3 P1 账户管理端点实现

#### 4.3.1 GET /v2/team/credit-usage - 剩余额度查询

**功能描述**：获取团队剩余的 credits，返回剩余额度、计划额度、计费周期等信息。

**实现代码**：
```python
@router.get("/team/credit-usage", dependencies=[Depends(enforce_client_governance)])
def team_credit_usage(
    request: Request,
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    """
    Get remaining credits for the authenticated team.
    
    Returns:
        - remainingCredits: Number of credits remaining
        - planCredits: Number of credits in the plan
        - billingPeriodStart: Start date of billing period
        - billingPeriodEnd: End date of billing period
    """
    request.state.endpoint = "team_credit_usage"
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

---

#### 4.3.2 GET /v2/team/queue-status - 队列状态

**功能描述**：获取团队的抓取队列指标。

**实现代码**：
```python
@router.get("/team/queue-status", dependencies=[Depends(enforce_client_governance)])
def team_queue_status(
    request: Request,
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    """
    Get metrics about your team's scrape queue.
    
    Returns:
        - jobsInQueue: Total number of jobs in queue
        - activeJobsInQueue: Number of active jobs
        - waitingJobsInQueue: Number of waiting jobs
        - maxConcurrency: Maximum concurrent jobs based on plan
        - mostRecentSuccess: Timestamp of most recent successful job
    """
    request.state.endpoint = "team_queue_status"
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="GET",
        primary_path=_with_query(request, "/v2/team/queue-status"),
        fallback_path=None,
        payload=None,
    )
```

---

#### 4.3.3 GET /v2/team/credit-usage/historical - 历史额度使用

**实现代码**：
```python
@router.get("/team/credit-usage/historical", dependencies=[Depends(enforce_client_governance)])
def team_credit_usage_historical(
    request: Request,
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    """
    Get historical credit usage for the authenticated team.
    
    Returns monthly credit usage statistics.
    """
    request.state.endpoint = "team_credit_usage_historical"
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="GET",
        primary_path=_with_query(request, "/v2/team/credit-usage/historical"),
        fallback_path=None,
        payload=None,
    )
```

---

#### 4.3.4 GET /v2/team/token-usage - Token 使用情况

**实现代码**：
```python
@router.get("/team/token-usage", dependencies=[Depends(enforce_client_governance)])
def team_token_usage(
    request: Request,
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    """
    Get remaining tokens for the authenticated team (Extract only).
    
    Note: This endpoint is specific to the Extract feature.
    """
    request.state.endpoint = "team_token_usage"
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="GET",
        primary_path=_with_query(request, "/v2/team/token-usage"),
        fallback_path=None,
        payload=None,
    )
```

---

#### 4.3.5 GET /v2/team/token-usage/historical - 历史 Token 使用

**实现代码**：
```python
@router.get("/team/token-usage/historical", dependencies=[Depends(enforce_client_governance)])
def team_token_usage_historical(
    request: Request,
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    """
    Get historical token usage for the authenticated team (Extract only).
    
    Returns monthly token usage statistics.
    """
    request.state.endpoint = "team_token_usage_historical"
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="GET",
        primary_path=_with_query(request, "/v2/team/token-usage/historical"),
        fallback_path=None,
        payload=None,
    )
```

---

### 4.4 P2 辅助功能端点实现

#### 4.4.1 GET /v2/crawl/active - 活跃爬取任务

**实现代码**：
```python
@router.get("/crawl/active", dependencies=[Depends(enforce_client_governance)])
def crawl_active(
    request: Request,
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    """
    Get all active crawls for the authenticated team.
    
    Returns a list of currently running crawl jobs.
    """
    request.state.endpoint = "crawl_active"
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="GET",
        primary_path=_with_query(request, "/v2/crawl/active"),
        fallback_path=None,
        payload=None,
    )
```

---

#### 4.4.2 POST /v2/crawl/params-preview - 参数预览

**实现代码**：
```python
@router.post("/crawl/params-preview", dependencies=[Depends(enforce_client_governance)])
def crawl_params_preview(
    request: Request,
    payload: Any = Body(...),
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    """
    Preview crawl parameters generated from natural language prompt.
    
    Helps users understand crawl parameters before starting a crawl.
    """
    request.state.endpoint = "crawl_params_preview"
    return _forward_with_fallback(
        request,
        db=db,
        client=client,
        method="POST",
        primary_path=_with_query(request, "/v2/crawl/params-preview"),
        fallback_path=None,
        payload=payload,
    )
```

---

### 4.5 路由注册顺序

**关键原则**：显式端点必须在通配符路由之前定义，确保路由优先级正确。

**最终文件结构**：
```python
# 1. 导入和辅助函数（不变）
from fastapi import APIRouter, Body, Depends, Request
...

# 2. 现有显式端点（不变）
@router.post("/crawl") ...
@router.post("/agent") ...
@router.post("/batch/scrape") ...
...

# 3. 新增端点（按优先级分组）
# P0 核心功能
@router.post("/scrape") ...
@router.post("/search") ...
@router.post("/map") ...

# P1 账户管理
@router.get("/team/credit-usage") ...
@router.get("/team/queue-status") ...
@router.get("/team/credit-usage/historical") ...
@router.get("/team/token-usage") ...
@router.get("/team/token-usage/historical") ...

# P2 辅助功能
@router.get("/crawl/active") ...
@router.post("/crawl/params-preview") ...

# 4. 通配符路由（保持在最后，不变）
@router.get("/{path:path}") ...
@router.post("/{path:path}") ...
@router.delete("/{path:path}") ...
```

**验证方法**：
```bash
# 查看路由注册顺序
python -c "
from app.main import app
for route in app.routes:
    if hasattr(route, 'path'):
        print(f'{route.methods} {route.path}')
"
```

---


## 5. 技术细节

### 5.1 错误处理策略

**错误传递机制**：
- 所有上游 API 错误（401、429、500 等）直接透传给客户端
- 保持与 Firecrawl 官方 API 的错误响应格式一致
- `_forward_with_fallback` 自动处理 429 错误（Key 冷却机制）

**常见错误场景**：

| 错误码 | 场景 | 处理方式 |
|--------|------|---------|
| 401 | API Key 无效 | 透传给客户端，提示 Key 配置错误 |
| 402 | 额度不足 | 透传给客户端，提示充值 |
| 429 | 限流 | Forwarder 自动冷却 Key 并重试 |
| 500 | 上游服务错误 | 透传给客户端，记录错误日志 |

---

### 5.2 审计日志

**日志字段**：
- `request.state.endpoint`：端点标识（如 "scrape", "team_credit_usage"）
- `request.state.api_key_id`：使用的 API Key ID
- `request.state.retry_count`：重试次数
- `request.state.request_id`：请求唯一标识

**日志记录位置**：在 `app/middleware.py` 的 `RequestIdMiddleware` 中自动记录

---

### 5.3 性能考虑

**转发延迟**：每个请求增加约 10-50ms 的转发延迟（网络往返时间）

**并发控制**：通过 `ConcurrencyManager` 控制每个 Client 的并发数

---

### 5.4 安全考虑

**请求头过滤**：过滤敏感请求头和响应头

**API Key 保护**：API Key 加密存储，转发时动态解密

**Client 鉴权**：所有端点都依赖 `enforce_client_governance` 进行鉴权

---

## 6. 测试计划

### 6.1 单元测试

**测试文件**: `tests/integration/test_firecrawl_v2_missing_endpoints.py`

**测试用例**:
- 测试所有 10 个端点可访问（不返回 404）
- 测试所有端点需要鉴权（无 token 返回 401）
- 测试 endpoint 标识设置正确

### 6.2 集成测试

使用 mock 上游 API，验证转发逻辑正确。

### 6.3 E2E 测试

使用真实 Firecrawl API Key，验证与官方 API 的兼容性。

**注意**: E2E 测试会产生费用，仅在必要时运行。

---

## 7. 部署与回滚

### 7.1 部署步骤

1. 代码审查（ruff check, mypy）
2. 运行测试（pytest）
3. 本地验证
4. 部署到生产环境
5. 验证部署

### 7.2 回滚方案

如遇问题，使用 Docker 或 Git 回滚到上一个版本。

---

## 8. 风险与缓解

| 风险 | 影响 | 概率 | 缓解措施 |
|------|------|------|---------|
| 路由冲突 | 端点无法访问 | 低 | 确保显式端点在通配符之前 |
| 上游 API 变更 | 行为不一致 | 中 | 定期同步 OpenAPI 规范 |
| 性能下降 | 转发延迟增加 | 低 | 监控响应时间 |

---

## 9. 参考文档

### 9.1 项目文档

- PRD: `docs/PRD/2026-02-25-firecrawl-v2-missing-endpoints.md`
- 技术方案: `docs/agent.md`
- API 契约: `docs/MVP/Firecrawl-API-Manager-API-Contract.md`

### 9.2 官方文档

- Firecrawl API v2: https://docs.firecrawl.dev/api-reference/v2-introduction
- OpenAPI 规范: `api-reference/firecrawl-docs/api-reference/v2-openapi.json`

### 9.3 代码参考

- 现有实现: `app/api/firecrawl_v2_compat.py`
- 转发逻辑: `app/core/forwarder.py`

---

## 10. 变更记录

| 日期 | 版本 | 变更内容 | 作者 |
|------|------|---------|------|
| 2026-02-25 | v1.0 | 初始版本 | Claude |

---

**FD 文档编写完成** ✅

