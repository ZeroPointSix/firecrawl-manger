# FD：Client 批量管理功能落地设计

> **对应 PRD**：`docs/PRD/2026-02-25-client-batch-operations.md`
> **创建时间**：2026-02-25
> **状态**：Draft
> **优先级**：P2（功能增强/管理效率）
> **范围**：前端 WebUI（Vue 3 + Naive UI）+ 后端控制面 API（FastAPI）

---

## 1. 背景与问题陈述

PRD 记录了管理员在日常运维中需要批量处理 5-10 个 Client 的需求，当前系统仅支持单个操作，效率低下。

本 FD 的目标是将 PRD 的需求"产品化"为：
- 前端 UI 层面的批量选择与操作交互
- 后端 API 层面的批量操作端点
- 事务保证与错误处理机制
- 审计日志与可观测性

---

## 2. 目标 / 非目标

### 2.1 目标

1) **MVP 功能完整**：支持批量启用、批量禁用、批量删除
2) **交互安全可控**：危险操作需要二次确认，防止误操作
3) **部分失败可恢复**：批量操作中部分失败时，显示详细信息并支持重试
4) **审计可追溯**：所有批量操作记录到审计日志

### 2.2 非目标

- 不在 MVP 中实现批量修改配置（rate_limit、quota_limit、concurrency_limit）
- 不支持批量创建（需要复杂的配置输入）
- 不支持批量修改名称和描述（个性化字段）

---

## 3. 现状分析（基于代码）

### 3.1 数据模型（app/db/models.py）

**Client 模型**：
```python
class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    daily_quota: Mapped[int | None] = mapped_column(Integer, nullable=True)
    daily_usage: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quota_reset_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    rate_limit_per_min: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    max_concurrent: Mapped[int] = mapped_column(Integer, nullable=False, default=10)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

**关键字段**：
- `is_active`：启用/禁用状态（批量操作的目标字段）
- `id`：主键（批量操作的标识符）

### 3.2 现有 API（app/api/control_plane.py）

**单个操作 API**：
- `GET /admin/clients`：列出所有 Client
- `POST /admin/clients`：创建 Client
- `PUT /admin/clients/{id}`：更新 Client（支持修改 `is_active`）
- `DELETE /admin/clients/{id}`：删除 Client（软删除，实际是设置 `is_active=False`）
- `DELETE /admin/clients/{id}/purge`：彻底删除 Client

**现有实现特点**：
- 使用 SQLAlchemy ORM 进行数据库操作
- 所有操作都在单个事务中完成
- 删除操作会级联删除关联的 ApiKey 和 RequestLog

### 3.3 前端实现（webui/src）

**API 封装（webui/src/api/clients.ts）**：
```typescript
export type ClientItem = {
  id: number;
  name: string;
  is_active: boolean;
  daily_quota: number | null;
  daily_usage: number;
  quota_reset_at: string | null;
  rate_limit_per_min: number;
  max_concurrent: number;
  created_at: string;
  last_used_at: string | null;
};

export async function fetchClients() { ... }
export async function createClient(payload: CreateClientRequest) { ... }
export async function updateClient(clientId: number, payload: UpdateClientRequest) { ... }
```

**UI 组件**：
- 使用 Naive UI 的 `n-data-table` 组件
- 当前不支持复选框选择

### 3.4 关键风险：批量操作的原子性与一致性

**风险 1：部分操作失败**
- 批量操作中部分 Client 可能不存在、已被删除或数据库错误
- 需要明确的错误处理策略：全部回滚 vs 部分成功

**风险 2：并发操作冲突**
- 多个管理员同时对同一批 Client 进行批量操作
- 需要数据库事务和行锁保证一致性

**风险 3：审计日志缺失**
- 批量操作需要记录到审计日志，便于追溯和恢复

---

## 4. 功能设计（需要实现/修改的功能）

### 4.1 P0：后端批量操作 API

#### 4.1.1 API 设计

**端点**：`PATCH /admin/clients/batch`

**请求格式**：
```json
{
  "client_ids": [1, 2, 3],
  "action": "enable" | "disable" | "delete"
}
```

**响应格式（成功）**：
```json
{
  "success_count": 3,
  "failed_count": 0,
  "failed_items": []
}
```

**响应格式（部分失败）**：
```json
{
  "success_count": 2,
  "failed_count": 1,
  "failed_items": [
    {
      "client_id": 3,
      "error": "Client not found"
    }
  ]
}
```

**错误响应**：
- `400 Bad Request`：参数错误（client_ids 为空、action 无效）
- `401 Unauthorized`：Admin Token 无效
- `404 Not Found`：所有 Client 都不存在

#### 4.1.2 实现方案（伪代码）

```python
# app/api/control_plane.py

