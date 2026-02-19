# FCAM WebUI2 前端方案（参考 gpt-load）

> 目标：在**不改变后端语义**（单一事实来源：`docs/agent.md` + `docs/MVP/Firecrawl-API-Manager-API-Contract.md`）的前提下，落地一套可维护、可扩展、排障友好的内置控制台：`GET /ui2/`。

## 0. 背景与约束

- WebUI2 是 FCAM 控制面的“效率工具”，主要面向运维/开发同学，降低 `/admin/*` 的日常操作与排障成本。
- 参考对象：`gpt-load` 的后台范式（Dashboard 指标 + 趋势、左侧分组/右侧表格详情、标准日志表格交互）。
- 安全默认：
  - **绝不展示/打印明文 token / api_key**。
  - Admin Token **默认不持久化**；如需持久化必须显式选择，并提供“一键清空”与过期机制。
- 交付约束：UI 以静态产物形式构建后放入 `app/ui2/`，由后端挂载 `StaticFiles` 提供服务；运行时不依赖 CDN。

## 1. 产品目标（P0）

P0 必须解决以下三个高频问题：

1) **Key 可编辑 + 可测试**：支持修改 key 配置字段（不含明文 api_key）并可一键测试健康状态。
2) **Key 批量操作**：对选中 keys 执行批量启用/禁用、批量改配额/限流/并发、批量清冷却、批量测试、批量软删除（尽力而为，允许部分失败并展示失败列表）。
3) **排障信息可视化**：日志页能看到 error_code，并可查看结构化 `error_details`（已脱敏+截断）用于定位问题。

## 2. 设计原则（对齐 gpt-load）

- **信息架构清晰**：顶部 Tabs（Dashboard / Clients & Keys / Logs / Audit）。
- **标准后台表格范式**：过滤区 + 表格 + 分页（游标分页/页码分页按后端契约）。
- **操作入口统一**：
  - 行内操作收敛到 `⋯` 菜单（测试/编辑/轮换/启用禁用/删除）。
  - 批量操作固定在表格工具栏（不塞进每行）。
- **尽量不新增 UI 专用后端 API**：所有 UI 行为优先复用 `/admin/*`；仅当“契约缺口”影响体验时才新增（需先改文档）。

## 3. 技术栈与工程结构

### 3.1 技术选型（当前即为推荐）
- Vue 3 + TypeScript
- Vue Router（hash history，base `/ui2/`）
- Naive UI（表格/弹窗/表单）
- axios（统一 HTTP 客户端，默认超时 30s）

### 3.2 前端目录（建议保持现状）
```text
webui/src/
  api/               # 与 /admin/* 对齐的调用封装与类型
  components/        # 通用组件（ConnectModal、StatCard、Chart、DetailDrawer...）
  state/             # 轻量状态（admin token / connection status）
  views/             # 页面（Dashboard、ClientsKeys、Logs、Audit）
  router/            # 路由与 Tabs
```

### 3.3 构建与交付
- 构建命令：`cd webui && npm ci && npm run build`
- 构建产物：输出到 `app/ui2/`（由 `webui/vite.config.ts` 指定 `outDir`）
- 后端挂载：`app/main.py` 控制面开启时挂载 `/ui2`
- 建议在 Docker 构建阶段生成产物并随镜像发布（避免线上环境需要 node/npm）。

## 4. 信息架构与路由

路由与 Tabs 一一对应：
- `#/dashboard`：仪表盘（全局健康与趋势）
- `#/clients`：Clients & Keys（左侧 client，右侧 key 表格）
- `#/logs`：请求日志（过滤 + 游标分页 + 详情）
- `#/audit`：审计日志（过滤 + 游标分页）

> 说明：hash 路由保证静态托管场景下“刷新不 404”。

## 5. 通用交互规范

### 5.1 连接（Admin Token）

建议交互（对齐 gpt-load 的 connect 模式）：
- Header 右上角固定 “连接/断开” 按钮，展示连接状态（ok/unauthorized/error）。
- 默认只保存在内存/同标签页；若用户勾选“本机持久化”，才写入 `localStorage`。
- 持久化时支持设置过期时间（小时）并显示“到期时间”；到期自动断开并清空本地存储。
- Token 永不打印到控制台；任何 UI 内部日志必须做脱敏（`Bearer …`、`fc-…`）。

### 5.2 错误展示与 request_id
- 所有 toast/错误提示尽量带上 `request_id`（从响应头 `X-Request-Id` 或响应体 `request_id` 提取），便于和服务端日志对齐。
- 对网关错误（`FcamError`）优先展示 `error.code: error.message`；对网络错误展示“网络不可达/超时”。

### 5.3 表格范式（通用）
- 工具栏（Toolbar）：刷新、搜索、列选择器、批量操作按钮。
- 列选择器：支持“全选/仅必选”，并持久化用户选择（`localStorage`）；必选列不可隐藏（如 Actions）。
- 批量操作：
  - 只有选中行时按钮可用；显示 `（n）` 数量。
  - 批量请求统一走 batch endpoint（如 `/admin/keys/batch`），并提供“部分失败列表”。

