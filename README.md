# Firecrawl API Manager（FCAM）

[![CI](https://github.com/ZeroPointSix/firecrawl-manger/actions/workflows/ci.yml/badge.svg)](https://github.com/ZeroPointSix/firecrawl-manger/actions/workflows/ci.yml)
[![Docker Image](https://img.shields.io/docker/v/guangshanshui/firecrawl-manager?label=docker&logo=docker)](https://hub.docker.com/r/guangshanshui/firecrawl-manager)

FCAM 是一个可容器化部署的轻量级 HTTP 网关，用于集中管理多把 Firecrawl API Key，并向内部业务提供统一的 `/api/*` 转发入口与 `/admin/*` 控制面。

## 文档导航

- 技术方案/语义/失败策略/开发规则：`docs/agent.md`
- API 接口契约（请求/响应/错误体/分页/示例）：`docs/MVP/Firecrawl-API-Manager-API-Contract.md`
- API 使用指南（面向调用方/运维，上手/配置/示例）：`docs/API-Usage.md`
- 接入方/运维快速手册（接口一览 + 部署要点）：`docs/handbook.md`
- Docker 部署（MVP/生产示例 + 数据库说明）：`docs/docker.md`
- 部署排障/经验总结（容器云/ClawCloud）：`docs/Exp/README.md`
- 产品需求：`docs/MVP/Firecrawl-API-Manager-PRD.md`

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
- WebUI（Vue/UI2）：`GET /ui2/`（需先在 `webui/` 执行 `npm ci` + `npm run build` 生成 `app/ui2/`；该目录在 `.gitignore` 中，仅本地/发布构建时存在；`/ui/` 会 307 跳转到 `/ui2/`）

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

真实 API E2E 测试（不使用 TestClient，不写 DB seed；通过 HTTP 请求驱动）：
```powershell
$env:FCAM_E2E="1"
& ".venv/Scripts/python.exe" -m pytest -q "tests/e2e/test_e2e_real_api.py"
```

可选：启用“真实上游”（会真实调用 Firecrawl，有配额/费用风险，请仅在隔离环境执行）：
```powershell
$env:FCAM_E2E="1"
$env:FCAM_E2E_ALLOW_UPSTREAM="1"
$env:FCAM_E2E_FIRECRAWL_API_KEY="<your_firecrawl_api_key>"
# $env:FCAM_E2E_SCRAPE_URL="https://example.com"  # 可选
& ".venv/Scripts/python.exe" -m pytest -q "tests/e2e/test_e2e_real_api.py::test_e2e_firecrawl_compat_scrape_success_with_real_upstream"

```

如不想在命令行暴露 key，可在仓库根目录创建本地文件 `.env.e2e`（已被 `.gitignore` 忽略），写入：
```text
FCAM_E2E_FIRECRAWL_API_KEY=...
FCAM_E2E_SCRAPE_URL=https://example.com
```

## 实施日志

- 变更/选择/阻塞记录：`docs/WORKLOG.md`

## Docker（dev）

```bash
docker compose up --build
```

### 常见问题：`sqlite3.OperationalError: unable to open database file`

默认 SQLite 数据库在容器内路径为 `/app/data/api_manager.db`。镜像以非 root 用户运行（uid `10001`），如果你用宿主机目录挂载 `-v $(pwd)/data:/app/data`，宿主机的 `./data` 可能对该 uid 不可写，导致启动时 `alembic upgrade head` 失败。

修复方式（二选一）：

1) 继续用宿主机目录（Linux 服务器上执行）：
```bash
mkdir -p ./data
sudo chown -R 10001:10001 ./data
```

2) 改用命名卷（避免权限问题）：
```bash
docker volume create fcam_data
docker run ... -v fcam_data:/app/data ...
```

如需固定版本（例如 `v0.1.7`）：

```bash
export FCAM_IMAGE="guangshanshui/firecrawl-manager:v0.1.7"
docker compose pull
docker compose up -d --no-build
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
- 你在 Swagger（`/docs`）里直接点 `Try it out` 调 `/admin/*`：Swagger 默认不会自动加 `Authorization` 头，因此会 401（推荐改用 `/ui2/` 或命令行请求）。
- 你访问的是“另一个端口/另一个进程”的实例（例如本机 `8000/8001` 已被占用），该实例的 `FCAM_ADMIN_TOKEN` 与你输入的不一致。
- 你在某个 PowerShell 窗口里设置了 `$env:FCAM_ADMIN_TOKEN=...`，但 uvicorn 是在**另一个窗口**启动的（环境变量只对当前进程/子进程生效）。
- `/ui2/` 如选择“仅本次（内存）”或 token 已过期/被清空，需要重新输入并点击“保存”。

自检（PowerShell 示例，注意不要在工单/群里粘贴明文 token）：
```powershell
$h=@{ Authorization = "Bearer <your_admin_token>" }
Invoke-RestMethod -Method GET -Uri "http://127.0.0.1:8000/admin/keys" -Headers $h
```

## Docker（prod 示例：Postgres + Redis + 端口隔离）

```bash
docker compose --profile prod up --build postgres redis fcam_api fcam_admin
```

- 数据面（/api）：对外暴露 `:8000`（`server.enable_control_plane=false`）
- 控制面（/admin）：仅绑定 `127.0.0.1:8001`（`server.enable_data_plane=false`）
- 多实例一致性：`state.mode=redis`（并发/限流/冷却）

## Docker（public：仅数据面）

```bash
docker compose --profile public up --build fcam_public
```
