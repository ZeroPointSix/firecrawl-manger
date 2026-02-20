# Docker 部署（MVP / 生产建议）

> 本文关注“怎么跑起来 + 数据库是什么 + 怎么持久化”。  
> 更完整的接口语义与错误体：`docs/MVP/Firecrawl-API-Manager-API-Contract.md`。
> ClawCloud Run（免费容器）部署与常见卷权限问题：`docs/deploy-clawcloud.md`。

## 0. 数据库现在用什么？

- **MVP 默认：SQLite**  
  - 配置：`database.path=./data/api_manager.db`（见 `config.yaml`）  
  - 容器内路径：`/app/data/api_manager.db`  
  - 持久化：把宿主机 `./data` 挂载到容器 `/app/data`
- **生产推荐：Postgres +（可选）Redis**  
  - 配置：推荐只设置 **一个** DSN（系统会自动做 alias，保证迁移期/运行期一致）  
    - `FCAM_DATABASE__URL=postgresql+psycopg://...`（推荐）  
    - （兼容）`FCAM_DATABASE_URL=postgresql+psycopg://...`  
  - 多实例一致性/限流/并发建议配合 `state.mode=redis`

> 说明：镜像启动时会自动执行 `alembic upgrade head`（见 `scripts/entrypoint.sh`）。

---

## 1. MVP（单容器 + SQLite，最省事）

适用：单机/内网/低并发；想快速验证 UI 与接口。

> 提示：`docker-compose.yml` 同时声明了 `image` + `build`：
> - 你本地开发时可直接 `--build`，默认 tag 为 `guangshanshui/firecrawl-manager:latest`
> - 远程机器想“只拉镜像不构建”，用 `--no-build`（见下方命令）

### 1.1 准备 `.env`（推荐）

复制示例并修改机密：

```bash
cp ".env.example" ".env"
```

至少设置：
- `FCAM_ADMIN_TOKEN`
- `FCAM_MASTER_KEY`（必须稳定；更换等同全量失效）

### 1.2 启动

```bash
docker compose up --build
```

如你在远程机器上不想/不能从源码构建（推荐）：

```bash
# 可选：固定版本（例如 v0.1.7）
export FCAM_IMAGE="guangshanshui/firecrawl-manager:v0.1.7"

docker compose pull
docker compose up -d --no-build
```

默认端口绑定：
- 默认：仅本机 `127.0.0.1:8000`（避免误暴露控制面）
- 如需对外暴露：启动前设置 `FCAM_BIND_ADDR=0.0.0.0`

探活：
```bash
curl -sS "http://127.0.0.1:8000/healthz"
curl -sS "http://127.0.0.1:8000/readyz"
```

数据持久化（SQLite 文件）：
- 宿主机：`./data/api_manager.db`
- 备份：直接备份该文件即可

### 1.3 安全提醒（非常重要）

`docker-compose.yml` 的 MVP 形态是“数据面 + 控制面同端口”，如果你把 `8000` 暴露到公网，`/admin/*` 也会暴露。  
生产请用 `docker compose` 的 `prod` profile 做端口/网段隔离（见下方命令）。

---

## 2. 生产示例（Postgres + Redis + 端口隔离）

适用：需要更强并发/一致性；需要把控制面限制在内网/本机。

启动：
```bash
docker compose --profile prod up --build postgres redis fcam_api fcam_admin
```

默认端口约定（`prod` profile）：
- 数据面：`0.0.0.0:8000`（对外）
- 控制面：`127.0.0.1:8001`（仅本机回环；建议再叠加 VPN/零信任）

你需要在环境里提供（推荐写进 `.env`，不要进仓库）：
- `FCAM_ADMIN_TOKEN`
- `FCAM_MASTER_KEY`

---

## 3. 可选：自定义 `config.yaml`

默认镜像内置 `config.yaml`。如需覆盖，最简单方式是挂载：

```yaml
services:
  fcam:
    volumes:
      - "./config.yaml:/app/config.yaml:ro"
    environment:
      FCAM_CONFIG: "/app/config.yaml"
```

---

## 4. SQLite → Postgres 直迁（一次性工具）

适用：你已有 **本地/单机** 的 SQLite 数据（`./data/api_manager.db`），希望迁移到 Postgres。  
不适用：ClawCloud 上的 SQLite PVC（不稳定，见 `docs/deploy-clawcloud.md`）。

建议在维护窗口执行（避免源 SQLite 与目标 Postgres 同时写入导致不一致）：

0) 备份源 SQLite 文件，并确保其 schema 已升级到最新（同一份代码版本）：

```bash
export FCAM_DATABASE_URL="sqlite:///./data/api_manager.db"
python -m alembic upgrade head
```

1) 准备 Postgres 空库，并先把 schema 升级到最新：

```bash
export FCAM_DATABASE_URL="postgresql+psycopg://USER:PASSWORD@HOST:5432/DB"
python -m alembic upgrade head
```

2) 执行直迁：

```bash
python -m app.tools.migrate_sqlite_to_postgres \
  --sqlite-path "./data/api_manager.db" \
  --postgres-url "postgresql+psycopg://USER:PASSWORD@HOST:5432/DB" \
  --verify
```

3) 切换服务到 Postgres（只设置一个 DSN 即可）：

- `FCAM_DATABASE__URL=postgresql+psycopg://...`（推荐）
