# Firecrawl API 兼容性修复方案（v1/v2）与测试改造计划

> **创建时间**: 2026-02-18  
> **状态**: Implemented（已落地到代码与测试）  
> **目标**: 用户使用 Firecrawl SDK / 直接 HTTP 调用时，仅替换 `api_url/base_url` 与 `api_key`（改为 FCAM Client Token），无需改业务代码即可切换到 FCAM。

---

## 1. 总体结论（结论先行）

当前 FCAM 的核心问题不是“转发能力”，而是 **路径语义与版本策略**：仓库内约定 `firecrawl.base_url` 必须包含 `/v1`，并且仅提供 `/api/*` 与 `/v1/*` 入站路由，导致 Firecrawl v2 客户端（`/v2/*`）迁移时出现 404/配置校验失败/Key Test 失败等不兼容。

**推荐修复方向**：
1. **增加 `/v2/*` 兼容层**（建议采用“透明转发”策略，尽量不硬编码端点清单）。
2. **取消 `firecrawl.base_url` 强制 `/v1`**，并在转发层增加“版本路径拼接/去重”能力，兼容：
   - `firecrawl.base_url=https://api.firecrawl.dev`（官方推荐）
   - `firecrawl.base_url=https://api.firecrawl.dev/v1`（历史配置）
   - `firecrawl.base_url=https://api.firecrawl.dev/v2`（特殊配置）
3. **更新请求白名单**：放行 v2 的 `map/extract/batch` 等一级段名，并将白名单逻辑扩展到 `/v2/` 前缀。

---

## 2. 官方接口要点（通过 MCP/Context7 摘要）

> 来源均为 Firecrawl 官方文档（`docs.firecrawl.dev`），通过 MCP（Context7）检索得到。

### 2.1 Base URL & 鉴权
- Base URL（官方）：`https://api.firecrawl.dev`
- 鉴权头：`Authorization: Bearer <api_key>`

在 FCAM 场景下：`<api_key>` 应为 **FCAM Client Token**（而不是 Firecrawl 官方 key），这样 SDK 的鉴权头保持一致。

### 2.2 v2 端点（以 `/v2/*` 为主）
以下端点在官方文档中出现（不同页面对 crawl/batch 的“start/status”形态存在差异，因此 FCAM 侧更适合做“透明转发 + 少量别名”）：

- `POST /v2/scrape`
- `POST /v2/search`
- `POST /v2/map`
- `POST /v2/extract`、`POST /v2/extract/start`、`GET /v2/extract/status/{id}`
- `POST /v2/agent`
- Crawl（文档同时出现两套形态）：
  - `POST /v2/crawl`、`GET /v2/crawl/{jobId}`（features/crawl）
  - `POST /v2/crawl/start`、`GET /v2/crawl/status/{id}`（migrate-to-v2）
  - `POST /v2/crawl/cancel/{id}`、`GET /v2/crawl/errors/{id}`、`POST /v2/crawl/params-preview`
- Batch scrape（文档同时出现多种形态）：
  - `POST /v2/batch/scrape`、`GET /v2/batch/scrape/{id}`（features/batch-scrape）
  - `POST /v2/batch/scrape/start`、`GET /v2/batch/scrape/status/{id}`、`GET /v2/batch/scrape/errors/{id}`（migrate-to-v2）
  - API reference 里也出现 `GET /batch/scrape/{id}/errors`（路径风格不同）

**结论**：FCAM 应优先保证“SDK/HTTP 客户端请求到什么路径，就转发什么路径”，避免因为我们硬编码了某一套形态而造成兼容性损失。

---

## 3. 现状与不兼容点（按严重程度排序）

### P0-1：`firecrawl.base_url` 强制包含 `/v1`
位置：`app/config.py`  
影响：
- 无法配置 `https://api.firecrawl.dev`（官方 base url）或 `/v2` 相关配置；
- 使“路径版本在入站 URL 中体现（/v1、/v2）”的设计无法落地。

### P0-2：缺失 `/v2/*` 入站路由（导致 SDK/HTTP 调用 404）
位置：路由仅有 `app/api/data_plane.py`（`/api/*`）与 `app/api/firecrawl_compat.py`（`/v1/*`）  
影响：
- 用户调用 `/v2/scrape` 等 v2 端点直接 404；
- 无法做到“只改 api_url/base_url 与 api_key”。

