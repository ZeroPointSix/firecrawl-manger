# TDD：Firecrawl API v2 缺失端点测试驱动开发

> **PRD**：`docs/PRD/2026-02-25-firecrawl-v2-missing-endpoints.md`
> **FD**：`docs/FD/2026-02-25-firecrawl-v2-missing-endpoints-fd.md`
> **创建时间**：2026-02-25
> **状态**：Draft
> **优先级**：P0（核心功能完整性）

---

## 0. 结论先行（我们要交付什么）

**核心目标**：确保用户可以无缝切换到我们的中转服务，所有 Firecrawl API v2 功能性端点都能正常工作。

**交付物**：

1. **10 个显式端点实现**
   - P0：scrape, search, map（核心功能）
   - P1：team/credit-usage, team/queue-status, team/credit-usage/historical, team/token-usage, team/token-usage/historical（账户管理）
   - P2：crawl/active, crawl/params-preview（辅助功能）

2. **完整的测试覆盖**
   - 单元测试：验证端点可访问性、鉴权、endpoint 标识
   - 集成测试：验证转发逻辑正确性
   - E2E 测试：验证与 Firecrawl 官方 API 的兼容性

3. **无缝切换保证**
   - 请求/响应格式与官方 API 完全一致
   - 错误处理与官方 API 一致
   - 所有查询参数和请求头正确传递

---

## 1. 约束与假设

### 1.1 技术约束

- 代码基于 FastAPI + SQLAlchemy（见 `app/api/firecrawl_v2_compat.py`）
- 使用现有的 `_forward_with_fallback` 函数进行转发
- 所有端点都需要 Client Token 鉴权（`enforce_client_governance`）
- 测试覆盖率要求 ≥ 80%

### 1.2 业务假设

- 所有 10 个端点都是同步返回结果（不需要资源绑定）
- 用户使用的 Firecrawl API Key 有足够的额度
- 上游 Firecrawl API 服务正常运行

### 1.3 无缝切换要求

- **请求兼容**：支持所有官方 API 的请求参数和请求头
- **响应兼容**：返回格式与官方 API 完全一致
- **错误兼容**：错误码和错误消息与官方 API 一致
- **性能可接受**：转发延迟 < 100ms

---

## 2. 当前实现要点（作为设计输入）

### 2.1 路由架构（app/api/firecrawl_v2_compat.py）

**现有端点**：
- 已实现：crawl, agent, batch_scrape, browser, extract（共 18 个端点）
- 通配符路由：`GET/POST/DELETE /{path:path}`（兜底转发）

**关键函数**：
```python
def _forward_with_fallback(
    request: Request,
    db: Session,
    client: Client,
    method: str,
    primary_path: str,
    fallback_path: str | None,
    payload: Any | None,
    pinned_api_key_id: int | None = None,
) -> Any:
    # 转发请求到上游 Firecrawl API
    # 自动处理 429 限流、重试、Key 切换
```

### 2.2 转发机制（app/core/forwarder.py）

**Forwarder 类核心功能**：
- 从 Key 池选择可用的 API Key
- 构建上游请求（添加 Authorization 头）
- 处理 429 错误（Key 冷却机制）
- 支持重试和 Key 切换
- 记录请求指标

### 2.3 现有端点实现模式

根据 FD 文档分析，项目中有 4 种端点实现模式：
1. 异步任务 + 资源绑定（crawl, agent, batch, extract）
2. 异步任务 + 幂等性（crawl, agent）
3. 查询绑定资源（GET /crawl/{id}）
4. **简单转发**（通配符路由）← 我们使用这种模式

---

## 3. 测试策略

### 3.1 测试金字塔

```
       E2E 测试 (10%)
      /          \
     /  集成测试  \  (30%)
    /              \
   /   单元测试     \  (60%)
  /________________\
```

**单元测试（60%）**：
- 端点可访问性（不返回 404）
- 鉴权要求（无 token 返回 401）
- endpoint 标识设置正确
- 路由优先级正确

**集成测试（30%）**：
- 转发逻辑正确性（mock 上游 API）
- 请求参数正确传递
- 响应格式正确返回
- 错误处理正确

**E2E 测试（10%）**：
- 与真实 Firecrawl API 的兼容性
- 完整的请求/响应流程
- 边界情况处理

### 3.2 测试覆盖率要求

- **整体覆盖率**：≥ 80%
- **新增代码覆盖率**：100%（所有新增端点）
- **关键路径覆盖率**：100%（转发逻辑）

### 3.3 无缝切换验证策略

**验证维度**：
1. **功能兼容性**：所有端点都能正常调用
2. **参数兼容都能正确传递
3. **响应兼容性**：响应格式与官方 API 一致
4. **错误兼容性**：错误码和错误消息一致
5. **性能可接受性**：转发延迟在可接受范围内

