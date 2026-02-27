# TODO 索引

本目录用于存放“可执行的待办清单（Task List）”，按 **一个 PRD/主题 = 一个 TODO 文件** 组织，避免把所有任务堆在一个大而全的清单里导致漂移与难以维护。

命名约定：
- `YYYY-MM-DD-<topic>.md`
- 每个 TODO 文件顶部必须引用对应的 PRD/FD/TDD（单一事实来源：PRD/FD/TDD；TODO 只记录执行与验收）

当前 TODO：
- `docs/TODO/2026-02-20-clawcloud-postgres-migration.md`：ClawCloud 稳定部署（SQLite CrashLoop）+ SQLite→Postgres 后端直迁（P0）
- `docs/TODO/2026-02-23-deprecation-warnings-cleanup.md`：清理测试运行中的弃用告警（FastAPI on_event / python-multipart / httpx）（P1）
