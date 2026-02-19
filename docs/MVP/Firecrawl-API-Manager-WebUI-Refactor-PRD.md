# FCAM WebUI 重构 PRD（参考 `example/gpt-load`）

> 目标：在**不影响数据面 `/api/*` 与 Firecrawl SDK 兼容性**的前提下，重构控制台 UI 的信息架构与交互方式，使其更接近 `example/gpt-load/web` 的“仪表盘 + 左侧分组 + 右侧详情/表格 + 标准日志表格”体验。

## 0. 范围与非目标

### 0.1 本次范围（UI 重构第一阶段）
- **Dashboard 仪表盘**：四个核心指标卡片 + 24 小时趋势折线图（成功/失败）。
- **Clients（分组）→ Keys（密钥）**：左侧 Clients 列表（替代 gpt-load 的 Groups），右侧显示该 Client 的详情与 Key 列表，并提供创建/导入/轮换/测试/禁用/删除等操作入口（弹窗式）。
- **Logs / Audit Logs**：标准“过滤区 + 表格 + 详情抽屉/弹窗 + 分页”形态。
- **系统/安全提示**：突出 `FCAM_MASTER_KEY` 相关的加密状态与故障提示（例如 `KEY_DECRYPT_FAILED`）。

### 0.2 明确非目标（本次不做）
- 不做“全量前端重写到完全一致的 gpt-load 视觉风格”，仅对齐**布局范式与交互信息结构**。
- 不做复杂图表系统（多维分析、可配置仪表盘、实时推送）。
- 不引入多用户登录体系（仍沿用 Admin Token 连接方式）。
- 不改变数据面鉴权/转发语义（仍是透明替换上游 URL + Key 池选择）。

## 1. 背景与问题陈述

当前 `app/ui/` 为零构建的原生 HTML/JS 控制台，功能可用但存在以下“可用性/可理解性”问题：
- 概览页以原始 JSON 输出为主，缺少一眼可读的指标与趋势。
- “创建/导入/旋转”等操作入口在页面中铺开或不统一，用户认知负担大。
- 请求日志/审计日志的呈现与筛选不够标准化，难以快速定位问题。
- 缺少“加密配置异常”的强提示：当 `FCAM_MASTER_KEY` 不匹配导致 `KEY_DECRYPT_FAILED` 时，用户体验为“网络错误/内部错误”。

参考仓库 `example/gpt-load/web` 的设计要点：
- 顶部导航简洁，Dashboard 用**指标卡片 + 趋势折线图**表达整体状态。
- Keys 管理采用**左侧分组（Groups）+ 右侧详情/表格**的双栏布局。
- Logs 为典型后台表格交互：过滤、列、分页、详情查看。

## 2. 关键概念映射（gpt-load → FCAM）

| gpt-load 概念 | FCAM 对应 | 说明 |
|---|---|---|
| Group | Client（调用方） | 本项目不需要“模型分组”，但需要以 Client 维度组织密钥与查看统计。 |
| Key | ApiKey | 仍然是上游 Firecrawl API Key（加密存储）。 |
| Dashboard Stats | `/admin/*` 聚合统计 | 需要新增/扩展统计接口以支持 24h 请求、错误率、趋势图。 |
| Encryption Status | `FCAM_MASTER_KEY` 健康度 | 用于提示“配置存在但数据不可解密”等状态。 |

## 3. 产品目标（可衡量）

### 3.1 可用性目标
- 用户在 **30 秒**内完成：连接 Admin Token → 创建 Client → 导入 Key → 数据面自检成功。
- 当存在不可解密 Key 时，Dashboard 与 Keys 页能明确提示并给出建议（配置同一 `FCAM_MASTER_KEY` 或迁移）。

### 3.2 可观测目标
- Dashboard 可展示近 24 小时请求趋势（成功/失败），并能按 Client 过滤（可选）。
- Logs 页面支持按时间范围、Client、endpoint、状态码、关键字过滤。

## 4. 信息架构（IA）与导航

建议导航（对齐 gpt-load 的 4 栏结构）：
- **仪表盘**（Dashboard）
- **Clients & Keys**（合并为一个“分组→密钥”视图）
- **请求日志**（Logs）
- **审计日志**（Audit）
- （可选）**设置/帮助**（包含连接、数据面自检、文档入口）

> 说明：当前 `app/ui/index.html` 的 Tabs 可保留，但内容布局升级为双栏与表格范式。

## 5. 页面级 PRD（交互/布局）

### 5.1 Dashboard（仪表盘）

**核心组件**
- 顶部安全/配置警报条：
  - `FCAM_MASTER_KEY` 未配置（若服务要求必须配置）
  - `FCAM_MASTER_KEY` 已配置但 DB 存在不可解密 Key（需要强提示）