---
## 4. 单元测试设计

### 4.1 端点可访问性测试

**目标**：验证所有 10 个端点都已正确注册，不返回 404。

#### 测试用例 1：P0 核心功能端点可访问

```python
def test_scrape_endpoint_exists(client: TestClient, client_headers: dict):
    """测试 POST /v2/scrape 端点存在"""
    response = client.post(
        "/v2/scrape",
        json={"url": "https://example.com"},
        headers=client_headers,
    )
    assert response.status_code != 404, "scrape endpoint should exist"


def test_search_endpoint_exists(client: TestClient, client_headers: dict):
    """测试 POST /v2/search 端点存在"""
    response = client.post(
        "/v2/search",
        json={"query": "test"},
        headers=client_headers,
    )
    assert response.status_code != 404, "search endpoint should exist"


def test_map_endpoint_exists(client: TestClient, client_headers: dict):
    """测试 POST /v2/map 端点存在"""
    response = client.post(
        "/v2/map",
        json={"url": "https://example.com"},
        headers=client_headers,
    )
    assert response.status_code != 404, "map endpoint should exist"
```

#### 测试用例 2：P1 账户管理端点可访问

```python
def test_team_credit_usage_endpoint_exists(client: TestClient, client_headers: dict):
    """测试 GET /v2/team/credit-usage 端点存在"""
    response = client.get("/v2/team/credit-usage", headers=client_headers)
    assert response.status_code != 404


def test_team_queue_status_endpoint_exists(client: TestClient, client_headers: dict):
    """测试 GET /v2/team/queue-status 端点存在"""
    response = client.get("/v2/team/queue-status", headers=client_headers)
    assert response.status_code != 404


def test_team_credit_usage_historical_endpoint_exists(client: TestClient, client_headers: dict):
    """测试 GET /v2/team/credit-usage/historical 端点存在"""
    response = client.get("/v2/team/credit-usage/historical", headers=client_headers)
    assert response.status_code != 404


def test_team_token_usage_endpoint_exists(client: TestClient, client_headers: dict):
    """测试 GET /v2/team/token-usage 端点存在"""
    response = client.get("/v2/team/token-usage", headers=client_headers)
    assert response.status_code != 404


def test_team_token_usage_historical_endpoint_exists(client: TestClient, client_headers: dict):
    """测试 GET /v2/team/token-usage/historical 端点存在"""
    response = client.get("/v2/team/token-usage/historical", headers=client_headers)
    assert response.status_code != 404
```

#### 测试用例 3：P2 辅助功能端点可访问

```python
def test_crawl_active_endpoint_exists(client: TestClient, client_headers: dict):
    """测试 GET /v2/crawl/active 端点存在"""
    response = client.get("/v2/crawl/active", headers=client_headers)
    assert response.status_code != 404


def test_crawl_params_preview_endpoint_exists(client: TestClient, client_headers: dict):
    """测试 POST /v2/crawl/params-preview 端点存在"""
    response = client.post(
        "/v2/crawl/params-preview",
        json={"prompt": "test"},
        headers=client_headers,
    )
    assert response.status_code != 404
```

---

### 4.2 鉴权测试

**目标**：验证所有端点都需要 Client Token 鉴权。

#### 测试用例 4：所有端点需要鉴权

```python
@pytest.mark.parametrize("method,path,payload", [
    ("POST", "/v2/scrape", {"url": "https://example.com"}),
    ("POST", "/v2/search", {"query": "test"}),
    ("POST", "/v2/map", {"url": "https://example.com"}),
    ("GET", "/v2/team/credit-usage", None),
    ("GET", "/v2/team/queue-status", None),
    ("GET", "/v2/team/credit-usage/historical", None),
    ("GET", "/v2/team/token-usage", None),
    ("GET", "/v2/team/token-usage/historical", None),
    ("GET", "/v2/crawl/active", None),
    ("POST", "/v2/crawl/params-preview", {"prompt": "test"}),
])
def test_endpoints_require_auth(client: TestClient, method: str, path: str, payload: dict):
    """测试所有端点都需要鉴权"""
    if method == "POST":
        rlient.post(path, json=payload)
    else:
        response = client.get(path)
    
    assert response.status_code == 401, f"{method} {path} should require authentication"
    assert "unauthorized" in response.json()["error"].lower()
```

#### 测试用例 5：无效 Token 返回 401

```python
def test_invalid_token_returns_401(client: TestClient):
    """测试无效 Token 返回 401"""
    response = client.post(
        "/v2/scrape",
        json={"url": "https://example.com"},
        headers={"Authorization": "Bearer invalid_token"},
    )
    assert response.status_code == 401
```

---

### 4.3 endpoint 标识测试

**目标**：验证每个端点设置了正确的 `request.state.endpoint`，用于审计日志。

