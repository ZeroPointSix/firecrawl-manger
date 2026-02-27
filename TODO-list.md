# TODO List

> 说明：此文件用于记录“跨主题/跨模块”的高优先级待办与验收口径；更大的主题任务建议拆到 `docs/TODO/YYYY-MM-DD-<topic>.md`。

## P0

- [x] 对齐 Firecrawl **v2** 全量接口（暂不处理 v1）：以 `api-reference/firecrawl-docs/api-reference/v2-openapi.json` 为准（含 `team/*`、`browser/*`、所有 `DELETE` 操作）。
  - 验收：新增自动化测试覆盖 **v2-openapi.json 的所有 operations**，确保 `/v2/*` 路由可达、路径白名单放行、方法支持齐全，并正确转发到上游同名路径。
  - 自动化测试：`tests/integration/test_firecrawl_v2_openapi_alignment.py`

- [x] 修复有状态/异步资源链路的 **Key 一致性（sticky key）**：对 `crawl/batch/agent/browser/extract` 等 create→status/delete/execute 链路，将上游返回的 `id` 与选中的 key 绑定，后续请求固定使用同一 key，避免 RR 抖动导致 404/权限错误。
  - 验收：在上游“资源按 key 隔离”的模拟场景下，create 后的 status/delete/execute 均不再偶发失败。
  - 自动化测试：`tests/integration/test_sticky_resource_bindings.py`

- [x] `agent` 版本对齐：`/api/agent` 与兼容层 `/v1/agent` **优先**转发上游 `/v2/agent`，遇到上游 `404/405` 再 fallback 到 `/v1/agent`（以适配上游 agent 实际版本归属不确定性）。
  - 验收：mock 上游断言优先命中 `/v2/agent`，并覆盖 fallback 分支。
  - 自动化测试：`tests/integration/test_api_data_plane.py`

- [x] 代理路径（`/api/*`、`/v1/*`、`/v2/*`）的网关自发错误体与 Firecrawl 风格对齐：`{"success": false, "error": "..."}`；控制面 `/admin/*` 仍保持 FCAM 错误体，避免破坏管理端语义。
  - 验收：透传上游错误体不变；仅当网关自身拒绝/异常时返回 Firecrawl 风格错误体。
  - 自动化测试：`tests/integration/test_middleware.py`、`tests/integration/test_api_data_plane.py`、`tests/integration/test_smoke.py`