from pydantic import BaseModel, Field
from enum import Enum

class BatchAction(str, Enum):
    ENABLE = "enable"
    DISABLE = "disable"
    DELETE = "delete"

class BatchCliest(BaseModel):
    client_ids: list[int] = Field(..., min_length=1, max_length=100)
    action: BatchAction

class BatchClientResponse(BaseModel):
    success_count: int
    failed_count: int
    failed_items: list[dict[str, Any]]

@router.patch("/clients/batch", dependencies=[Depends(require_admin)])
def batch_update_clients(
    request: Request,
    payload: BatchClientRequest,
    db: Session = Depends(get_db)
) -> BatchClientResponse:
    """批量操作 Client"""

    success_count = 0
    failed_count = 0
    failed_items = []

    # 查询所有目标 Client
    clients = db.query(Client).filter(Client.id.in_(payload.client_ids)).all()
    client_map = {c.id: c for c in clients}

    # 检查不存在的 Client
    for client_id in payload.client_ids:
        if client_id not in client_map:
            failed_count += 1
            failed_items.append({
                "client_id": client_id,
                "error": "Client not found"
            })

    # 执行批量操作
    try:
        for client_id in payload.client_ids:
            if client_id not in client_map:
                continue

            client = client_map[client_id]

            try:
                if payload.action == BatchAction.ENABLE:
                    client.is_active = True
                elif payload.action == BatchAction.DISABLE:
                    client.is_active = False
                elif payload.action == BatchAction.DELETE:
                    # 软删除：设置 is_active = False
                    client.is_active = False
                    # 或硬删除：db.delete(client)

                success_count += 1

            except Exception as e:
                failed_count += 1
                failed_items.append({
                    "client_id": client_id,
                    "error": str(e)
                })

        # 提交事务
        db.commit()

        # 记录审计日志
        _log_batch_operation(
            request=request,
            action=payload.action,
            client_ids=payload.client_ids,
            success_count=success_count,
            failed_count=failed_count
        )

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Batch operation failed: {str(e)}")

    return BatchClientResponse(
        success_count=success_count,
        failed_count=failed_count,
        failed_ite_items
    )
```

#### 4.1.3 事务策略

**方案 A：全部成功或全部失败（推荐用于启用/禁用）**
- 使用数据库事务，任何一个操作失败则回滚所有操作
- 优点：保证原子性，数据一致性强
- 缺点：一个失败导致全部失败

**方案 B：部分成功（推荐用于删除）**
- 每个 Client 独立操作，记录成功和失败的结果
- 优点：容错性强，部分失败不影响其他操作
- 缺点：需要更复杂的错误处理

**选择**：
- 批量启用/禁用：使用方案 A（原子性更重要）
- 批量删除：使用方案 B（容错性更重要）

#### 4.1.4 审计日志

在 `RequestLog` 或新增 `AuditLog` 表中记录批量操作：
```python
def _log_batch_operation(
    request: Request,
    action: BatchAction,
    client_ids: list[int],
    success_count: int,
    failed_count: int
):
    """记录批量操作到审计日志"""
    log_entry = {
        "request_id": request.state.request_id,
        "action": f"batch_{action.value}",
        "target": "clients",
        "target_ids": client_ids,
        "success_count": success_count,
        "failed_count": failed_count,
        "timestamp": datetime.utcnow()
    }
    # 写入日志...