#### 测试用例 6：endpoint 标识正确设置

```python
from unittest.mock import patch, MagicMock

@patch('app.core.forwarder.Forwarder.forward')
def test_scrape_sets_correct_endpoint(mock_forward, client: TestClient, client_headers: dict):
    """测试 scrape 端点设置正确的 endpoint 标识"""
    mock_forward.return_value = MagicMock(
        response=MagicMock(status_code=200, body=b'{"success": true}', headers={}),
        api_key_id=1,
        retry_count=0,
    )
    
    # 捕获 request.state
    captured_request = None
    original_forward = mock_fide_effect
    
    def capture_request(*args, **kwargs):
        nonlocal captured_request
        # 从 kwargs 中获取 request（如果通过 _forward_with_fallback 传递）
        return mock_forward.return_value
    
    mock_forward.side_effect = capture_request
    
    response = client.post(
        "/v2/scrape",
        json={"url": "https://example.com"},
        headers=client_headers,
    )
    
    # 验证响应成功
    assert response.status_code == 200
    # 注意：实际验证 endpoint 需要在中间件或日志中检查


@pytest.mark.parametrize("path,expected_endpoint", [
    ("/v2/scrape", "scrape"),
    ("/v2/sear "search"),
    ("/v2/map", "map"),
    ("/v2/team/credit-usage", "team_credit_usage"),
    ("/v2/team/queue-status", "team_queue_status"),
    ("/v2/crawl/active", "crawl_active"),
])
def test_endpoint_identifiers(path: str, expected_endpoint: str):
    """测试端点标识符映射正确"""
    # 这个测试验证代码中的 endpoint 标识符设置
    # 实际实现中，可以通过读取代码或检查日志来验证
    pass  # 占位符，实际测试需要访问 request.state
```

---

### 4.4 路由优先级测试

**目标**：验证显式端点优先于通配符路由匹配。

#### 测试用例 7：显式端点优先匹配

```python
def test_explicit_routes_take_precedence(client: TestClient, client_headers: dict):
    """测试显式端点优先于通配符路由"""
    # 如果显式端点没有正确注册，请求会被通配符路由捕获
    # 通配符路由不会设置特定的 endpoint 标识
    
    response = client.post(
        "/v2/scrape",
        json={"url": "https://example.com"},
        headers=client_headers,
    )
    
    # 验证不是 404（端点存在）
    assert response.status_code != 404
    
    # 如果可以访问日志，验证 endpoint 字段不是通配符
    # assert logged_endpoint == "scrape"  # 需要日志访问
```

---


## 5. 集成测试设计（无缝切换验证）

### 5.1 转发逻辑测试

**目标**：验证请求正确转发到上游 Firecrawl API，响应正确返回。

#### 测试用例 8：scrape 端点转发正确

```python
from unittest.mock import patch, MagicMock

@patch('httpx.Client.post')
def test_scrape_forwards_correctly(mock_post, client: TestClient, client_headers: dict):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "application/json"}
    mock_response.json.return_value = {
        "success": True,
        "data": {"markdown": "# Example Domain"}
    }
    mock_post.return_value = mock_response
    
    response = client.post(
        "/v2/scrape",
        json={"url": "https://example.com"},
        headers=client_headers,
    )
    
    assert response.status_code == 200
    assert response.json()["success"] is True
```

---

### 5.2 参数传递测试

**目标**：验证所有请求参数正确传递到上游 API。

#### 测试用例 9：查询参数正确传递

```python
@patch('httpx.Client.post')
def test_query_parameters_forwarded(mock_post, client: TestClient, client_headers: dict):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"success": True}
    mock_post.return_value = mock_response
    
    response = client.post(
        "/v2/scrape?timeout=30000",
        json={"url": "https://example.com"},
        headers=client_headers,
    )
    
    assert response.status_code == 200
    # 验证上游请求包含查询参数
    upstream_url = mock_post.call_args[0][0]
    assert "timeout=30000" in upstream_url
```

---

### 5.3 响应格式测试

**目标**：验证响应格式与 Firecrawl 官方 API 完全一致。

#### 测试用例 10：成功响应格式一致

```python
@patch('httpx.Client.post')
def test_success_response_format(mock_post, client: TestClient, client_headers: dict):
    official_response = {
        "success": True,
        "data": {"markdown": "# Example"}
    }
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = official_response
    mock_post.return_value = mock_response
    
    response = client.post(
        "/v2/scrape",
        json={"url": "https://example.com"},
        headers=client_headers,
    )
    
    assert response.json() == official_response
```

---

### 5.4 错误处理测试

**目标**：验证错误响应与官方 API 一致。

#### 测试用例 11：401 错误正确透传