### P0-3：请求白名单未覆盖 `/v2/` 且 allowed_paths 不含 `map/extract/batch`
位置：`app/middleware.py` + `app/config.py` 默认 `security.request_limits.allowed_paths`  
影响：
- 即使新增 `/v2/*` 路由，也需要同步更新白名单，否则会被 `PATH_NOT_ALLOWED` 拦截；
- `map/extract/batch` 必须加入 allowed_paths，否则仍不可用。

### P1：Key Test 端点硬编码为 `POST /scrape`
位置：`app/core/forwarder.py::test_key`  
影响：
- 当上游配置为 root（无 `/v1` 前缀）时，`/scrape` 可能不是合法端点（应为 `/v1/scrape` 或 `/v2/scrape`）；
- 当我们希望以 v2 作为默认路径时，Key Test 的探测路径需要可推导/可配置。

---

## 4. 设计方案（建议）

### 4.1 路由层：新增 `/v2/*` 兼容层（透明转发）
新增 `app/api/firecrawl_v2_compat.py`（命名可讨论），路由前缀 `/v2`：
- 支持 `GET/POST`（优先覆盖官方文档出现的方法）
- `upstream_path` 直接使用 **原始请求路径 + query**：
  - `upstream_path = request.url.path + ("?" + request.url.query if request.url.query else "")`
- 依赖：
  - `Depends(enforce_client_governance)`（保持治理策略一致）
  - `Depends(require_client)`（鉴权保持一致）

> 这样做的核心收益：无需枚举 v2 全量端点，且天然兼容未来 Firecrawl v2 新增的子路径。

#### 别名路由（要求）
为最大化兼容（官方文档/示例中同一语义存在多种路径形态），FCAM **必须**提供少量“别名路由”，将常见变体重写到主路径（仅做“语义等价”的重写；其他路径保持透明转发）：
- `POST /v2/crawl/start` → `POST /v2/crawl`
- `GET  /v2/crawl/status/{id}` → `GET /v2/crawl/{id}`
- `POST /v2/batch/scrape/start` → `POST /v2/batch/scrape`
- `GET  /v2/batch/scrape/status/{id}` → `GET /v2/batch/scrape/{id}`

约束：
- 必须保留 query string（如 `?skip=...`、`?next=...`）原样透传。
- `POST` 的 body 与 headers 必须原样透传（只改路径）。
- errors 路径 **先不做** 重写，避免猜错上游形态；后续如验证到明确的“等价别名”，再补充。

### 4.2 转发层：统一“上游 URL 解析/拼接”策略（兼容 base_url 是否含版本）
目标：支持同时转发 `/v1/*` 与 `/v2/*`，且兼容历史配置。

实际落地策略（已实现）：
- `Forwarder` 初始化时会对 `firecrawl.base_url` 做归一化：如果末尾是 `/v1` 或 `/v2`，会剥离版本后缀，仅保留 origin 作为 `httpx.Client(base_url=...)`；同时记录该版本提示（用于 Key Test 的探测优先级）。
- 转发时只使用 `upstream_path`（入站 path + query，包含 `/v1` 或 `/v2`）作为请求 URL，从而天然避免出现 `/v1/v2/...` 这样的错误拼接。

结果：兼容 `https://api.firecrawl.dev`（官方推荐）与历史 `.../v1` 配置，并支持同一实例同时转发 v1 与 v2。

### 4.3 配置层：放宽 base_url 校验 + 更新默认值/示例
- 移除 `firecrawl.base_url` 必须包含 `/v1` 的 validator
- 仅做规范化：去除尾部 `/`
- 更新 `config.yaml` 注释与示例为：
  - `firecrawl.base_url: "https://api.firecrawl.dev"`

### 4.4 安全白名单：扩展到 `/v2/` 并补齐 allowed_paths
改动点：
- `RequestLimitsMiddleware` 增加 `/v2/` 前缀识别
- `security.request_limits.allowed_paths` 默认值 & `config.yaml` 示例增加：
  - `map`, `extract`, `batch`

### 4.5 透明转发注意事项（要求）
#### 4.5.1 防止响应体被“截断”
现象：用户侧收到的 response body 字符串/JSON 被截断，内容不完整。

根因：`httpx` 默认会自动解压 `gzip/br` 等编码；如果 FCAM 在把上游响应构造成 FastAPI `Response` 时 **原样透传** 上游的 `Content-Encoding` 与压缩态 `Content-Length`，就会出现：**body 实际是解压后的长度，但 header 仍是压缩前的长度**，客户端按旧 `Content-Length` 读取会被截断。