```

### 4.2 P0：前端批量选择与操作

#### 4.2.1 UI 改动

**1. 添加复选框列**

在 `webui/src/views/ClientsKeysView.vue` 中修改 `n-data-table` 配置：
```typescript
const columns = [
  {
    type: 'selection',  // Naive UI 内置的复选框列
    disabled: (row: ClientItem) => false  // 可选：禁用某些行
  },
  { title: 'ID', key: 'id' },
  { title: '名称', key: 'name' },
  // ... 其他列
];
```

**2. 选择状态管理**

```typescript
import { ref } from 'vue';

const selectedClientIds = ref<number[]>([]);

const handleCheck = (rowKeys: number[]) => {
  selectedClientIds.value = rowKeys;
};
```

**3. 批量操作按钮区域**

```vue
<template>
  <div class="batch-operations" v-if="selectedClientIds.length > 0">
    <n-space>
      <n-text>已选择 {{ selectedClientIds.length }} 个 Client</n-text>
      <n-button
        type="success"
        @click="handleBatchEnable"
        :disabled="!hasDisabledClients"
      >
        批量启用
      </n-button>
      <n-button
        type="warning"
        @click="handleBatchDisable"
        :disabled="!hasEnabledClients"
      >
        批量禁用
      </n-button>
      <n-button
        type="error"
        @click="handleBatchDelete"
      >
        批量删除
      </n-button>
    </n-space>
  </div>

  <n-data-table
    :columns="columns"
    :data="clients"
    :row-key="(row: ClientItem) => row.id"
    @update:checked-row-keys="handleCheck"
  />
</template>
```

#### 4.2.2 批量操作逻辑

```typescript
// webui/src/api/clients.ts

export type BatchClientRequest = {
  client_ids: number[];
  action: 'enable' | 'disable' | 'delete';
};

export type BatchClientResponse = {
  success_count: number;
  failed_count: number;
  failed_items: Array<{
    client_id: number;
    error: string;
  }>;
};

export async function batchUpdateClients(payload: BatchClientRequest) {
  const res = await http.patch<BatchClientResponse>('/admin/clients/batch', payload);
  return res.data;
}
```

```typescript
// webui/src/views/ClientsKeysView.vue

import { useDialog, useMessage } from 'naive-ui';

const dialog = useDialog();
const message = useMessage();

const handleBatchEnable = async () => {
  try {
    const result = await batchUpdateClients({
      client_ids: selectedClien
      action: 'enable'
    });

    if (result.failed_count === 0) {
      message.success(`已启用 ${result.success_count} 个 Client`);
    } else {
      message.warning(`成功 ${result.success_count} 个，失败 ${result.failed_count} 个`);
    }

    // 刷新列表
    await fetchClients();

    // 清空选择
    selectedClientIds.value = [];

  } catch (error) {
    message.error('批量启用失败');
  }
};

const handleBatchDisable = () => {
  dialog.warning({
    title: '确认批量禁用',
    content: `确认禁用 ${selectedClientIds.value.length} 个 Client？禁用后这些 Client 将无法访问 API。`,
    positiveText: '确认禁用',
    neg取消',
    onPositiveClick: async () => {
      try {
        const result = await batchUpdateClients({
          client_ids: selectedClientIds.value,
          action: 'disable'
        });

        if (result.failed_count === 0) {
          message.success(`已禁用 ${result.success_count} 个 Client`);
        } else {
          message.warning(`成功 ${result.success_count} 个，失败 ${result.failed_count} 个`);
        }

        await fetchClients();
        selectedClientIds.value = [];

      } catch (error) {
        message.error('批量禁用失败');
      }
    }
  });
};