- 指标卡片（4 个）：
  1) 密钥数量（总数 + 无效/失败数 sub-badge）
  2) Client 数量
  3) 24 小时请求数（total）
  4) 24 小时错误率（failed / total）
- 折线图（24h 请求趋势）：
  - 两条线：成功请求、失败请求
  - X 轴：时间（按 **1 小时** bucket）
  - Y 轴：请求数
  - 支持筛选：全部 Clients / 指定 Client（可选，取决于后端聚合能力）

**交互细节**
- 首屏加载：Skeleton/Loading；失败显示可重试。
- 统计口径：**按浏览器本地时区展示**；后端返回 RFC3339 时间戳（建议 UTC），前端用 `toLocaleString()`/`Intl` 渲染为本地时间。

### 5.2 Clients & Keys（左侧 Clients，右侧 Keys）

**布局**
- 左侧：Client 列表（含搜索框、刷新按钮、创建按钮）
- 右侧：
  - Client 详情卡片（名称、启用状态、配额/限流、创建时间、最近使用）
  - Client Token 操作（创建/旋转/复制；提示“仅显示一次”）
  - Key 列表表格（属于该 Client 的 Keys）

#### 5.2.1 Client Token：创建 vs 轮换（语义与交互）

> 目标：明确“轮换 Token ≠ 创建 Client”。轮换是**同一个 Client 的凭证重置**，会立刻让旧 Token 失效；UI 必须避免复用“创建 Client”表单导致误解。

- **创建 Client**
  - 弹窗仅用于创建（name/配额/限流/并发/is_active）。
  - 创建成功后展示 `Client Token（仅显示一次）`：只读 + 一键复制。
  - 弹窗底部按钮始终是“创建 Client”，不要与轮换复用同一个弹窗标题/按钮文案。
- **轮换 Token（Rotate）**
  - 点击后先弹出“危险操作”确认（提示：旧 Token 将立即失效，依赖该 Token 的服务会中断）。
  - 建议二次确认方式（参考 gpt-load 删除分组的模式）：
    - 让用户输入 Client 名称或 `ROTATE` 才允许继续。
  - 确认后调用 `POST /admin/clients/{id}/rotate`，仅回显新 Token（只显示一次）。
  - 轮换成功弹窗只显示：Client 名称/ID + 新 Token + “复制”按钮 + 风险提示；**不包含任何创建表单字段**。

#### 5.2.2 Client 的“禁用 / 删除 / 彻底删除（Purge）”语义

> 目标：把“可逆操作”与“不可逆操作”清晰分层，避免把后端的 soft-delete（仅禁用）在 UI 中误称为“删除”。

- **禁用/启用（可逆）**
  - UI 语义：切换 `is_active`，用于暂时停用该 Client（不破坏数据）。
  - 推荐用 `PUT /admin/clients/{id}` 更新 `is_active`（而不是用 `DELETE /admin/clients/{id}` 触发“删除”的误解）。
- **彻底删除（不可逆，Purge）**
  - UI 语义：永久删除 Client 记录（并触发解绑/脱钩逻辑）。
  - 调用 `DELETE /admin/clients/{id}/purge`。
  - 确认弹窗必须强提示副作用（当前实现会将该 Client 的 Keys 与 RequestLogs 的 `client_id` 置空、删除 idempotency 记录），并要求二次确认输入（Client 名称）。
- **“删除”按钮文案建议**
  - 把 `DELETE /admin/clients/{id}` 对应入口改名为“禁用”（或隐藏该接口，由“禁用/启用”开关统一承载）。
  - “删除”在 UI 中仅用于 `purge`，并标注“不可恢复”。

#### 5.2.3 Keys 列表：操作区自适应 + 搜索 + 列选择（参考 gpt-load）

> 目标：解决小屏“操作列 fixed + 宽度过大”导致的重叠/挤压；同时补齐按 name 搜索与“列显示/隐藏”能力。

- **操作区（Actions）最小化**
  - 用 `⋯` 下拉菜单（`NDropdown`）承载行内操作：测试、启用/禁用、轮换、删除。
  - Actions 列宽度控制在 64~100px；尽量不要 `fixed: right`，避免小屏重叠（或在 `scroll-x` 足够时才 fixed）。
  - 批量操作（删除所选）保持在表格上方工具栏，而不是塞进每行 Actions。
- **按 name 搜索（快速定位）**
  - 工具栏增加 `name` 搜索框（只搜 name，满足“不要搜其他的，就搜名字”）。
  - MVP 可先做前端过滤；如 Key 数量预期较大，建议扩展 `GET /admin/keys` 支持 `q`/`name_contains` + 分页参数（见“开放问题”）。
