# 实现提示词：Firecrawl API v2 缺失端点实现

## 📋 任务概述

请在 `app/api/firecrawl_v2_compat.py` 文件中添加 10 个缺失的 Firecrawl API v2 端点，确保用户可以无缝切换到我们的中转服务。

---

## 🎯 核心要求

1. **简单转发模式**：所有端点都采用简单转发，不添加资源绑定、幂等性等复杂逻辑
2. **路由顺序**：新端点必须插入在现有端点之后、通配符路由 `/{path:path}` 之前
3. **endpoint 标识**：每个端点都要设置 `request.state.endpoint`，用于审计日志
4. **保持一致性**：函数签名、代码风格与现有端点保持一致

---

## 📂 文件位置

**修改文件**：`app/api/firecrawl_v2_compat.py`

**插入位置**：在现有端点（如 `extract`, `browser` 等）之后、通配符路由之前

**参考文档**：
- PRD: `docs/PRD/2026-02-25-firecrawl-v2-missing-endpoints.md`
- FD: `docs/FD/2026-02-25-firecrawl-v2-missing-endpoints-fd.md`
- TDD: `docs/TDD/2026-02-25-firecrawl-v2-missing-endpoints-tdd.md`
- TODO: `docs/TODO/2026-02-25-firecrawl-v2-missing-endpoints.md`

---

## 🔧 需要实现的 10 个端点

### P0：核心功能端点（3个）

#### 1. POST /v2/scrape - 单页抓取

