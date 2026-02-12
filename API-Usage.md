# FCAM API 使用指南（面向调用方/运维）

> 本文是“如何使用 FCAM”的上手手册；完整接口契约（字段/分页/错误体）以 `Firecrawl-API-Manager-API-Contract.md` 为准。

## 0. 你需要理解的三个概念

### 0.1 两个平面

- **数据面**：`/api/*` —— 给业务服务调用，用 **Client Token** 鉴权；FCAM 会在内部选择并使用某把 Firecrawl Key 进行转发。
- **控制面**：`/admin/*` —— 给运维/管理员使用，用 **Admin Token** 鉴权；用于管理 Firecrawl Key 池、发放/轮换 Client Token、查询日志等。

### 0.2 两种 Token

- **Admin Token**：环境变量 `FCAM_ADMIN_TOKEN`，用于调用 `/admin/*`。
- **Client Token**：由 `/admin/clients` 创建/轮换时返回一次，用于调用 `/api/*`（建议写入业务服务的 Secret 管理系统）。

### 0.3 Master Key（必须稳定）

环境变量 `FCAM_MASTER_KEY` 用于：
- 加密落库 Firecrawl API Key
- 哈希存储 Client Token（用于鉴权）

**不要随意更换 `FCAM_MASTER_KEY`**：更换会导致旧的 Firecrawl Key 无法解密、旧的 Client Token 无法通过鉴权（相当于“全量失效”）。

---

## 1. 启动与配置

### 1.1 最小启动（本地）

```powershell
# 1) 设置机密（示例，勿把明文写入工单/群）
$env:FCAM_ADMIN_TOKEN="dev_admin_token"
$env:FCAM_MASTER_KEY="dev_master_key_32_bytes_minimum____"

# 2) 迁移 + 启动
& ".venv/Scripts/python.exe" -m alembic upgrade head
& ".venv/Scripts/python.exe" -m uvicorn "app.main:app" --host "127.0.0.1" --port 18000
```

浏览器入口：
- `GET /ui/`：内置 WebUI（可用于发 Key、发 Client Token、看日志）
- `GET /docs`：Swagger（若启用）

### 1.2 `config.yaml` 与环境变量覆盖（推荐）

- 默认读取 `config.yaml`；可通过 `FCAM_CONFIG` 指定路径。
- 支持环境变量覆盖（前缀 `FCAM_`，嵌套用 `__`）：例如 `FCAM_SERVER__PORT=18000`。

常用覆盖示例：
```powershell
$env:FCAM_SERVER__PORT="18000"
$env:FCAM_FIRECRAWL__BASE_URL="https://api.firecrawl.dev/v1"   # 必须包含 /v1
$env:FCAM_FIRECRAWL__TIMEOUT="30"
$env:FCAM_FIRECRAWL__MAX_RETRIES="3"
```

生产建议（强烈）：
- 将数据面与控制面 **端口隔离/网段隔离**（详见 `docker-compose.prod.yml` 示例）。

---

## 2. 运维：配置 Key 池（Firecrawl Keys）

> 业务服务调用 `/api/*` 不需要 Firecrawl Key；Key 由 FCAM 在控制面统一管理。

### 2.1 创建 Key（控制面）

HTTP：
- `POST /admin/keys`
- Header：`Authorization: Bearer <FCAM_ADMIN_TOKEN>`

示例（PowerShell）：
```powershell
$origin="http://127.0.0.1:18000"
$h=@{ Authorization = "Bearer $env:FCAM_ADMIN_TOKEN" }

$body=@{
  api_key="fc-xxxxxxxxxxxxxxxx0001"
  name="free-01"
  plan_type="free"
  daily_quota=5
  max_concurrent=2
  rate_limit_per_min=10
  is_active=$true
} | ConvertTo-Json

Invoke-RestMethod -Method POST -Uri "$origin/admin/keys" -Headers $h -ContentType "application/json" -Body $body
```

### 2.2 Key 健康检查（是否能真实打到 Firecrawl）

HTTP：
- `POST /admin/keys/{id}/test`

要点：
- 该接口会 **真实调用** 上游 Firecrawl：`POST {firecrawl.base_url}/scrape`（body：`{"url": test_url}`）。
- 返回体包含 `ok` 与 `upstream_status_code`，用它们判断是否真的成功打通上游。

