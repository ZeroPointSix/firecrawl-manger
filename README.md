# Firecrawl API Manager（FCAM）

FCAM 是一个可容器化部署的轻量级 HTTP 网关，用于集中管理多把 Firecrawl API Key，并向内部业务提供统一的 `/api/*` 转发入口与 `/admin/*` 控制面。

## 单一事实来源（先读文档）

- 技术方案/语义/失败策略/开发规则：`agent.md`
- API 接口契约（请求/响应/错误体/分页/示例）：`Firecrawl-API-Manager-API-Contract.md`
- API 使用指南（面向调用方/运维，上手/配置/示例）：`API-Usage.md`
- 实施代办清单（里程碑顺序）：`TD.md`（`TODO.md` 仅兼容入口）
- 产品需求：`Firecrawl-API-Manager-PRD.md`

## 本地开发（不使用 Docker）

### Windows（无全局 Python 时可用）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts/bootstrap-python.ps1"
```

启动服务：
```powershell
& ".venv/Scripts/python.exe" -m alembic upgrade head
& ".venv/Scripts/python.exe" -m uvicorn "app.main:app" --host "0.0.0.0" --port 8000
```

浏览器入口：
- Swagger：`GET /docs`（`server.enable_docs=true`）
- 内置 WebUI：`GET /ui/`（`server.enable_control_plane=true`；顶部横向标签：概览/Keys/Clients/Logs/Audit/帮助；Admin Token 可配置持久化/过期；Dashboard 提供 `/api/scrape` 端到端自检）

### 通用（已安装 Python）

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

export FCAM_ADMIN_TOKEN=dev_admin_token
export FCAM_MASTER_KEY=dev_master_key_32_bytes_minimum____

alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

运行测试（覆盖率门禁）：
```bash
pytest --cov=app --cov-fail-under=80
```

## 实施日志

- 变更/选择/阻塞记录：`WORKLOG.md`

## Docker（dev）

```bash
docker compose up --build
```

探活：
- `GET /healthz`
- `GET /readyz`

指标（Prometheus）：
- 默认关闭；开启：`config.yaml` 设置 `observability.metrics_enabled: true`
- 访问：`GET /metrics`（可通过 `observability.metrics_path` 修改路径）

清理任务（保留策略）：
- 执行：`python "scripts/cleanup.py"`（读取 `config.yaml` + env 覆盖）
- 配置：`observability.retention.request_logs_days` / `observability.retention.audit_logs_days`

## 常见问题

### 1) `ADMIN_UNAUTHORIZED: Missing or invalid admin token`

含义：你在调用 `/admin/*` 时，**没有携带** `Authorization: Bearer <admin_token>`，或携带的 token 与服务端启动时读取到的 `FCAM_ADMIN_TOKEN` **不一致**。

常见原因：
- 你在 Swagger（`/docs`）里直接点 `Try it out` 调 `/admin/*`：Swagger 默认不会自动加 `Authorization` 头，因此会 401（推荐改用 `/ui/` 或命令行请求）。
- 你访问的是“另一个端口/另一个进程”的实例（例如本机 `8000/8001` 已被占用），该实例的 `FCAM_ADMIN_TOKEN` 与你输入的不一致。
- 你在某个 PowerShell 窗口里设置了 `$env:FCAM_ADMIN_TOKEN=...`，但 uvicorn 是在**另一个窗口**启动的（环境变量只对当前进程/子进程生效）。
- `/ui/` 如选择“仅本次（内存）”或 token 已过期/被清空，需要重新输入并点击“保存”。

自检（PowerShell 示例，注意不要在工单/群里粘贴明文 token）：
```powershell
$h=@{ Authorization = "Bearer <your_admin_token>" }
Invoke-RestMethod -Method GET -Uri "http://127.0.0.1:8000/admin/keys" -Headers $h
```

## Docker（prod 示例：Postgres + Redis + 端口隔离）

生产示例 compose：`docker-compose.prod.yml`

- 数据面（/api）：对外暴露 `:8000`（`server.enable_control_plane=false`）
- 控制面（/admin）：仅绑定 `127.0.0.1:8001`（`server.enable_data_plane=false`）
- 多实例一致性：`state.mode=redis`（并发/限流/冷却）

```bash
docker compose -f docker-compose.prod.yml up --build
```