```python
@router.post("/scrape", dependencies=[Depends(enforce_client_governance)])
def scrape(
    request: Request,
    payload: Any = Body(...),
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    """Scrape a single URL and optionally extract information using an LLM."""
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

#### 2. POST /v2/search - 搜索

```python
@router.post("/search", dependencies=[Depends(enforce_client_governance)])
def search(
    request: Request,
    payload: Any = Body(...),
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    """Search and optionally scrape search results."""
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

#### 3. POST /v2/map - 网站地图

```python
@router.post("/map", dependencies=[Depends(enforce_client_governance)])
def map_urls(
    request: Request,
    payload: Any = Body(...),
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    """Map multiple URLs based on options."""
    request.state.endpoint = "map"
    return _forward_with_fallback(
        reqt,
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

### P1：账户管理端点（5个）

#### 4. GET /v2/team/credit-usage - 剩余额度查询

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

#### 5. GET /v2/team/queue-status - 队列状态

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

#### 6. GET /v2/team/credit-usage/historical - 历史额度使用

```python
@router.get("/team/credit-usage/historical", dependencies=[Depends(enforce_client_governance)])
def team_credit_usage_historical(
    request: Request,
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    """Get historical credit usage for the authenticated team."""
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

#### 7. GET /v2/team/token-usage - Token 使用情况

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

#### 8. GET /v2/team/token-usage/historical - 历史 Token 使用

```python
@router.get("/team/token-usage/historical", dependencies=[Depends(enforce_client_governance)])
def team_token_usage_historical(
    request: Request,
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    """Get historical token usage for the authenticated team (Extract only)."  request.state.endpoint = "team_token_usage_historical"
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

### P2：辅助功能端点（2个）

#### 9. GET /v2/crawl/active - 活跃爬取任务

```python
@router.get("/crawl/active", dependencies=[Depends(enforce_client_governance)])
def crawl_active(
    request: Request,
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    """Get all active crawls for the authenticated team."""
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

#### 10. POST /v2/crawl/params-preview - 参数预览

```python
@router.post("/crawl/params-preview", dependencies=[Depends(enforce_client_governance)])
def crawl_params_preview(
    request: Request,
    payload: Any = Body(...),
    client: Client = Depends(require_client),
    db: Session = Depends(get_db),
) -> Any:
    """Preview crawl parameters generated from natural language prompt."""
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

## 📝 实现步骤

### 步骤 1：找到插入位置

1. 打开 `app/api/firecrawl_v2_compat.py`
2. 找到现有端点的最后一个（如 `ect_status` 或 `passthrough_delete` 之前）
3. 找到通配符路由 `@router.get("/{path:path}")` 的位置
4. 在这两者之间插入新代码

### 步骤 2：添加注释分隔

在插入位置添加清晰的注释：

```python
# ========== P0 核心功能端点 ==========

# (添加 scrape, search, map)

# ========== P1 账户管理端点 ==========

# (添加 5 个 team/* 端点)

# ========== P2 辅助功能端点 ==========

# (添加 crawl/active, crawl/params-preview)
```

### 步骤 3：复制粘贴代码

按照上面提供的代码，依次添加 10 个端点。

### 步骤 4：验证代码

运行代码风格检查：
```bash
ruff check app/api/firecrawl_v2_compat.py
mypy app/api/firecrawl_v2_compat.py
```

---

## ✅ 验收标准

### 功能验收

- [ ] 所有 10 个端点已添加到 `firecrawl_v2_compat.py`
- [ ] 每个端点都设置了正确的 `request.state.endpoint`
- [ ] 所有端点都在通配符路由之前定义
- [ ] POST 端点函数签名：`(request, payload, client, db)`
- [ ] GET 端点函数签名：`(request, client, db)`

### 代码质量验收

- [ ] 代码风格检查通过（ruff）
- [ ] 类型检查通过（mypy）
- [ ] 所有端点都有 docstring
- [ ] 代码格式与现有端点一致

### 测试验收

运行测试：
```bash
pytest tests/integration/test_firecrawl_v2_missing_endpoints.py -v
```

预期结果：
- [ ] 所有端点可访问性测试通过（10个）
- [ ] 所有鉴权测试通过（2个）
- [ ] 测试覆盖率 ≥ 80%

### 本地验证

启动服务：
```bash
uvicorn app.main:app --reload
```

访问 Swagger 文档：
```
http://localhost:8000/docs
```

验证：
- [ ] Swagger 文档中显示所有 10 个新端点
- [ ] 可以在 Swagger 中测试端点（需要 Client Token）

---

## ⚠️ 注意事项

### 1. **错误示例**（会导致端点无法访问）：
```python
# 通配符路由
@router.get("/{path:path}")
def passthrough_get(...): ...

# 新端点（错误：在通配符之后）
@router.post("/scrape")
def scrape(...): ...
```

**正确示例**：
```python
# 新端点（正确：在通配符之前）
@router.post("/scrape")
def scrape(...): ...

# 通配符路由
@router.get("/{path:path}")
def passthrough_get(...): ...
```

### 2. endpoint 标识命名规范

- 使用下划线分隔：`team_credit_usage`（不是 `teamCreditUsage`）
- 与路径对应：`/team/credit-usage` → `team_credit_usage`
- 保持一致性：参考现有端点的命名

### 3. 函数命名

- `map` 函数名改为 `map_与 Python 内置函数冲突）
- 其他函数名与路径最后一段对应

### 4. 不要添加额外逻辑

- ❌ 不要添加资源绑定（`_maybe_bind_created_resource`）
- ❌ 不要添加幂等性（`idempotency_start_or_replay`）
- ❌ 不要添加特殊的错误处理
- ✅ 只需要简单转发即可

---

## 🧪 测试命令

### 运行所有测试
```bash
pytest tests/integration/test_firecrawl_v2_missing_endpoints.py -v
```

### 运行特定测试组
```bash
# 只测试端点可访问性
pytest tests/integration/test_firecrawl_v2_missing_endpoints.py::TestEndpointAccessibility -v

# 只测试鉴权
pytest tests/integration/test_firecrawl_v2_missing_endpoints.py::TestAuthentication -v
```

### 检查测试覆盖率
```bash
pytest --cov=app/api/firecrawl_v2_compat --cov-report=html tests/integration/test_firecrawl_v2_missing_endpoints.py
```

### 运行 E2E 测试（可选，需要真实 API Key）
```bash
export FCAM_E2E="1"
export FCAM_E2E_ALLOW_UPSTREAM="1"
export FCAM_E2E_FIRECRAWL_API_KEY="fc-xxx"
pytest tests/e2e/test_e2e_firecrawl_v2_compatibility.py -v
```

---

## 📊 预计工作量

- **代码实现**：30-45 分钟（复制粘贴 + 调整）
- **代码检查**：5-10 分钟（ruff + mypy）
- **测试验证**：10-15 分钟（运行测试）
- **本地验证**：5-10 分钟（Swagger 检查）
- **总计**：约 1 小时

---

## 🎯 成功标准

实现完成后，应该满足：

1. ✅ 所有 10 个端点都能在 Swagger 中看到
2. ✅ 所有端点都需要 Client Token 鉴权
3. ✅ 所有测试通过（至少 21 个集成测试）
4. ✅ 测试覆盖率 ≥ 80%
5. ✅ 代码风格检查通过
6. ✅ 用户可以无缝切换到我们的服务

---

## 📚 参考资料

- **现有端点示例**：查看 `firecrawl_v2_compat.py` 中的 `extract`, `browser` 等端点
- **OpenAPI 规范**：`api-reference/firecrawl-docs/api-reference/v2-openapi.json`
- **官方文档**：https://docs.firecrawl.dev/api-reference/v2-introduction

---

**祝实现顺利！如有问题，请参考 FD 和 TDD 文档。** 🚀