### 5.4 安全与脱敏
- UI 绝不展示明文 `api_key`（仅 masked）。
- 任何复制功能都只允许复制 masked 或 token（token 仅在创建/轮换时短暂展示，且需显式点击复制）。
- `error_details` 展示前需认为其“已脱敏”；但 UI 仍应对字符串做二次简单脱敏（防止意外字段透传）。

## 6. 页面级方案（P0）

### 6.1 Dashboard
- 顶部安全告警条：
  - `FCAM_MASTER_KEY` 未配置/不匹配导致的 decrypt failure 提示（来自 `/admin/encryption-status`）
- 指标卡片（4 个）：
  - keys 总数（含失败/不可解密数）
  - clients 总数
  - 24h 请求数（成功/失败）
  - 24h 错误率
- 趋势折线图（24h，1h bucket，本地时区显示）
- 支持按 client 过滤（下拉选择 client_id）

### 6.2 Clients & Keys（核心工作台）

**布局（对齐 gpt-load）**
- 左侧：Clients 列表（搜索、刷新、创建 Client）
- 右侧：Client 详情卡片 + Keys 表格

**Keys 表格（重点）**
- 工具栏包含：
  - 添加 Key（弹窗）
  - 文本导入（弹窗）
  - 批量编辑（弹窗，见 6.2.2）
  - 删除所选（Purge，危险操作，二次确认；不与“软删除”混淆）
  - name 搜索框（server-side：复用 `GET /admin/keys?q=` + 分页）
  - 列选择器（持久化）
- 行内 `⋯` 菜单包含：
  - 测试（弹窗）
  - 编辑（弹窗，仅改配置字段）
  - 启用/禁用（快速切换）
  - 轮换（弹窗，输入新 api_key）
  - 彻底删除（Purge，危险操作）

#### 6.2.1 “编辑 Key”（单条）
- 表单字段：
  - `name`、`plan_type`、`daily_quota`、`rate_limit_per_min`、`max_concurrent`、`is_active`
- 保存：调用 `PUT /admin/keys/{id}`（不包含 `api_key`）
- 可选：提供“保存并测试”按钮（保存成功后调用 `/admin/keys/{id}/test`）

#### 6.2.2 “批量编辑 Keys”（尽力而为）
- 调用 `POST /admin/keys/batch`（见接口契约 2.5.1）
- 交互建议：
  - 模式选择（多选）：批量启用/禁用、批量改配额/限流/并发、清冷却、批量测试、批量软删除
  - Patch 字段采用“显式启用开关 + 输入框”模式，避免误把空值写入
  - 提交后显示：
    - summary：requested/succeeded/failed
    - 失败列表：key_id + error.code + error.message
    - 成功项可折叠查看 test 结果（如有）
- 提交完成后自动刷新 Keys 表格。

### 6.3 Logs（请求日志）
- 过滤区：limit、level、client_id、endpoint、success、q（保持与 `/admin/logs` 一致）
- 表格列（P0）：
  - 时间、level、endpoint、status、耗时、client_id、api_key(masked)、retry、error_code、request_id
- 详情查看（P0 必须）：
  - 点击行或“详情”按钮，打开 Drawer/Modal
  - 展示 `error_details`（JSON 折叠展示 + 一键复制）
  - 展示 `request_id` 与“复制 request_id”

### 6.4 Audit（审计日志）
- 标准过滤 + 游标分页
- 支持查看 action/resource/ip/ua，满足追溯需求

## 7. 依赖的后端接口（仅列 P0）

- Admin 连接验证：`GET /admin/stats`
- Dashboard：`GET /admin/encryption-status`、`GET /admin/dashboard/stats`、`GET /admin/dashboard/chart`
- Clients：`GET/POST/PUT /admin/clients*`、`POST /admin/clients/{id}/rotate`
- Keys：
  - CRUD：`GET/POST/PUT/DELETE /admin/keys*`
  - 测试：`POST /admin/keys/{id}/test`
  - 批量：`POST /admin/keys/batch`
  - 危险操作：`DELETE /admin/keys/{id}/purge`（彻底删除）
- Logs：`GET /admin/logs`、`GET /admin/audit-logs`

## 8. 实施计划（只覆盖前端 P0）

1) API 层补齐：`keys.batch` + `logs.error_details` 类型
2) Clients & Keys：
   - 新增“编辑 Key”弹窗（单条）
   - 新增“批量编辑”弹窗（批量）
   - 调整工具栏按钮与文案（区分 soft delete vs purge）
3) Logs：
   - 增加详情 Drawer/Modal 展示 `error_details`
   - toast 附带 `request_id`（可选增强）
4) 交付：
   - 在 Docker 构建阶段执行 `npm ci && npm run build` 并打包 `app/ui2/`

## 9. 验收清单（P0 DoD）
- UI 能完成：连接 → 创建 Client → 添加/导入 Key → 编辑 Key 配置 → 测试 Key
- UI 能完成：选择多条 Key → 批量启用/禁用/改配额/清冷却/测试/软删除，并看到部分失败列表
- Logs 页能查看 `error_code` + `error_details`，并能复制 `request_id`
- 任意页面不会泄露明文 token/api_key（手动检查 + 走一次常见路径）