- **列选择器（显示/隐藏列）**
  - 参考 `example/gpt-load/web/src/components/logs/LogTable.vue` 的实现：
    - `required`（必选列不可取消）/`alwaysVisible`（Actions 列强制显示）
    - `localStorage` 持久化可见列集合
  - Keys 表格列建议分级：
    - 必选：Key、Status、Actions
    - 常用：Name、启用、Plan、Quota、Last Used
    - 高级：Cooldown、RPM、并发、创建时间、账号信息等

**Key 列表需求**
- 表格列建议：
  - 选择框（多选）
  - Key（masked）
  - Name
  - Status（active/cooling/quota_exceeded/failed/disabled）
  - is_active（启用/禁用）
  - daily_usage / daily_quota
  - cooldown_until / last_used_at
  - 操作：测试、禁用/启用、轮换（输入新 key）、删除（硬删除/或二次确认）
- 工具栏按钮：
  - 创建 Key（弹窗）
  - 文本导入（弹窗；支持账号/密码/默认 key/验证时间）
  - 批量编辑（对选中 keys 执行：批量启用/禁用、批量改配额/限流/并发、批量清冷却、批量测试、批量软删除；允许部分成功并展示失败列表）
  - 删除所选（危险操作，二次确认）

**已确认决策：Key 按 Client 隔离池（强隔离）**
- 数据面选 Key 只在“当前 Client 绑定的 Keys”中轮询。
- 未绑定 Client 的 Key 仅作为“待分配资源”，不会被数据面使用（避免跨 Client 泄露/误用）。
- 迁移策略：为历史 Key 提供“绑定/迁移到某个 Client”的管理入口（UI 或 API）。

### 5.3 Logs（请求日志）
- 顶部过滤区（建议字段）：
  - 时间范围（start/end）
  - Client（下拉/搜索）
  - endpoint（scrape/crawl/search/agent）
  - success（true/false）
  - status_code
  - error_message contains
  - request_id / idempotency_key
- 表格：
  - 时间、endpoint、状态、耗时、Client、使用的 key(masked)、错误摘要
  - 行点击打开详情（弹窗/抽屉）：request/response body（如已记录）、headers（脱敏）
- 分页：page/page_size；保持与后端一致。

### 5.4 Audit（审计日志）
- 同 Logs 的表格范式
- 过滤：action、resource_type、resource_id、时间范围
- 详情：actor/ip/user_agent

## 6. 后端接口需求（为 UI 提供稳定数据）

> 原则：优先复用现有 `/admin/*`；必要时新增“聚合统计接口”，避免 UI 通过拉全量 logs 自己算。

### 6.1 已有接口（现状）
- `GET /admin/stats`：keys/clients 的数量分布（已存在）
- `GET /admin/logs`：请求日志（分页）
- `GET /admin/audit-logs`：审计日志（分页）
- 其它：keys/clients CRUD、key import-text、purge 等

### 6.2 建议新增/扩展接口（UI 重构需要）
1) **Dashboard 聚合统计（含 24h）**
   - `GET /admin/dashboard/stats`
   - 返回：
     - `key_count.total`
     - `key_count.failed`（不可解密/failed 状态）
     - `client_count.total`
     - `request_24h.total`
     - `request_24h.failed`
     - `error_rate_24h`（百分比）
     - `security_warnings[]`（可选）
2) **Dashboard 折线图数据**
   - `GET /admin/dashboard/chart?range=24h&bucket=hour&client_id=...&tz=...`
   - 返回：
     - `labels[]`（ISO8601）
     - `datasets[]`：success / failed（对齐 gpt-load 的 `ChartData`）
3) **加密/主密钥状态**
   - `GET /admin/encryption-status`
   - 返回：
     - `master_key_configured: boolean`
     - `has_decrypt_failures: boolean`
     - `suggestion: string`（人类可读）

> 备注：如果你希望“按 Client 过滤图表/统计”，需要在 RequestLog 中确保能关联 client_id（当前已有），并提供聚合查询。

## 7. 技术方案（实现路径）

### 7.1 方案 1：继续零构建（现有 `app/ui`）
- 优点：部署最简单；无 Node 构建链；改动集中在 `app/ui/*`。
- 做法：用 CSS 变量 + 组件化 DOM（模板函数）实现卡片/表格/弹窗；折线图采用 **SVG**（可直接借鉴 gpt-load 的纯 SVG 思路，但用原生 JS 实现）。
- 适用：你希望保持“单文件可部署”，并且 UI 只服务个人使用。

### 7.2 方案 2：引入 Vue3 + Vite + NaiveUI（对齐 gpt-load 技术栈）
- 优点：组件化、可维护性强；快速做出标准后台体验；复用 `example/gpt-load/web` 的模式更直接。
- 成本：需要 Node 构建；产物需打包到 `app/ui/dist` 或类似目录并由后端静态托管；需要调整 CI/发布流程。