```python
@patch('httpx.Client.post')
def test_401_error_forwarded(mock_post, client: TestClient, client_headers: dict):
    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.json.return_value = {
        "success": False,
        "error": "Unauthorized"
    }
    mock_post.return_value = mock_response
    
    response = client.post(
        "/v2/scrape",
        json={"url": "https://example.com"},
        headers=client_headers,
    )
    
    assert response.status_code == 401
    assert "Unauthorized" in response.json()["error"]
```

---

## 6. E2E 测试设计（真实 API 验证）

### 6.1 E2E 测试策略

**目标**：使用真实 Firecrawl API Key，验证完整的无缝切换。

**注意事项**：
- E2E 测试会产生费用，仅在必要时运行
- 需要设置环境变量 `FCAM_E2E_ALLOW_UPSTREAM=1`
- 需要提供真实的 Firecrawl API Key

### 6.2 E2E 测试用例

#### 测试用例 12：scrape 端点 E2E 测试

```python
@pytest.mark.skipif(
    not os.getenv("FCAM_E2E_ALLOW_UPSTREAM"),
    reason="E2E test with real upstream API disabled"
)
def test_e2e_scrape_with_real_api(client: TestClient, client_headers: dict):
    response = client.post(
        "/v2/scrape",
        json={
            "url": "https://example.com",
            "formats": ["markdown"]
        },
        headers=client_headers,
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "markdown" in data["data"]
```

#### 测试用例 13：team/credit-usage E2E 测试

```python
@pytest.mark.skipif(
    not os.getenv("FCAM_E2E_ALLOW_UPSTREAM"),
    reason="E2E test with real upstream API disabled"
)
def test_e2e_team_credit_usage(client: TestClient, client_headers: dict):
    response = client.get(
        "/v2/team/credit-usage",
        headers=client_headers,
    )
    
    assert response.status_code == 200
    data = response.json()
    assert "remainingCredits" in data["data"]
    assert isinstance(data["data"]["remainingCredits"], (int, float))
```

---

## 7. 测试执行计划

### 7.1 测试文件组织

```
tests/
├── integration/
│   └── test_firecrawl_v2_missing_endpoints.py  # 单元测试 + 集成测试
└── e2e/
    └── test_e2e_firecrawl_v2_compatibility.py  # E2E 测试
```

### 7.2 测试执行命令

**运行单元测试**：
```bash
pytest tests/integration/test_firecrawl_v2_missing_endpoints.py -v
```

**运行集成测试**：
```bash
pytest tests/integration/test_firecrawl_v2_missing_endpoints.py -v -k "forwa``

**运行 E2E 测试**：
```bash
export FCAM_E2E="1"
export FCAM_E2E_ALLOW_UPSTREAM="1"
export FCAM_E2E_FIRECRAWL_API_KEY="fc-xxx"
pytest tests/e2e/test_e2e_firecrawl_v2_compatibility.py -v
```

**检查测试覆盖率**：
```bash
pytest --cov=app/api/firecrawl_v2_compat --cov-report=html
```

---

## 8. 验收标准

### 8.1 功能验收

- [ ] 所有 10 个端点都能正常调用（不返回 404）
- [ ] 所有端点都需要鉴权（无 token 返回 401）
- [ ] 所有端点设置正确的 endpoint 标识
- [ ] 显式端点优先于通配符路由匹配

### 8.2 兼容性验收（无缝切换）

- [ ] 请求参数正确传递（查询参数、请求体、请求头）
- [ ] 响应格式与官方 API 完全一致
- [ ] 错误响应与官方 API 一致（401、402、429、500）
- [ ] E2E 测试通过（至少 P0 端点）

### 8.3 测试覆盖率验收

- [ ] 整体覆盖率 ≥ 80%
率 = 100%
- [ ] 所有测试用例通过

### 8.4 性能验收

- [ ] 转发延迟 < 100ms（P95）
- [ ] 无明显性能退化

---

## 9. 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| Mock 测试不准确 | 生产环境出现问题 | 增加 E2E 测试覆盖 |
| 上游 API 变更 | 兼容性破坏 | 定期同步 OpenAPI 规范 |
| 测试环境不稳定 | 测试失败 | 使用 mock 减少对外部依赖 |

---

## 10. 参考文档

- **PRD**: `docs/PRD/2026-02-25-firecrawl-v2-missing-endpoints.md`
- **FD**: `docs/FD/2026-02-25-firecrawl-v2-missing-endpoints-fd.md`
- **OpenAPI 规范**: `api-reference/firecrawl-docs/api-reference/v2-openapi.json`
- **现有测试**: `tests/integration/test_firecrawl_v2_openapi_alignment.py`

---

## 11. 变更记录

| 日期 | 版本 | 变更内容 | 作者 |
|------|------|-------|
| 2026-02-25 | v1.0 | 初始版本 | Claude |

---

**TDD 文档编写完成** ✅