要求：FCAM 转发响应时必须保证 `Content-Length/Content-Encoding` 与实际 body 一致。当前实现采取的策略是：**丢弃上游的 `Content-Encoding/Content-Length`，由 FastAPI 重新计算**。

> 备注：若未来要做到“byte-level 完全透明转发”（包括压缩编码原样透传），需要改为 raw stream 方式转发并保留上游编码头。

#### 4.5.2 request log 必须包含上游真实错误（便于排查）
要求：当上游返回非 2xx（例如 400/402/429/5xx），`/admin/logs` 中的 `error_details` 必须能看到上游返回的错误 body 预览（JSON 会尽量解析为 `body_json`），避免仅记录 FCAM 的占位符错误信息。

---

## 5. 测试改造计划（先测后改）

> 按约定：先写/改测试，再实现。

### 5.1 单测：配置
- 更新 `tests/unit/test_config.py`
  - 删除/替换 “base_url 必须包含 /v1” 的断言
  - 新增：允许 `https://api.firecrawl.dev`、`.../v1`、`.../v2` 并统一去尾斜杠

### 5.2 单测：中间件白名单
- 更新 `tests/integration/test_middleware.py`
  - 新增：`POST /v2/evil` 在 allowed_paths={"scrape"} 时应返回 404 + `PATH_NOT_ALLOWED`
  - 新增：允许 `POST /v2/map`（当 allowed_paths 包含 "map"）

### 5.3 集成：路由→转发路径
- 更新 `tests/integration/test_api_data_plane.py`
  - 增加 v2 用例：`POST /v2/scrape`、`POST /v2/map`、`POST /v2/extract`、`POST /v2/batch/scrape`、`GET /v2/crawl/abc`
  - 增加 v2 别名用例（重写到主路径）：
    - `POST /v2/crawl/start` → upstream `/v2/crawl`
    - `GET  /v2/crawl/status/abc` → upstream `/v2/crawl/abc`
    - `POST /v2/batch/scrape/start` → upstream `/v2/batch/scrape`
    - `GET  /v2/batch/scrape/status/abc` → upstream `/v2/batch/scrape/abc`
  - 调整 base_url fixture（建议改为 `http://firecrawl.test`，由期望路径显式包含 `/v1` 或 `/v2` 来断言）
  - 保留 `/api/*` 仍转发到 `/v1/*` 的行为（向后兼容）

### 5.4 单测：Forwarder base_url 归一化（已覆盖）
- 允许 `firecrawl.base_url` 为 root / `/v1` / `/v2`（统一去尾斜杠）
- 转发时不应出现 `/v1/v2`、`/v2/v1` 等错误拼接
- Key Test 优先探测 `/v2/scrape`，不支持再回退 `/v1/scrape`

### 5.5 单测：响应头一致性 + 上游错误详情入库
- 转发响应时：不应保留上游的 `Content-Encoding/Content-Length`（避免解压后长度不一致导致截断）
- 请求日志：当返回非 2xx 且不是 FCAM 自身抛出的 `FcamError`，也应写入上游响应的错误详情预览（`error_details`）

---

## 6. 验证命令（迁移视角）

### 6.1 cURL（v2）
```bash
curl -X POST http://<fcam-host>:8000/v2/scrape \
  -H "Authorization: Bearer <FCAM_CLIENT_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com","formats":["markdown"]}'
```

### 6.2 Firecrawl Python SDK（概念验证）
> 以官方文档提到的 `api_url` 配置方式为准（此处仅展示迁移形态）。
```python
from firecrawl import Firecrawl

app = Firecrawl(
    api_key="FCAM_CLIENT_TOKEN",
    api_url="http://<fcam-host>:8000",
)

doc = app.scrape("https://example.com", formats=["markdown"])
print(doc)
```

---

## 7. 已确认的决策与要求（实现前冻结）

1. `/api/*` 继续固定转发到上游 `/v1/*`（避免影响现有内部调用方/测试），`/v2/*` 另起兼容层。
2. `/v2/*` 采用“透明转发（catch-all）”作为主策略。
3. `/v2/*` 必须实现别名重写（`crawl` 与 `batch/scrape` 的 `start/status` 形态，包含 GET+POST，详见 4.1）。