### 7.3 推荐
- **本次选择方案 2（Vue3 + Vite + NaiveUI）**：直接对齐 `example/gpt-load/web` 的布局范式与组件交互，减少自研 UI 组件成本。
- 后端静态托管：构建产物输出到独立目录（例如 `app/ui2/`），与现有 `app/ui/` 并行一段时间，验证稳定后再切换默认入口。

## 8. 验收标准（Definition of Done）
- Dashboard：四卡 + 24h 折线图正常显示；不可解密/配置异常有明确提示与建议；刷新不闪屏。
- Clients & Keys：左侧选择 Client 后右侧刷新；Key 的创建/导入/轮换/测试/禁用/删除入口清晰；多选删除可用且有二次确认。
- Logs/Audit：过滤可用；表格分页正常；详情查看可读；关键字段可复制（masked）。
- 不破坏现有控制面/数据面接口兼容；无新增高危默认行为。

## 9. 里程碑与计划清单（Checklist）

### Phase 0：需求确认（已完成）
- [x] Key 按 Client 隔离池（强隔离）
- [x] 图表 bucket：按小时（1h）
- [x] 展示时区：浏览器本地时区

### Phase 1：后端能力补齐（1~2 天）
- [ ] ApiKey 增加 `client_id` 字段与迁移（用于隔离池）
- [ ] 数据面 Key 选择逻辑：按 client_id 过滤
- [ ] 增加 `/admin/dashboard/stats`
- [ ] 增加 `/admin/dashboard/chart`
- [ ] 增加 `/admin/encryption-status`
- [ ] 更新 `docs/MVP/Firecrawl-API-Manager-API-Contract.md`

### Phase 2：UI 骨架重构（1 天）
- [ ] 提炼 UI state/store（避免散落全局状态）
- [ ] 抽象通用组件：Card、Table、Modal、Toast、Filters
- [ ] 完成导航与路由（Tabs/Views）结构整理

### Phase 3：Dashboard UI（1 天）
- [ ] 指标卡片布局（参考 `example/gpt-load/web/src/components/BaseInfoCard.vue`）
- [ ] SVG 折线图（参考 `example/gpt-load/web/src/components/LineChart.vue`）
- [ ] 安全/加密提示条（参考 `EncryptionMismatchAlert` 思路）

### Phase 4：Clients & Keys UI（1~2 天）
- [ ] 左侧 Client 列表（搜索/创建/刷新）
- [ ] 右侧 Client 详情 + Token 操作
- [ ] Key 表格（多选/批量删除/弹窗创建与导入/轮换与测试）

### Phase 5：Logs/Audit UI（1~2 天）
- [ ] 标准过滤区 + 表格 + 分页
- [ ] 详情弹窗/抽屉 + JSON 友好展示

## 10. 开放问题（需要你确认）
- 是否需要暗色模式/主题切换（gpt-load 有）？
- Dashboard 是否需要“按 Client 过滤”作为一等能力（下拉选择），还是先仅展示全局？
- Clients：UI 上“删除”是否只指 **purge（不可逆）**？“禁用/启用”是否统一用开关承载？
- Client Token：轮换 Token 是否需要“输入 Client 名称/ROTATE”的二次确认（默认建议需要）？
- Keys：列表形态优先“表格（列选择器）”还是“卡片网格（更像 gpt-load）”？（两者并存会增加复杂度）
- Keys：`GET /admin/keys` 是否需要做服务端分页与 `name_contains` 搜索（避免 Key 多时前端全量拉取）？

## 11. 参考资料（仓库内）
- `example/gpt-load/screenshot/dashboard.png`（布局参考）
- `example/gpt-load/web/src/views/Dashboard.vue`（页面结构）
- `example/gpt-load/web/src/components/BaseInfoCard.vue`（指标卡片）
- `example/gpt-load/web/src/components/LineChart.vue`（SVG 折线图实现）
- `example/gpt-load/web/src/components/keys/GroupList.vue`（左侧分组列表：搜索/滚动定位/创建入口）
- `example/gpt-load/web/src/components/keys/GroupInfoCard.vue`（右侧详情卡：操作区 icon + 危险操作二次确认）
- `example/gpt-load/web/src/components/keys/KeyTable.vue`（Keys 列表：工具栏搜索+筛选+分页的交互密度）
- `example/gpt-load/web/src/components/logs/LogTable.vue`（列选择器 + localStorage 持久化 + Actions 列 alwaysVisible）
- 当前实现：`app/ui/index.html`、`app/ui/app.js`、`app/ui/app.css`