const handleBatchDelete = () => {
  dialog.errore: '确认批量删除',
    content: `确认删除 ${selectedClientIds.value.length} 个 Client？此操作不可恢复。`,
    positiveText: '确认删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        const result = await batchUpdateClients({
          client_ids: selectedClientIds.value,
          action: 'delete'
        });

        if (result.failed_count === 0) {
          message.success(`已删除 ${result.success_count} 个 Client`);
        } else {
          message.warning(`成功 ${result.success_count} 个，失败 ${result.failed_count} 个`);
        }

        await fetchClients();
        selectedClientIds.value
      } catch (error) {
        message.error('批量删除失败');
      }
    }
  });
};
```

#### 4.2.3 部分失败处理

当批量操作部分失败时，显示详细的失败信息：
```typescript
if (result.failed_count > 0) {
  const failedDetails = result.failed_items
    .map(item => `Client ${item.client_id}: ${item.error}`)
    .join('\n');

  dialog.warning({
    title: '部分操作失败',
    content: `成功 ${result.success_count} 个，失败 ${result.failed_count} 个\n\n失败详情：\n${failedDetails}`,
    positiveText: '确定'
  });

  // 保持失败的 Client 选中状态，便于重试
  selectedClientIds.value = result.failed_items.map(item => item.client_id);
}
```

### 4.3 P1：性能优化

#### 4.3.1 限制批量操作数量

```python
class BatchClientRequest(BaseModel):
    client_ids: list[int] = Field(..., min_length=1, max_length=100)  # 最多 100 个
    action: BatchAction
```

#### 4.3.2 使用批量更新语句

```python
# 优化前：逐个更新
for client in clients:
    client.is_active = True
    db.add(client)

# 优化后：批量更新
db.query(Client).filter(Client.id.in_(client_ids)).update(
    {Client.is_active: True},
    synchronize_session=False
)
```

---

## 5. 验收标准（DoD）

### 5.1 后端验收

- [ ] 新增 `PATCH /admin/clients/batch` API
- [ ] 支持 `enable`、`disable`、`delete` 三种操作
- [ ] 返回详细的成功/失败统计
- [ ] 批量操作使用数据库事务
- [ ] 批量操作记录到审计日志
- [ ] 单元测试覆盖率 ≥ 80%
- [ ] 集成测试覆盖所有批量操作场景

### 5.2 前端验收

- [ ] Client 列表显示复选框列
- [ ] 支持单选、多选、全选
- [ ] 选中 Client 后显示"已选择 X 个 Client"
- [ ] 批量启用按钮可用，点击后成功启用
- [ ] 批量禁用按钮可用，点击后弹出确认弹窗
- [ ] 批量删除按钮可用，点击后弹出确认弹窗
- [ ] 操作完成后清空选择状态，刷新列表
- [ ] 部分失败时显示详细提示

### 5.3 集成测试场景

**测试场景 1：批量启用成功**
```python
def test_batch_enable_clients_success():
    # 创建 3 个禁用的 Client
    clients = [create_client(is_active=False) for _ in range(3)]
    client_ids = [c.id for c in clients]

    # 批量启用
    response = client.patch("/admin/clients/batch", json={
        "client_ids": client_ids,
        "action": "enable"
    })

    assert response.status_code == 200
    assert response.json()["success_count"] == 3
    assert response.json()["failed_count"] == 0

    # 验证数据库状态
    for client_id in client_ids:
        client = db.query(Client).get(client_id)
        assert client.is_active is True
```

**测试场景 2：批量删除部分失败**
```python
def test_batch_delete_clients_partial_failure():
    # 创建 2 个 Client
    clients = [create_client() for _ in range(2)]
    client_ids = [c.id for c in clients] + [9999]  # 9999 不存在

    # 批量删除
    response = client.patch("/admin/clients/batch", json={
        "client_ids": client_ids,
        "action": "delete"
    })

    assert response.status_code == 200
    assert response.json()["success_count"] == 2
    assert response.json()["failed_count"] == 1
    assert len(response.json()["failed_items"]) == 1
    assert response.json()["failed_items"][0]["client_id"] == 9999
