# Firecrawl API 兼容性问题与迁移清单

> **创建时间**: 2026-02-18
> **优先级**: P0（阻塞用户无缝迁移）
> **影响范围**: 数据面 API 转发、配置验证、用户迁移体验

---

## 问题概述

当前 FCAM 的实现与 Firecrawl 官方 API 规范存在多处不兼容，导致用户无法通过"单纯替换 URL 和 API Key"的方式从 Firecrawl 官方服务无缝迁移到 FCAM。

**核心目标**：确保用户使用 Firecrawl SDK 或直接调用 API 时，只需修改 `base_url` 和 `api_key`，无需修改任何业务代码即可切换到 FCAM。

---

## 问题清单

### 1. API 版本路径不匹配（P0）

**问题描述**：
- **Firecrawl 官方**: Base URL 为 `https://api.firecrawl.dev`，端点为 `/v2/scrape`、`/v2/crawl` 等
- **FCAM 当前实现**:
  - 配置强制要求 `base_url` 必须以 `/v1` 结尾（`app/config.py:28-34`）
  - 数据面端点为 `/api/scrape`、`/api/crawl`（`app/api/data_plane.py`）
  - 转发时直接拼接路径，例如 `upstream_path="/scrape"`（`forwarder.py:33`）

**影响**：
- 用户配置 `base_url: "https://api.firecrawl.dev/v2"` 会被拒绝（验证失败）
- 即使绕过验证，转发路径会变成 `/v2/scrape/scrape`（重复）

**相关代码**：
```python
# app/config.py:28-34
@field_validator("base_url")
def _base_url_must_include_v1(cls, v: str) -> str:
    if not normalized.endswith("/v1"):
        raise ValueError("必须包含 /v1")
```

```python
# app/core/forwarder.py:176
with httpx.Client(
    base_url=self._config.firecrawl.base_url,  # 例如 "https://api.firecrawl.dev/v1"
    ...
) as client_http:
    resp = client_http.request(
        method=method,
        url=upstream_path,  # 例如 "/scrape"
        ...
    )
```

**建议修复**：
1. 移除 `base_url` 的 `/v1` 强制验证，改为支持任意版本（`/v1`、`/v2` 或无版本）
2. 转发逻辑改为完整路径拼接，例如：
   - 配置 `base_url: "https://api.firecrawl.dev"`
   - 转发时使用 `upstream_path="/v2/scrape"`

---

### 2. 缺失的 Firecrawl v2 端点（P0）

**问题描述**：
根据 Firecrawl 官方文档，v2 API 包含以下端点，但 FCAM 当前未实现：

| 端点 | 方法 | 状态 | 说明 |
|------|------|------|------|
| `/v2/scrape` | POST | ❌ 缺失 | 抓取单个 URL |
| `/v2/crawl` | POST | ❌ 缺失 | 启动爬取任务（异步） |
| `/v2/crawl/{id}` | GET | ❌ 缺失 | 查询爬取状态 |
| `/v2/crawl/cancel/{id}` | POST | ❌ 缺失 | 取消爬取任务 |
| `/v2/crawl/erro}` | GET | ❌ 缺失 | 获取爬取错误 |
| `/v2/map` | POST | ❌ 缺失 | 获取网站 URL 列表 |
| `/v2/search` | POST | ❌ 缺失 | 搜索网页内容 |
| `/v2/extract` | POST | ❌ 缺失 | 提取结构化数据 |
| `/v2/agent` | POST | ❌ 缺失 | AI 代理模式 |
| `/v2/batch/scrape/start` | POST | ❌ 缺失 | 批量抓取 |
| `/v2/batch/scrape/status/{id}` | GET | ❌ 缺失 | 批量抓取状态 |

**当前实现**：
- FCAM 仅实现了 `/api/scrape`、`/api/crawl`、`/api/search`、`/api/agent`（`app/api/data_plane.py`）
- 缺少 `/v2` 前缀的路由
- 缺少 `map`、`extract`、`batch` 等端点
- 缺少 `crawl/{id}`、`crawl/cancel/{id}` 等异步任务管理端点

**影响**：
- 用户使用 Firecrawl SDK 调用 `/v2/map` 或 `/v2/extract` 会收到 404
- 无法管理异步爬取任务（查询状态、取消任务）
\n1. 新增 `/v2/*` 路由组，完整映射 Firecrawl v2 API
2. 补齐缺失端点：
   - `/v2/map` - 转发到上游
   - `/v2/extract` - 转发到上游
   - `/v2/crawl/cancel/{id}` - 转发到上游
   - `/v2/crawl/errors/{id}` - 转发到上游
   - `/v2/batch/*` - 转发到上游

---

### 3. 测试端点硬编码（P1）

**问题描述**：
- Key 测试功能硬编码了 `/scrape` 端点（`forwarder.py:375`）
- 如果上游服务使用 `/v2/scrape`，测试会失败

**相关代码**：
```python
# app/core/forwarder.py:375
resp = client_http.request(
    method="POST",
    url="/scrape",  # 硬编码
    headers=upstream_headers,
    json={"url": test_url},
)
```

**建议修复**：
- 测试端点应从配置读取或根据 `base_url` 自动推导版本

