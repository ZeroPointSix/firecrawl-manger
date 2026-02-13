# FCAM 使用与接口手册（给接入方/运维）

> 目标：让“另一个服务”在不读代码的情况下，完成 **部署、发放 Token、调用 /api 或 /v1 兼容层、排障**。  
> **接口字段/分页/错误体的单一事实来源**：`Firecrawl-API-Manager-API-Contract.md`。  
> **上手与迁移指南（更完整示例）**：`API-Usage.md`。

## 1. 你接入 FCAM 需要知道的三件事

1) **业务服务不再持有 Firecrawl API Key**：业务侧只持有 FCAM 发放的 **Client Token**。  
2) **FCAM 有两个平面**：  
   - 数据面：`/api/*` 与 `/v1/*`（给业务调用，Client Token 鉴权）  
   - 控制面：`/admin/*`（给运维用，Admin Token 鉴权）  
3) **`FCAM_MASTER_KEY` 必须稳定**：用于解密落库的 Firecrawl Key、验证 Client Token（更换等同“全量失效”）。

---

## 2. 快速开始（最小可用链路）

### 2.1 启动服务（Ubuntu 24.04，最小命令）

> 说明：以下为“非 Docker”本地运行示例；生产部署建议参考 `docker-compose.prod.yml` 并做控制面隔离。
>
> 如需 Docker 部署（MVP/生产示例与数据库说明）：见 `docs/docker.md`。

```bash
cd /path/to/firecrawl-manger

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export FCAM_ADMIN_TOKEN="change_me_admin"
export FCAM_MASTER_KEY="change_me_master_key_32_bytes_minimum____"

.venv/bin/python -m alembic upgrade head
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

探活：
```bash
curl -sS "http://127.0.0.1:8000/healthz"
curl -sS "http://127.0.0.1:8000/readyz"
```

### 2.2 运维初始化（控制面：配置 Key 池 + 发放 Client Token）

1) 添加 Firecrawl Key：`POST /admin/keys`  
2)（可选）测试 Key 真实可用：`POST /admin/keys/{id}/test`（会真实请求上游）  
3) 创建 client 并拿到 token：`POST /admin/clients`（token **只返回一次**）

PowerShell 示例见：`API-Usage.md`。

### 2.3 业务调用（数据面：/v1 兼容层优先）

建议接入方优先使用 **/v1 兼容层**，便于从 Firecrawl SDK/HTTP 调用最小迁移：

| 目的 | 方法 | 路径 | 鉴权 |
|---|---|---|---|
| scrape | POST | `/v1/scrape` | `Authorization: Bearer <CLIENT_TOKEN>` |
| crawl（创建） | POST | `/v1/crawl` | 同上 |
| crawl（查询） | GET | `/v1/crawl/{id}` | 同上 |
| search | POST | `/v1/search` | 同上 |
| agent | POST | `/v1/agent` | 同上 |

最小 curl：
```bash
export FCAM_ORIGIN="http://127.0.0.1:8000"
export FCAM_CLIENT_TOKEN="fcam_client_xxx" # 放到 Secret 系统，不要写入仓库

curl -sS \
  -H "Authorization: Bearer ${FCAM_CLIENT_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com"}' \
  "${FCAM_ORIGIN}/v1/scrape"
```

---

## 3. 接口一览（按职责划分）

> 详细请求/响应/错误体/分页过滤见：`Firecrawl-API-Manager-API-Contract.md`。

### 3.1 探活 / 指标

| 目的 | 方法 | 路径 | 说明 |
|---|---|---|---|
| 进程存活 | GET | `/healthz` | 不依赖 DB/Redis |
| 就绪检查 | GET | `/readyz` | 检查机密配置 + DB +（可选）Redis |
| 指标 | GET | `/metrics` | 默认关闭：`observability.metrics_enabled: true` |

### 3.2 控制面（/admin/*，Admin Token）

| 目的 | 方法 | 路径（示例） |
|---|---|---|
| Key 列表/创建/更新 | GET/POST/PUT | `/admin/keys`、`/admin/keys/{id}` |
| Key 测试（真实上游） | POST | `/admin/keys/{id}/test` |
| Client 创建/更新 | POST/PUT | `/admin/clients`、`/admin/clients/{id}` |
| Client Token 轮换 | POST | `/admin/clients/{id}/rotate` |
| 请求日志查询 | GET | `/admin/logs?limit=50&cursor=...` |
| 审计日志查询 | GET | `/admin/audit-logs?limit=50&cursor=...` |
| 统计概览 | GET | `/admin/stats`、`/admin/stats/quota` |

### 3.3 数据面（/api/*，Client Token）

> 语义：对上游 Firecrawl `/v1/*` 做治理与转发；成功响应与上游错误默认透传。

| 目的 | 方法 | 路径 |
|---|---|---|
| scrape | POST | `/api/scrape` |
| crawl（创建） | POST | `/api/crawl` |
| crawl（查询） | GET | `/api/crawl/{id}` |
| search | POST | `/api/search` |
| agent | POST | `/api/agent` |

---

## 4. 接入方最佳实践（KISS 但不踩坑）

### 4.1 机密与配置

- **Client Token**：只写入你的 Secret 管理系统（K8s Secret / Vault / CI Secret），不要出现在日志/工单/群聊。  
- **Admin Token**：只给运维使用；控制面建议只在内网/VPN 暴露。  
- **Master Key**：必须稳定；更换会导致历史数据不可用、历史 token 全失效。

### 4.2 你应该加的两个 Header

- `X-Request-Id`：接入方生成并在自己日志中记录；用于和 `/admin/logs?request_id=...` 对齐排障。  
- `X-Idempotency-Key`：强烈建议用于 `crawl/agent`，避免重试重复创建/重复扣费风险。

---

## 5. 部署建议（生产最小安全边界）

1) **控制面与数据面隔离**：生产推荐按 `docker-compose.prod.yml` 分端口/分网段暴露。  
2) **关闭 Swagger**：生产建议 `server.enable_docs=false`。  
3) **限制可转发路径与 body 大小**：见 `config.yaml: security.request_limits`。  
4) **限制公网暴露**：如必须公网暴露数据面，建议前置反向代理/WAF + IP allowlist + TLS/mTLS。

---

## 6. 排障最短路径

1) 先看就绪：`GET /readyz`（常见：缺少 `FCAM_ADMIN_TOKEN`/`FCAM_MASTER_KEY` 或 DB 不可用）  
2) 接入侧每次请求带 `X-Request-Id`  
3) 运维用 `GET /admin/logs?request_id=<id>` 精确定位一次调用（不要让接入方提供任何 token）