示例：
```powershell
$origin="http://127.0.0.1:18000"
$h=@{ Authorization = "Bearer $env:FCAM_ADMIN_TOKEN" }
$body=@{ mode="scrape"; test_url="https://www.google.com" } | ConvertTo-Json
Invoke-RestMethod -Method POST -Uri "$origin/admin/keys/1/test" -Headers $h -ContentType "application/json" -Body $body
```

---

## 3. 运维：发放 Client Token（给业务服务用）

### 3.1 创建 Client

HTTP：
- `POST /admin/clients`

返回：
- `token`：**只返回一次**（请立即复制保存）

示例（PowerShell）：
```powershell
$origin="http://127.0.0.1:18000"
$h=@{ Authorization = "Bearer $env:FCAM_ADMIN_TOKEN" }
$body=@{ name="service-a"; daily_quota=$null; rate_limit_per_min=60; max_concurrent=10; is_active=$true } | ConvertTo-Json
Invoke-RestMethod -Method POST -Uri "$origin/admin/clients" -Headers $h -ContentType "application/json" -Body $body
```

### 3.2 轮换 Client Token

HTTP：
- `POST /admin/clients/{id}/rotate`

建议：
- 轮换后旧 token 应尽快从业务服务侧撤销。

---

## 4. 调用方：通过 `/api/*` 使用 Firecrawl（scrape / crawl / agent）

> FCAM 的成功响应与上游错误响应默认透传；网关仅在自身拦截（鉴权/治理/无 Key/校验失败）时返回网关错误体。

### 4.1 通用调用约束（非常重要）

- Header：`Authorization: Bearer <CLIENT_TOKEN>`
- Body：必须是 `application/json`
- 路径白名单：默认只允许 `scrape|crawl|search|agent`（其他会返回 `PATH_NOT_ALLOWED`）
- 建议你自己传 `X-Request-Id`（便于对齐网关日志）；不传则网关自动生成并通过响应头回传。

### 4.2 scrape（单次抓取，通常同步返回）

- `POST /api/scrape`
- 上游：`POST {base_url}/scrape`

最小示例（PowerShell，payload 字段以 Firecrawl 为准）：
```powershell
$origin="http://127.0.0.1:18000"
$h=@{ Authorization = "Bearer <CLIENT_TOKEN>" }
$body=@{ url="https://example.com" } | ConvertTo-Json
Invoke-RestMethod -Method POST -Uri "$origin/api/scrape" -Headers $h -ContentType "application/json" -Body $body
```

### 4.3 crawl（爬取/任务型，通常需要轮询状态）

- `POST /api/crawl`：创建任务
- `GET /api/crawl/{id}`：查询状态（FCAM 透传上游）

建议：
- 业务侧传 `X-Idempotency-Key`（重试不会重复创建任务）。
- 如要强制所有调用都带幂等键，可在 `config.yaml` 配置 `idempotency.require_on=["crawl","agent"]`。

示例：
```powershell
$origin="http://127.0.0.1:18000"
$h=@{ Authorization = "Bearer <CLIENT_TOKEN>"; "X-Idempotency-Key"="crawl-$(New-Guid)" }
$body=@{ url="https://example.com" } | ConvertTo-Json
Invoke-RestMethod -Method POST -Uri "$origin/api/crawl" -Headers $h -ContentType "application/json" -Body $body
```

### 4.4 agent（智能 Agent，强烈建议幂等）

- `POST /api/agent`
- 上游：`POST {base_url}/agent`

建议：
- 业务侧强制传 `X-Idempotency-Key`（避免重复创建/重复扣费风险）。

---

## 5. 如何测试（三种方式）

### 5.1 本仓库的“模拟上游”单测（不会真实请求 Firecrawl）

本仓库测试使用 `httpx.MockTransport` 模拟上游，验证：
- `/api/scrape`、`/api/crawl`、`/api/agent` 确实转发到期望的上游路径（如 `/v1/scrape`）
- 幂等键逻辑（crawl/agent）可正确 replay / conflict
- `request_logs` 会记录 `/api/*` 入站请求

运行：
```bash
pytest -q tests/test_api_data_plane.py
```

### 5.2 UI 内的“数据面自检（/api/scrape）”（端到端但不自动保存 Client Token）

访问 `GET /ui/` → Dashboard → “数据面自检（/api/scrape）”。

### 5.3 真实联调（会真实请求 Firecrawl）

前提：
- 控制面已配置至少 1 把可用 Firecrawl Key（可用 `/admin/keys/{id}/test` 验证 `ok=true`）。
- 已发放 Client Token。

然后业务方直接调用 `/api/*` 即可；调用后可用 `/admin/logs` 查询本次请求记录。