---

### 4. 兼容层路径映射不完整（P1）

**问题描述**：
- 当前存在 `/v1/*` 兼容层（根据 WORKLOG.md 提到的"安全一致性"修复）
- 但缺少 `/v2/*` 兼容层

**建议修复**：
- 同时支持 `/api/*`、`/v1/*`、`/v2/*` 三种路径前缀
- 统一转发到上游对应端点

---

## 迁移路径设计

### 方案 A：完全兼容 Firecrawl v2（推荐）

**目标**：用户无需修改代码，只需替换 SDK 配置中的 `base_url` 和 `api_key`

**实现步骤**：
1. 新增 `/v2/*` 路由组，完整映射 Firecrawl v2 所有端点
2. 移除 `base_url` 的 `/v1` 强制验证
3. 配置示例改为：
   ```yaml
   firecrawl:
     base_url: "https://api.firecrawl.dev"  # 不包含版本号
   ```
4. 转发时使用完整路径：
   ```python
   # 用户请求: POST /v2/scrape
   # 转发到: POST https://api.firecrawl.dev/v2/scrape
   upstream_path = request.url.path  # "/v2/scrape"
   ```

**优点**：
- 用户体验最佳，真正做到"无缝迁移"
- 与 Firecrawl SDK 完全兼容

**缺点**：
- 需要补齐所有 v2 端点（工作量较大）

---

### 方案 B：路径重写（折中方案）

**目标**：保持当前 `/api/*` 端点，通过路径重写支持 `/v2/*`

**实现步骤**：
1. 新增中间件，将 `/v2/scrape` 重写为 `/api/scrape`
2. 转发时根据原始路径决定上游路径：
   ```python
   # 用户请求: POST /v2/scrape
   # 内部重写: POST /api/scrape
   # 转发到: POST https://api.firecrawl.dev/v2/scrape
   ```

**优点**：
- 无需修改现有路由逻辑
- 可以逐步补齐端点

**缺点**：
- 增加路径映射复杂度
- 仍需补齐缺失端点

---

## 验证清单

完成修复后，需验证以下场景：

### 场景 1：Firecrawl Python SDK 迁移
```python
from firecrawl import Firecrawl

# 原配置（官方服务）
# app = Firecrawl(api_key="fc-xxx")

# 迁移后（FCAM）
app = Firecrawl(
    api_key="client_token_xxx",  # FCAM Client Token
    api_url="http://your-fcam-host:8000"  # FCAM 地址
)

# 业务代码无需修改
result = app.scrape("https://example.com")
```

### 场景 2：直接 HTTP 调用
```bash
# 原请求（官方服务）
curl -X POST https://api.firecrawl.dev/v2/scrape \
  -H "Authorization: Bearer fc-xxx" \
  -d '{"url": "https://example.com"}'

# 迁移后（FCAM）
curl -X POST http://your-fcam-host:8000/v2/scrape \
  -H "Authorization: Bearer client_token_xxx" \
  -d '{"url": "https://example.com"}'
```

### 场景 3：所有端点可用性
- [ ] `/v2/scrape` - 200
- [ ] `/v2/crawl` - 200（返回任务 ID）
- [ ] `/v2/crawl/{id}` - 200（查询状态）
- [ ] `/v2/map` - 200
- [ ] `/v2/search` - 200
- [ ] `/v2/extract` - 200
- [ ] `/v2/agent` - 200

---

## 相关文件

- `app/config.py:28-34` - base_url 验证逻辑
- `app/core/forwarder.py:176` - 转发逻辑
- `app/core/forwarder.py:375` - Key 测试端点
- `app/api/data_plane.py` - 数据面路由
- `config.yaml:11` - base_url 配置示例
- `docs/MVP/Firecrawl-API-Manager-API-Contract.md` - API 契约文档

---

## 下一步行动

1. **立即修复**（P0）：
   - [ ] 移除 `/v1` 强制验证
   - [ ] 新增 `/v2/scrape`、`/v2/crawl`、`/v2/map`、`/v2/search`、`/v2/extract`、`/v2/agent` 路由
   - [ ] 修复转发路径拼接逻辑

2. **补齐端点**（P0）：
   - [ ] `/v2/crawl/{id}` - GET 查询状态
   - [ ] `/v2/crawl/cancel/{id}` - POST 取消任务
   - [ ] `/v2/map` - POST 获取 URL 列表
   - [ ] `/v2/extract` - POST 提取数据

3. **测试验证**（P0）：
   - [ ] 使用 Firecrawl Python SDK 进行端到端测试
   - [ ] 验证所有 v2 端点返回正确状态码
   - [ ] 更新 E2E 测试用例

4. **文档更新**（P1）：
   - [ ] 更新 `docs/API-Usage.md` 迁移指南
   - [ ] 更新 `config.yaml` 示例配置
   - [ ] 新增"从 Firecrawl 迁移"章节

---

## 参考资料

- Firecrawl 官方文档: https://docs.firecrawl.dev/
- Firecrawl v2 API 端点列表: https://docs.firecrawl.dev/api-reference/v2-introduction
- Firecrawl Python SDK: https://github.com/mendableai/firecrawl-py