```

**测试场景 3：并发批量操作**
```python
def test_concurrent_batch_operations():
    # 创建 5 个 Client
    clients = [create_client() for _ in range(5)]
    client_ids = [c.id for c in clients]

    # 两个管理员同时批量禁用
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(batch_disable_clients, client_ids)
            for _ in range(2)
        ]
        results = [f.result() for f in futures]

    # 验证数据一致性
    for client_id in client_ids:
        client = db.query(Client).get(client_id)
        assert client.is_active is False
```

---

## 6. 回滚策略

### 6.1 前端回滚

- 如果前端改动导致 UI 异常：
  - 回滚前端代码到上一个稳定版本
  - 重新构建：`cd webui && npm run build`

### 6.2 后端回滚
 导致功能异常：
  - 回滚后端代码到上一个稳定版本
  - 重启服务

### 6.3 数据恢复

- 如果批量操作导致数据异常：
  - 通过审计日志查找异常操作
  - 使用数据库备份恢复数据
  - 或根据审计日志手动修正受影响的 Client

---

## 7. 代码审查结论（按严重程度排序）

### 7.1 总体评价

现有实现已经具备单个 Client 的 CRUD 操作，数据模型清晰，API 设计规范。批量操作功能可以在现有基础上快速实现，主要工作集中在：
1. 新增批量操作 API 端点
2. 前端添加复选框和批量操作按钮
3. 完善错误处理和审计日志

### 7.2 具体问题列表

**P0**
1) 缺少批量操作 API：需要新增 `PATCH /admin/clients/batch` 端点
2) 前端不支持复选框选择：需要修改 `n-data-table` 配置

**P1**
3) 批量操作缺少审计日志：需要记录批量操作到审计日志
4) 批量操作缺少性能优化：需要限制批量操作数量，使用批量更新语句

### 7.3 改进建议与示例

**建议 1：使用批量更新语句提升性能**
```python
# 优化前
for client_id in client_ids:
    client = db.query(Client).get(cl   client.is_active = True
    db.add(client)

# 优化后
db.query(Client).filter(Client.id.in_(client_ids)).update(
    {Client.is_active: True},
    synchronize_session=False
)
```

**建议 2：使用 Naive UI 的内置复选框功能**
```typescript
const columns = [
  { type: 'selection' },  // 内置复选框列
  // ... 其他列
];
```

**建议 3：部分失败时保持选中状态**
```typescript
if (result.failed_count > 0) {
  // 只保留失败的 Client 选中状态
  selectedClientIds.value = result.failed_items.map(item => item.client_id);
}
```

---

## 8. 实施计划

### 8.1 第一阶段：后端 API（1-2 天）

- [ ] 新增 `BatchClientRequest` 和 `BatchClientResponse` 数据模型
- [ ] 实现 `PATCH /admin/clients/batch` API
- [ ] 添加事务处理和错误处理
- [ ] 添加审计日志记录
- [ ] 编写单元测试和集成测试

### 8.2 第二阶段：前端 UI（1-2 天）

- [ ] 修改 `n-data-table` 配置，添加复选框列
- [ ] 添加批量操作按钮区域
- [ ] 实现批量启用/禁用/删除逻辑
- [ ] 添加二次确认弹窗
- [ ] 添加部分失败处理
- [ ] 前端 API 封装

### 8.3 第三阶段：测试与文档（1 天）

- [ ] E2E 测试
- [ ] 更新 API 契约文档
- [ ] 更新 API 使用指南
- [ ] 更新变更日志

---

## 9. 参考资料

- **PRD**：`docs/PRD/2026-02-25-client-batch-operations.md`
- **API 契约**：`docs/MVP/Firecrawl-API-Manager-API-Contract.md`
- **数据模型**：`app/db/models.py`
- **控制面 API**：`app/api/control_plane.py`
- **前端组件**：`webui/src/views/ClientsKeysView.vue`
- **前PI**：`webui/src/api/clients.ts`
- **Naive UI 文档**：https://www.naiveui.com/zh-CN/os-theme/components/data-table
