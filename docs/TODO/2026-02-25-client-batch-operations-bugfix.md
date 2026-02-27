# Client 批量管理功能 Bug 修复文档（第二版 - 最终版）

**创建日期**：2026-02-25
**更新日期**：2026-02-25（第二次反馈后更新）
**优先级**：P0（严重）
**状态**：待修复

---

## 问题概述

Client 批量管理功能上线后，用户反馈了严重的功能缺陷。第一次修复后，用户再次反馈发现了更严重的问题。

---

## 用户反馈的核心问题

### 🔴 P0 - 严重问题

#### Bug #1: 删除逻辑变成了禁用逻辑（核心缺陷）

**问题描述**：
- 用户执行"批量删除"操作后，Client 只是被禁用，并没有真正删除
- "批量禁用"和"批量删除"的效果完全一样
- 用户无法区分哪些是禁用的，哪些是删除的

**根本原因分析**：
查看后端代码 `app/api/control_plane.py`：
```python
if payload.action == BatchAction.ENABLE:
    client.is_active = True
elif payload.action == BatchAction.DISABLE:
    client.is_active = False
elif payload.action == BatchAction.DELETE:
    # 软删除：设置 is_active = False
    client.is_active = False  # ❌ 和 DISABLE 完全一样！
```

**问题根源**：
- `DISABLE` 操作：设置 `is_active = False`
- `DELETE` 操作：也是设置 `is_active = False`
- **结果**：两个操作完全相同，无法区分！

**用户期望**：
- **禁用（Disable）**：临时停用，可以重新启用，Client 仍然在列表中可见（显示"禁用"状态）
- **删除（Delete）**：永久删除，从列表中消失，不再显示

**影响范围**：
- 批量删除功能完全失效
- 单个删除功能也有同样的问题
- 用户无法真正删除 Client
- 数据库中会积累大量"假删除"的记录

---

#### Bug #2: 全选 UI 设计不合理

**问题描述**：
- 第一次修复时，我添加了一个独立的"全选所有 Client"按钮
- 占用了额外的空间，不符合常见的 UI 模式
- 批量操作区域还有重复的"全选"/"取消全选"按钮

**用户期望**：
- 在"创建 Client"按钮**旁边**（右边）添加一个小复选框
- 点击复选框即可全选/取消全选
- 支持半选状态（部分选中时显示）
- 不需要批量操作区域的"全选"/"取消全选"按钮

**当前实现（错误）**：
```vue
<!-- 独立的全选按钮 -->
<div v-else style="padding: 4px 0">
  <n-button size="small" @click="selectAllClients" style="width: 100%">
    全选所有 Client
  </n-button>
</div>

<!-- 批量操作区域还有全选按钮 -->
<n-space size="small">
  <n-button size="tiny" @click="selectAllClients">全选</n-button>
  <n-button size="tiny" @click="deselectAllClients">取消全选</n-button>
</n-space>
```

**正确实现**：
```vue
<!-- 在"创建 Client"按钮旁边添加复选框 -->
<n-space align="center">
  <n-button type="primary" size="small" @click="showCreateClient = true">创建 Client</n-button>
  <n-checkbox
    :checked="allClientsSelected"
    :indeterminate="someClientsSelected"
    @update:checked="handleSelectAll"
  />
</n-space>
```

---

## 正确的解决方案（参考 Key 的设计）

### Key 的状态管理设计

查看 `app/db/models.py` 中的 ApiKey 模型：
```python
class ApiKey(Base):
    __tablename__ = "api_keys"

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
```

查看 Key 的删除逻辑 `app/api/control_plane.py`：
```python
@router.delete("/keys/{key_id}", status_code=204)
def delete_key(request: Request, key_id: int, db: Session = Depends(get_db)):
    key.is_active = False
    key.status = "disabled"  # 软删除，设置状态为 disabled
```

**Key 的状态值**：
- `"active"` - 正常使用
- `"disabled"` - 已禁用/已删除
- `"cooling"` - 冷却中
- `"quota_exceeded"` - 配额耗尽
- `"failed"` - 失败

### Client 应该采用相同的设计

**添加 status 字段**：
```python
class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")  # 新增

    # ... 其他字段保持不变
```

**Client 的状态值**：
- `"active"` - 正常使用
- `"disabled"` - 已禁用（临时停用，可以重新启用）
- `"deleted"` - 已删除（永久删除，不再显示）

**修改后的逻辑**：
```python
if payload.action == BatchAction.ENABLE:
    client.is_active = True
    client.status = "active"
elif payload.action == BatchAction.DISABLE:
    client.is_active = False
    client.status = "disabled"  # 禁用
elif payload.action == BatchAction.DELETE:
    client.is_active = False
    client.status = "deleted"  # 删除
```

**前端过滤逻辑**：
```typescript
// 不显示已删除的 Client
clients.value = (await fetchClients()).filter((c) => c.status !== 'deleted');
```

**优点**：
- ✅ 与 Key 的设计保持一致
- ✅ 使用字符串状态，更灵活，可以扩展更多状态
- ✅ 不需要额外的 `is_deleted` 字段
- ✅ 语义清晰：`status = "disabled"` 表示禁用，`status = "deleted"` 表示删除
- ✅ 符合项目现有的设计模式

---

## 详细实施步骤

### 步骤1：修复全选 UI（P0，简单，前端修改）

#### 1.1 移除独立的全选按钮

**修改文件**：`webui/src/views/ClientsKeysView.vue`

**移除代码**：
```vue
<!-- 删除这段 -->
<div v-else style="padding: 4px 0">
  <n-button size="small" @click="selectAllClients" style="width: 100%">
    全选所有 Client
  </n-button>
</div>
```

#### 1.2 在"创建 Client"按钮旁边添加复选框

**修改代码**：
```vue
<!-- 修改前 -->
<n-button type="primary" size="small" @click="showCreateClient = true">创建 Client</n-button>

<!-- 修改后 -->
<n-space align="center">
  <n-button type="primary" size="small" @click="showCreateClient = true">创建 Client</n-button>
  <n-checkbox
    :checked="allClientsSelected"
    :indeterminate="someClientsSelected"
    @update:checked="handleSelectAll"
  />
</n-space>
```

#### 1.3 移除批量操作区域的全选按钮

**修改代码**：
```vue
<!-- 修改前 -->
<n-space align="center" justify="space-between">
  <div style="font-size: 12px; color: #666">已选择 {{ checkedClientIds.length }} 个 Client</div>
  <n-space size="small">
    <n-button size="tiny" @click="selectAllClients">全选</n-button>
    <n-button size="tiny" @click="deselectAllClients">取消全选</n-button>
  </n-space>
</n-space>

<!-- 修改后 -->
<div style="font-size: 12px; color: #666">已选择 {{ checkedClientIds.length }} 个 Client</div>
```

#### 1.4 添加计算属性和处理函数

**添加代码**：
```typescript
const allClientsSelected = computed(() => {
  return checkedClientIds.value.length === filteredClients.value.length
    && filteredClients.value.length > 0;
});

const someClientsSelected = computed(() => {
  return checkedClientIds.value.length > 0
    && checkedClientIds.value.length < filteredClients.value.length;
});

function handleSelectAll(checked: boolean) {
  if (checked) {
    selectAllClients();
  } else {
    deselectAllClients();
  }
}
```

**测试验证**：
- ✅ 复选框在"创建 Client"按钮右边
- ✅ 未选中任何 Client 时，复选框为空
- ✅ 选中部分 Client 时，复选框显示半选状态（横线）
- ✅ 选中所有 Client 时，复选框显示全选状态（勾选）
- ✅ 点击复选框可以全选/取消全选
- ✅ 批量操作区域没有重复的全选按钮

---

### 步骤2：添加 status 字段（P0，复杂，需要数据库迁移）

#### 2.1 修改数据库模型

**修改文件**：`app/db/models.py`

**修改内容**：
```python
class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")  # 新增

    daily_quota: Mapped[int | None] = mapped_column(Integer, nullable=True)
    daily_usage: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quota_reset_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    rate_limit_per_min: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    max_concurrent: Mapped[int] = mapped_column(Integer, nullable=False, default=10)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    api_keys: Mapped[list[ApiKey]] = relationship(back_populates="client")
    request_logs: Mapped[list["RequestLog"]] = relationship(back_populates="client")
    idempotency_records: Mapped[list["IdempotencyRecord"]] = relationship(back_populates="client")
```

#### 2.2 创建数据库迁移

**命令**：
```bash
alembic revision --autogenerate -m "add status field to clients table"
```

**生成的迁移文件**（示例）：
```python
"""add status field to clients table

Revision ID: 0007_add_client_status
Revises: 0006_add_upstream_resource_bindings
Create Date: 2026-02-25 XX:XX:XX.XXXXXX

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0007_add_client_status'
down_revision = '0006_add_upstream_resource_bindings'
branch_labels = None
depends_on = None

def upgrade():
    # 添加 status 字段，默认值为 'active'
    op.add_column('clients', sa.Column('status', sa.String(32), nullable=False, server_default='active'))

def downgrade():
    # 回滚：删除 status 字段
    op.drop_column('clients', 'status')
```

**运行迁移**：
```bash
alembic upgrade head
```

#### 2.3 修改后端批量操作逻辑

**修改文件**：`app/api/control_plane.py`

**修改内容**：
```python
# 在批量操作函数中修改
for client_id in unique_client_ids:
    if client_id not in client_map:
        continue

    client = client_map[client_id]

    try:
        if payload.action == BatchAction.ENABLE:
            client.is_active = True
            client.status = "active"  # 新增
        elif payload.action == BatchAction.DISABLE:
            client.is_active = False
            client.status = "disabled"  # 修改
        elif payload.action == BatchAction.DELETE:
            client.is_active = False
            client.status = "deleted"  # 修改：使用 deleted 状态

        success_count += 1

        # 记录审计日志
        _audit(
            db,
            request=request,
            action=f"client.batch.{payload.action.value}",
            resource_type="client",
            resource_id=str(client.id),
        )

    except Exception as e:
        failed_count += 1
        failed_items.append({
            "client_id": client_id,
            "error": str(e)
        })
```

#### 2.4 修改后端单个删除逻辑（保持一致性）

**修改文件**：`app/api/control_plane.py`

**查找并修改**：单个 Client 的禁用操作（如果有单独的删除端点）

**注意**：根据现有代码，可能只有"禁用 Client"操作，没有单独的"删除 Client"端点。如果有，需要保持一致。

#### 2.5 修改后端列表 API（推荐）

**修改文件**：`app/api/control_plane.py`

**查找**：`GET /admin/clients` 端点

**修改内容**：
```python
@router.get("/clients")
def list_clients(
    include_deleted: bool = Query(False, description="是否包含已删除的 Client"),
    db: Session = Depends(get_db)
) -> dict[str, Any]:
    query = db.query(Client)
    if not include_deleted:
        query = query.filter(Client.status != "deleted")  # 过滤已删除的
    clients = query.all()
    return {"items": clients}
```

**优点**：
- 后端默认过滤已删除的记录
- 前端不需要额外的过滤逻辑
- 可选参数 `include_deleted` 允许查看已删除的记录（用于管理或恢复）

---

### 步骤3：修改前端（类型定义和显示逻辑）

#### 3.1 修改前端类型定义

**修改文件**：`webui/src/api/clients.ts`

**修改内容**：
```typescript
export type ClientItem = {
  id: number;
  name: string;
  is_active: boolean;
  status: string;  // 新增：'active' | 'disabled' | 'deleted'
  daily_quota: number | null;
  daily_usage: number;
  quota_reset_at: string | null;
  rate_limit_per_min: number;
  max_concurrent: number;
  created_at: string;
  last_used_at: string | null;
};
```

#### 3.2 修改前端加载逻辑（如果后端不过滤）

**修改文件**：`webui/src/views/ClientsKeysView.vue`

**修改内容**：
```typescript
async function loadClients() {
  if (!adminToken.value) return;
  loadingClients.value = true;
  try {
    // 如果后端已经过滤，直接使用
    clients.value = await fetchClients();

    // 如果后端不过滤，前端过滤
    // clients.value = (await fetchClients()).filter((c) => c.status !== 'deleted');

    if (!clients.value.length) {
      selectedClientId.value = null;
      return;
    }

    const currentId = selectedClientId.value;
    const exists = currentId !== null && clients.value.some((c) => c.id === currentId);
    if (!exists) selectedClientId.value = clients.value[0].id;
  } catch (err: unknown) {
    message.error(getFcamErrorMessage(err), { duration: 5000 });
  } final    loadingClients.value = false;
  }
}
```

---

### 步骤4：测试验证

#### 4.1 全选功能测试

**测试场景 1：全选复选框位置和外观**
- ✅ 复选框在"创建 Client"按钮右边
- ✅ 复选框大小适中，易于点击
- ✅ 批量操作区域没有重复的全选按钮

**测试场景 2：全选状态**
- ✅ 未选中任何 Client：复选框为空
- ✅ 选中部分 Client：复选框显示半选状态（横线）
- ✅ 选中所有 Client：复选框显示全选状态（勾选）

**测试场景 3：全选操作**
- ✅ 点击空复选框：全选所有 Client
- ✅ 点击全选复选框：取消全选
- ✅ 点击半选复选框：全选所有 Client

#### 4.2 禁用功能测试

**测试场景 4：批量禁用**
1. 创建 3 个启用状态的 Client
2. 勾选这 3 个 Client
3. 点击"批量禁用"
4. ✅ 验证：Client 仍在列表中，状态标签显示"禁用"（灰色）
5. ✅ 验证数据库：`is_active = false`, `status = "disabled"`

**测试场景 5：批量启用**
1. 勾选多个禁用lient
2. 点击"批量启用"
3. ✅ 验证：Client 状态变为"启用"（绿色）
4. ✅ 验证数据库：`is_active = true`, `status = "active"`

#### 4.3 删除功能测试

**测试场景 6：批量删除**
1. 创建 3 个 Client
2. 勾选这 3 个 Client
3. 点击"批量删除"
4. 确认操作
5. ✅ 验证：Client 从列表中消失（被过滤）
6. ✅ 验证数据库：`is_active = false`, `status = "deleted"`

**测试场景 7：禁用和删除的区别**
1. 创建 2 个 Client
2. 禁用第一个，删除第二个
3. ✅ 验证：第一个仍在列表中（显示"禁用"），第二个消失
4. ✅ 验证数据库：
   - Client 1: `status = "disabled"`
   - Client 2: `status = "deleted"`

**测试场景 8：已删除的 Client 不能启用**
1. 删除一个 Client
2. 尝试通过 API 启用该 Client（`include_deleted=true` 查询后尝试启用）
3. ✅ 验证：操作失败或被忽略（已删除的不应该被启用）

#### 4.4 审计日志测试

**测试场景 9：删除操作记录**
1. 执行批量删除操作
2. 查看审计日志
3. ✅ 验证：每个被删除的 Client 都有审计日志
4. ✅ 验证：action 为 `client.batch.delete`
5. ✅ 验证：包含 Client ID 和名称

#### 4.5 回归测试

**测试场景 10：创建禁用状态的 Client**
1. 创建 Client，`is_active = false`
2. ✅ 验证：Client 在列表中可见，状态显示"禁用"
3. ✅ 验证数据库：`status = "active"` 或 `"disabled"`（取决于创建时的设置）

**测试场景 11：搜索功能**
1. 搜索 Client 名称
2. ✅ 验证：搜索结果不包含已删除的 Client
3. ✅ 验证：搜索结果包含禁用的 Client

**测试场景 12：请求日志关联**
1. 查看已删除 Client 的历史请求日志
2. ✅ 验证：日志仍然存在
3. ✅ 验证：Client 信息正确显示

---

## 数据迁移注意事项

### 现有数据处理

**问题**：现有数据库中的 Client 没有 `status` 字段

**解决方案**：
1. 迁移脚本中设置默认值为 `'active'`
2. 所有现有 Client 的 `status` 都为 `'active'`
3. 不需要手动处理现有数据

**迁移 SQL**：
```sql
-- 添加字段，默认值为 'active'
ALTER TABLE clients ADD COLUMN status VARCHAR(32) NOT NULL DEFAULT 'active';
```

### 回滚方案

如果迁移出现问题，可以回滚：

```bash
# 回滚到上一个版本
alembic downgrade -1
```

回滚后：
- `status` 字段被删除
- 数据恢复到迁移前的状态
- 需要重新部署旧版本的代码

---

## 风险评估

### 高风险项

1. **数据库迁移失败**
   - 风险：迁移过程中出错，导致数据库不一致
   - 缓解：在测试环境先验证迁移脚本
   - 缓解：备份生产数据库

### 中风险项

1. **API 兼容性**
   - 风险：前端旧版本可能不识别 `status` 字段
   - 缓解：前后端同时部署
   - 缓解：`status` 字段有默认值，不会导致错误

2. **现有 Client 的行为变化**
   - 风险：之前"软删除"的 Client（`is_active=false`）现在会显示出来
   - 缓解：这实际上是修复，不是风险
   - 缓解：在发布说明中告知用户

### 低风险项

1. **性能影响**
   - 风险：新增字段和过滤条件可能影响查询性能
   - 缓解：`status` 是字符串字段，性能影响极小
   - 缓解：可以添加索引（如果需要）

---

## 实施时间估算

- **步骤1（全选 UI）**：30 分钟
  - 修改前端代码：20 分钟
  - 测试验证：10 分钟

- **步骤2（status 字段）**：60 分钟
  - 修改数据库模型：10 分钟
  - 创建和验证迁移：15 分钟
  - 修改后端 API：20 分钟
  - 修改前端类型：10 分钟
  - 测试验证：5 分钟

- **步骤3（全面测试）**：30 分钟

- **步骤4（文档更新）**：15 分钟

**总计**：约 2 小时

---

## 相关文件清单

### 需要修改的文件

**后端**：
- `app/db/models.py` - 添加 `status` 字段
- `migrations/versions/0007_add_client_status.py` - 新建迁移文件
- `app/api/control_plane.py` - 修改批量操作逻辑和列表 API

**前端**：
- `webui/src/api/clients.ts` - 更新类型定义
- `webui/src/views/ClientsKeysView.vue` - 修改全选 UI 和加载逻辑

**文档**：
- `docs/WORKLOG.md` - 记录本次修复
- `docs/MVP/Firecrawl-API-Manager-API-Contract.md` - 更新 API 文档（可选）

**测试**：
- `tests/integration/test_batch_clients.py` - 可能需要更新测试用例

---

## 总结

本次修复解决了两个严重问题：

1. ✅ **全选 UI 优化**：在"创建 Client"按钮旁边添加复选框，移除重复的全选按钮
2. ✅ **删除和禁用区分**：通过 `status` 字段明确区分两种操作，与 Key 的设计保持一致

修复后的效果：
- 用户可以清楚地区分禁用和删除
- 已删除的 Client 不会再出现在列表中
- 全选操作更加便捷和直观
- 与项目现有的 Key 设计保持一致

**下一步**：按照实施步骤逐步修复，每一步都要测试验证。


---

## 第二次反馈的问题（更严重）

### 🔴 P0 - 严重问题

#### Bug #1: 删除逻辑变成了禁用逻辑（核心缺陷）

**问题描述**：
- 用户执行"批量删除"操作后，Client 只是被禁用，并没有真正删除
- "批量禁用"和"批量删除"的效果完全一样
- 用户无法区分哪些是禁用的，哪些是删除的

**根本原因分析**：
查看后端代码 `app/api/control_plane.py`：
```python
if payload.action == BatchAction.ENABLE:
    client.is_active = True
elif payload.action == BatchAction.DISABLE:
    client.is_active = False
elif payload.action == BatchAction.DELETE:
    # 软删除：设置 is_active = False
    client.is_active = False  # ❌ 和 DISABLE 完全一样！
```

**问题根源**：
- `DISABLE` 操作：设置 `is_active = False`
- `DELETE` 操作：也是设置 `is_active = False`
- **结果**：两个操作完全相同，无法区分！

**用户期望**：
- **禁用（Disable）**：临时停用，可以重新启用，Client 仍然在列表中可见
- **删除（Delete）**：永久删除或标记为已删除，不应该再出现在列表中

**影响范围**：
- 批量删除功能完全失效
- 单个删除功能也可能有同样的问题
- 用户无法真正删除 Client
- 数据库中会积累大量"假删除"的记录

**解决方案**：添加 `is_deleted` 字段

需要在 Client 模型中添加 `is_deleted` 布尔字段来区分"禁用"和"删除"：

```python
class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # 新增
    # ... 其他字段
```

修改后的逻辑：
```python
if payload.action == BatchAction.ENABLE:
    client.is_active = True
elif payload.action == BatchAction.DISABLE:
    client.is_active = False  # 仅禁用
elif payload.action == BatchAction.DELETE:
    client.is_deleted = True  # 标记为已删除
    client.is_active = False  # 同时禁用
```

前端过滤逻辑：
```typescript
// 不显示已删除的 Client
clients.value = (await fetchClients()).filter((c) => !c.is_deleted);
```

---

#### Bug #2: 全选 UI 设计不合理

**问题描述**：
- 第一次修复时，我添加了一个独立的"全选所有 Client"按钮
- 占用了额外的空间，不符合常见的 UI 模式
- 用户期望的是标准的复选框全选模式

**用户期望**：
- 在"创建 Client"按钮旁边添加一个小复选框
- 点击复选框即可全选/取消全选
- 支持半选状态（部分选中时显示）
- 这是标准的表格全选模式

**当前实现（错误）**：
```vue
<!-- 未选择时显示全选按钮 -->
<div v-else style="padding: 4px 0">
  <n-button size="small" @click="selectAllClients" style="width: 100%">
    全选所有 Client
  </n-button>
</div>
```

**正确实现**：
```vue
<n-space align="center">
  <n-checkbox
    :checked="checkedClientIds.length === filteredClients.length && filteredClients.length > 0"
    :indeterminate="checkedClientIds.length > 0 && checkedClientIds.length < filteredClients.length"
    @update:checked="(checked) => {
      if (checked) {
        selectAllClients();
      } else {
        deselectAllClients();
      }
    }"
  />
  <n-button type="primary" size="small" @click="showCreateClient = true">创建 Client</n-button>
</n-space>
```

**复选框状态说明**：
- **未选中**：没有任何 Client 被选中
- **半选中（indeterminate）**：部分 Client 被选中
- **全选中**：所有 Client 都被选中

---

## 第一次反馈的问题（已部分修复）

### ✅ 已修复：批量禁用/删除后 Client 消失

**修复内容**：
- 移除了 `loadClients()` 函数中的 `.filter((c) => c.is_active)` 过滤
- 现在显示所有 Client（包括禁用的）

**但是**：由于 Bug #1（删除逻辑问题），这个修复还不完整。

---

## 正确的解决方案（参考 Key 的设计）

### Key 的状态管理设计

查看 `app/db/models.py` 中的 ApiKey 模型：
```python
class ApiKey(Base):
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
```

查看 Key 的删除逻辑 `app/api/control_plane.py`：
```python
@router.delete("/keys/{key_id}", status_code=204)
def delete_key(request: Request, key_id: int, db: Session = Depends(get_db)):
    key.is_active = False
    key.status = "disabled"  # 软删除，设置状态为 disabled
```

**Key 的状态值**：
- `"active"` - 正常使用
- `"disabled"` - 已禁用/已删除
- `"cooling"` - 冷却中
- `"quota_exceeded"` - 配额耗尽
- `"failed"` - 失败

### Client 应该采用相同的设计

**添加 status 字段**：
```python
class Client(Base):
    __tablename__ = "clients"

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")  # 新增
```

**Client 的状态值**：
- `"active"` - 正常使用
- `"disabled"` - 已禁用（临时停用，可以重新启用）
- `"deleted"` - 已删除（永久删除，不再显示）

**修改后的逻辑**：
```python
if payload.action == BatchAction.ENABLE:
    client.is_active = True
    client.status = "active"
elif payload.action == BatchAction.DISABLE:
    client.is_active = False
    client.status = "disabled"  # 禁用
elif payload.action == BatchAction.DELETE:
    client.is_active = False
    client.status = "deleted"  # 删除
```

**前端过滤逻辑**：
```typescript
// 不显示已删除的 Client
clients.value = (await fetchClients()).filter((c) => c.status !== 'deleted');
```

**优点**：
- ✅ 与 Key 的设计保持一致
- ✅ 使用字符串状态，更灵活，可以扩展更多状态
- ✅ 不需要额外的 `is_deleted` 字段
- ✅ 语义清晰：`status = "disabled"` 表示禁用，`status = "deleted"` 表示删除

---

**优点**：
- 语义清晰，易于理解
- 查询简单：`WHERE is_deleted = FALSE`
- 布尔字段，性能好

**缺点**：
- 需要数据库迁移

**实现复杂度**：中等

---

#### 方案B：使用 `deleted_at` 时间戳

**优点**：
- 可以记录删除时间，便于审计
- 可以实现"软删除后 N 天自动清理"

**缺点**：
- 需要数据库迁移
- 查询稍复杂：`WHERE deleted_at IS NULL`
- 时间戳字段，占用空间稍大

**实现复杂度**：中等

---

#### 方案C：DELETE 直接物理删除

**优点**：
- 简单直接，不需要额外字段

**缺点**：
- ❌ 丢失历史数据
- ❌ 影响审计日志和请求日志的关联
- ❌ 违反软删除的设计原则
- ❌ 无法恢复误删除的数据

**实现复杂度**：低

**结论**：不推荐

---

### 最终选择：方案A（添加 is_deleted 字段）

**理由**：
1. 最简单、最清晰
2. 保持软删除的优点（保留历史数据）
3. 明确区分"禁用"和"删除"的语义
4. 前端过滤逻辑简单
5. 查询性能好

---

## 详细实施步骤

### 步骤1：修复全选 UI（P0，简单）

**修改文件**：`webui/src/views/ClientsKeysView.vue`

**修改内容**：
1. 移除独立的"全选所有 Client"按钮
2. 在"创建 Client"按钮旁边添加复选框
3. 实现全选/取消全选/半选状态

**代码修改**：
```vue
<!-- 修改前 -->
<div v-else style="padding: 4px 0">
  <n-button size="small" @click="selectAllClients" style="width: 100%">
    全选所有 Client
  </n-button>
</div>
<n-button type="primary" size="small" @click="showCreateClient = true">创建 Client</n-button>

<!-- 修改后 -->
<n-space align="center">
  <n-checkbox
    :checked="allClientsSelected"
    :indeterminate="someClientsSelected"
    @update:checked="handleSelectAll"
  />
  <n-button type="primary" size="small" @click="showCreateClient = true">创建 Client</n-button>
</n-space>
```

**添加计算属性**：
```typescript
const allClientsSelected = computed(() => {
  return checkedClientIds.value.length === filteredClients.value.length
    && filteredClients.value.length > 0;
});

const someClientsSelected = computed(() => {
  return checkedClientIds.value.length > 0
    && checkedClientIds.value.length < filteredClients.value.length;
});

function handleSelectAll(checked: boolean) {
  if (checked) {
    selectAllClients();
  } else {
    deselectAllClients();
  }
}
```

**测试验证**：
- ✅ 复选框在"创建 Client"按钮左边
- ✅ 未选中任何 Client 时，复选框为空
- ✅ 选中部分 Client 时，复选框显示半选状态
- ✅ 选中所有 Client 时，复选框显示全选状态
- ✅ 点击复选框可以全选/取消全选

---

### 步骤2：添加 is_deleted 字段（P0，复杂）

#### 2.1 修改数据库模型

**修改文件**：`app/db/models.py`

**修改内容**：
```python
class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # 新增

    # ... 其他字段保持不变
```

#### 2.2 创建数据库迁移

**命令**：
```bash
alembic revision --autogenerate -m "add is_deleted field to clients table"
```

**生成的迁移文件**（示例）：
```python
"""add is_deleted field to clients table

Revision ID: 0007_add_is_deleted
Revises: 0006_add_upstream_resource_bindings
Create Date: 2026-02-25 XX:XX:XX.XXXXXX

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0007_add_is_deleted'
down_revision = '0006_add_upstream_resource_bindings'
branch_labels = None
depends_on = None

def upgrade():
    # 添加 is_deleted 字段，默认值为 False
    op.add_column('clients', sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default='0'))

def downgrade():
    # 回滚：删除 is_deleted 字段
    op.drop_column('clients', 'is_deleted')
```

**运行迁移**：
```bash
alembic upgrade head
```

#### 2.3 修改后端批量删除逻辑

**修改文件**：`app/api/control_plane.py`

**修改内容**：
```python
# 修改前
elif payload.action == BatchAction.DELETE:
    # 软删除：设置 is_active = False
    client.is_active = False

# 修改后
elif payload.action == BatchAction.DELETE:
    # 软删除：标记为已删除
    client.is_deleted = True
    client.is_active = False  # 同时禁用
```

#### 2.4 修改后端单个删除逻辑（保持一致性）

**修改文件**：`app/api/control_plane.py`

**查找**：`DELETE /admin/clients/{id}` 端点

**修改内容**：
```python
# 修改前
@router.delete("/clients/{id}", status_code=204)
def delete_client(client_id: int, db: Session = Depends(get_db)):
    client = db.query(Client).filter(Client.id == client_id).one_or_none()
    if client is None:
        raise FcamError(status_code=404, code="NOT_FOUND", message="Not found")

    client.is_active = False  # 软删除
    db.commit()
    return Response(status_code=204)

# 修改后
@router.delete("/clients/{id}", status_code=204)
def delete_client(client_id: int, db: Session = Depends(get_db)):
    client = db.query(Client).filter(Client.id == client_id).one_or_none()
    if client is None:
        raise FcamError(status_code=404, code="NOT_FOUND", message="Not found")

    client.is_deleted = True  # 标记为已删除
    client.is_active = False  # 同时禁用
    _audit(db, request=request, action="client.delete", resource_type="client", resource_id=str(client.id))
    db.commit()
    return Response(status_code=204)
```

#### 2.5 修改后端列表 API（推荐）

**修改文件**：`app/api/control_plane.py`

**修改内容**：
```python
# 修改前
@router.get("/clients")
def list_clients(db: Session = Depends(get_db)):
    clients = db.query(Client).all()
    return {"items": clients}

# 修改后
@router.get("/clients")
def list_clients(
    include_deleted: bool = Query(False, description="是否包含已删除的 Client"),
    db: Session = Depends(get_db)
):
    query = db.query(Client)
    if not include_deleted:
        query = query.filter(Client.is_deleted == False)
    clients = query.all()
    return {"items": clients}
```

**优点**：
- 后端默认过滤已删除的记录
- 前端不需要额外的过滤逻辑
- 可选参数 `include_deleted` 允许查看已删除的记录（用于管理或恢复）

#### 2.6 修改前端类型定义

**修改文件**：`webui/src/api/clients.ts`

**修改内容**：
```typescript
export type ClientItem = {
  id: number;
  name: string;
  is_active: boolean;
  is_deleted: boolean;  // 新增
  daily_quota: number | null;
  daily_usage: number;
  quota_reset_at: string | null;
  rate_limit_per_min: number;
  max_concurrent: number;
  created_at: string;
  last_used_at: string | null;
};
```

#### 2.7 修改前端加载逻辑（如果后端不过滤）

**修改文件**：`webui/src/views/ClientsKeysView.vue`

**修改内容**：
```typescript
// 如果后端已经过滤，这一步可以省略
async function loadClients() {
  if (!adminToken.value) return;
  loadingClients.value = true;
  try {
    // 后端已经过滤了 is_deleted=true 的记录
    clients.value = await fetchClients();

    // 或者前端过滤（如果后端不过滤）
    // clients.value = (await fetchClients()).filter((c) => !c.is_deleted);

    if (!clients.value.length) {
      selectedClientId.value = null;
      return;
    }

    const currentId = selectedClientId.value;
    const exists = currentId !== null && clients.value.some((c) => c.id === currentId);
    if (!exists) selectedClientId.value = clients.value[0].id;
  } catch (err: unknown) {
    message.error(getFcamErrorMessage(err), { duration: 5000 });
  } finally {
    loadingClients.value = false;
  }
}
```

---

### 步骤3：测试验证

#### 3.1 全选功能测试

**测试场景 1：全选复选框位置**
- ✅ 复选框在"创建 Client"按钮左边
- ✅ 复选框大小适中，易于点击

**测试场景 2：全选状态**
- ✅ 未选中任何 Client：复选框为空
- ✅ 选中部分 Client：复选框显示半选状态（横线）
- ✅ 选中所有 Client：复选框显示全选状态（勾选）

**测试场景 3：全选操作**
- ✅ 点击空复选框：全选所有 Client
- ✅ 点击全选复选框：取消全选
- ✅ 点击半选复选框：全选所有 Client

#### 3.2 禁用功能测试

**测试场景 4：批量禁用**
1. 创建 3 个启用状态的 Client
2. 勾选这 3 个 Client
3. 点击"批量禁用"
4. ✅ 验证：Client 仍在列表中，状态标签显示"禁用"（灰色）
5. ✅ 验证：`is_active = false`, `is_deleted = false`

**测试场景 5：批量启用**
1. 勾选多个禁用状态的 Client
2. 点击"批量启用"
3. ✅ 验证：Client 状态变为"启用"（绿色）
4. ✅ 验证：`is_active = true`, `is_deleted = false`

#### 3.3 删除功能测试

**测试场景 6：批量删除**
1. 创建 3 个 Client
2. 勾选这 3 个 Client
3. 点击"批量删除"
4. 确认操作
5. ✅ 验证：Client 从列表中消失（被过滤）
6. ✅ 验证数据库：`is_deleted = true`, `is_active = false`

**测试场景 7：单个删除**
1. 选中一个 Client
2. 点击"禁用 Client"按钮（如果有单独的删除按钮）
3. ✅ 验证：Client 从列表中消失
4. ✅ 验证数据库：`is_deleted = true`

**测试场景 8：已删除的 Client 不能启用**
1. 删除一个 Client
2. 尝试通过 API 启用该 Client
3. ✅ 验证：操作失败或被忽略（已删除的不应该被启用）

#### 3.4 审计日志测试

**测试场景 9：删除操作记录**
1. 执行批量删除操作
2. 查看审计日志
3. ✅ 验证：每个被删除的 Client 都有审计日志
4. ✅ 验证：action 为 `client.batch.delete`
5. ✅ 验证：包含 Client ID 和名称

#### 3.5 回归测试

**测试场景 10：创建禁用状态的 Client**
1. 创建 Client，`is_active = false`
2. ✅ 验证：Client 在列表中可见，状态显示"禁用"
3. ✅ 验证：`is_deleted = false`

**测试场景 11：搜索功能**
1. 搜索 Client 名称
2. ✅ 验证：搜索结果不包含已删除的 Client
3. ✅ 验证：搜索结果包含禁用的 Client

**测试场景 12：请求日志关联**
1. 查看已删除 Client 的历史请求日志
2. ✅ 验证：日志仍然存在
3. ✅ 验证：Client 信息正确显示（可选：标注"已删除"）

---

## 数据迁移注意事项

### 现有数据处理

**问题**：现有数据库中的 Client 没有 `is_deleted` 字段

**解决方案**：
1. 迁移脚本中设置默认值为 `False`
2. 所有现有 Client 的 `is_deleted` 都为 `False`
3. 不需要手动处理现有数据

**迁移 SQL**：
```sql
-- 添加字段，默认值为 False
ALTER TABLE clients ADD COLUMN is_deleted BOOLEAN NOT NULL DEFAULT 0;
```

### 回滚方案

如果迁移出现问题，可以回滚：

```bash
# 回滚到上一个版本
alembic downgrade -1
```

回滚后：
- `is_deleted` 字段被删除
- 数据恢复到迁移前的状态
- 需要重新部署旧版本的代码

---

## 风险评估

### 高风险项

1. **数据库迁移失败**
   - 风险：迁移过程中出错，导致数据库不一致
   - 缓解：在测试环境先验证迁移脚本
   - 缓解：备份生产数据库

2. **现有 Client 的行为变化**
   - 风险：之前"软删除"的 Client（`is_active=false`）现在会显示出来
   - 缓解：这实际上是修复，不是风险
   - 缓解：在发布说明中告知用户

### 中风险项

1. **API 兼容性**
   - 风险：前端旧版本可能不识别 `is_deleted` 字段
   - 缓解：前后端同时部署
   - 缓解：`is_deleted` 字段有默认值，不会导致错误

2. **审计日志变化**
   - 风险：删除操作的审计日志格式可能变化
   - 缓解：保持审计日志格式不变，只是语义更清晰

### 低风险项

1. **性能影响**
   - 风险：新增字段和过滤条件可能影响查询性能
   - 缓解：`is_deleted` 是布尔字段，性能影响极小
   - 缓解：可以添加索引（如果需要）

---

## 实施时间估算

- **步骤1（全选 UI）**：30 分钟
  - 修改前端代码：15 分钟
  - 测试验证：15 分钟

- **步骤2（is_deleted 字段）**：90 分钟
  - 修改数据库模型：10 分钟
  - 创建和验证迁移：20 分钟
  - 修改后端 API：30 分钟
  - 修改前端类型和逻辑：15 分钟
  - 测试验证：15 分钟

- **步骤3（全面测试）**：30 分钟

- **文档更新**：15 分钟

**总计**：约 2.5 小时

---

## 发布说明

### 给用户的说明

**标题**：Client 批量管理功能重要修复

**内容**：

我们修复了 Client 批量管理功能的两个重要问题：

1. **全选功能优化**：
   - 现在在"创建 Client"按钮旁边有一个复选框
   - 点击即可全选/取消全选所有 Client
   - 支持半选状态显示

2. **删除和禁用功能区分**：
   - **禁用**：临时停用 Client，仍在列表中显示，可以重新启用
   - **删除**：永久删除 Client，从列表中移除，不可恢复
   - 之前这两个操作的效果是一样的，现在已经修复

**注意事项**：
- 已删除的 Client 不会再显示在列表中
- 如果您之前"删除"的 Client 现在又出现了，那是因为之前的删除实际上只是禁用
- 您可以重新执行删除操作来真正删除它们

---

## 相关文件清单

### 需要修改的文件

**后端**：
- `app/db/models.py` - 添加 `is_deleted` 字段
- `migrations/versions/0007_add_is_deleted.py` - 新建迁移文件
- `app/api/control_plane.py` - 修改批量删除和单个删除逻辑

**前端**：
- `webui/src/api/clients.ts` - 更新类型定义
- `webui/src/views/ClientsKeysView.vue` - 修改全选 UI 和加载逻辑

**文档**：
- `docs/WORKLOG.md` - 记录本次修复
- `docs/MVP/Firecrawl-API-Manager-API-Contract.md` - 更新 API 文档（可选）

**测试**：
- `tests/integration/test_batch_clients.py` - 可能需要更新测试用例

---

## 后续优化建议

### 可选功能

1. **恢复已删除的 Client**
   - 添加"查看已删除"选项
   - 提供"恢复"按钮
   - 设置 `is_deleted = False`

2. **自动清理**
   - 定期清理已删除超过 N 天的 Client
   - 物理删除数据库记录
   - 需要配置和定时任务

3. **删除确认增强**
   - 要求输入 Client 名称确认
   - 显示关联的 API Key 数量
   - 警告删除的影响

4. **批量恢复**
   - 添加批量恢复功能
   - 允许恢复误删除的 Client

---

## 总结

本次修复解决了两个严重问题：

1. ✅ **全选 UI 优化**：采用标准的复选框模式，更符合用户习惯
2. ✅ **删除和禁用区分**：通过 `is_deleted` 字段明确区分两种操作

修复后的效果：
- 用户可以清楚地区分禁用和删除
- 已删除的 Client 不会再出现在列表中
- 全选操作更加便捷和直观

**下一步**：按照实施步骤逐步修复，每一步都要测试验证。


---

## Bug 列表

### 🔴 P0 - 严重问题（功能性 Bug）

#### Bug #1: 批量禁用/删除后 Client 从列表消失

**问题描述**：
- 用户创建 Client 后，执行批量禁用或批量删除操作
- 操作成功，但 Client 从 UI 列表中完全消失
- 用户误以为操作失败或 Client 被物理删除

**复现步骤**：
1. 创建一个或多个 Client
2. 勾选这些 Client
3. 点击"批量禁用"或"批量删除"
4. 确认操作
5. 观察：Client 从列表中消失

**根本原因**：
- 文件：`webui/src/views/ClientsKeysView.vue`
- 代码位置：`loadClients()` 函数
- 问题代码：
  ```typescript
  clients.value = (await fetchClients()).filter((c) => c.is_active);
  ```
- 分析：前端过滤掉了所有 `is_active=false` 的 Client

**影响范围**：
- 批量禁用操作后，Client 消失
- 批量删除操作后，Client 消失（软删除设置 `is_active=false`）
- 用户无法看到禁用状态的 Client
- 用户无法对禁用的 Client 执行批量启用操作

**期望行为**：
- 显示所有 Client（包括禁用的）
- 通过状态标签区分启用/禁用状态
- 禁用的 Client 应该可见，只是状态不同

**修复方案**：
```typescript
// 修改前
clients.value = (await fetchClients()).filter((c) => c.is_active);

// 修改后
clients.value = await fetchClients();
```

---

#### Bug #2: 创建禁用状态的 Client 后看不到

**问题描述**：
- 用户创建 Client 时，将 `is_active` 设置为 `false`
- 创建成功提示显示，但 Client 不在列表中
- 用户误以为创建失败

**复现步骤**：
1. 点击"创建 Client"
2. 填写名称等信息
3. 将"启用"开关关闭（`is_active=false`）
4. 提交创建
5. 观察：提示创建成功，但列表中看不到新 Client

**根本原因**：
- 同 Bug #1，前端过滤掉了 `is_active=false` 的 Client

**影响范围**：
- 用户无法创建禁用状态的 Client（虽然后端创建成功）
- 用户体验混乱

**期望行为**：
- 创建禁用状态的 Client 后，应该在列表中可见
- 状态标签显示"禁用"

**修复方案**：
- 同 Bug #1

---

### 🟡 P1 - 重要功能缺失

#### Issue #3: 缺少全选功能

**问题描述**：
- 用户需要批量操作多个 Client 时，必须逐个勾选
- 缺少"全选"和"取消全选"功能
- 操作效率低下

**期望行为**：
- 在 Client 列表顶部或批量操作区域添加"全选"按钮
- 点击后选中当前筛选结果中的所有 Client
- 再次点击取消全选

**实现方案**：
1. 在批量操作按钮区域添加"全选"和"取消全选"按钮
2. 或者在列表顶部添加一个全选复选框（更符合 UI 惯例）

**参考实现**：
```typescript
// 全选功能
function selectAllClients() {
  checkedClientIds.value = filteredClients.value.map(c => c.id);
}

// 取消全选
function deselectAllClients() {
  checkedClientIds.value = [];
}
```

---

### 🟢 P2 - 体验优化

#### Issue #4: 日志时间显示格式过于复杂

**问题描述**：
- 当前时间格式：`2026-02-25T07:38:33.656012Z`（ISO 8601 格式）
- 包含毫秒、微秒等精度，过于冗长
- 用户只需要到秒级别的精度

**期望格式**：
- `2026-02-25 07:38:33`（年-月-日 时:分:秒）
- 或 `2026-02-25 15:38:33`（本地时区）

**影响范围**：
- 请求日志（Request Logs）
- 审计日志（Audit Logs）
- 所有时间戳显示

**实现方案**：
- 在前端格式化时间显示
- 创建统一的时间格式化函数
- 应用到所有日志视图

**参考实现**：
```typescript
function formatTimestamp(timestamp: string): string {
  const date = new Date(timestamp);
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false
  }).replace(/\//g, '-');
}
```

---

#### Issue #5: 请求日志缺少列选择功能

**问题描述**：
- 审计日志有列选择功能，可以自主选择显示哪些列
- 请求日志没有此功能，所有列都强制显示
- 用户无法根据需求定制显示内容

**期望行为**：
- 参考审计日志的实现
- 添加列选择下拉菜单
- 用户可以选择显示/隐藏特定列
- 选择结果保存到 localStorage

**实现方案**：
- 参考 `ClientsKeysView.vue` 中 Key 列表的列选择实现
- 为请求日志添加类似的列配置和选择功能

---

## 修复优先级

1. **立即修复（P0）**：
   - Bug #1: 批量禁用/删除后 Client 消失
   - Bug #2: 创建禁用状态的 Client 后看不到

2. **尽快实现（P1）**：
   - Issue #3: 缺少全选功能

3. **后续优化（P2）**：
   - Issue #4: 日志时间显示格式优化
   - Issue #5: 请求日志列选择功能

---

## 测试计划

### P0 Bug 修复验证

**测试场景 1：批量禁用后 Client 可见**
1. 创建 3 个启用状态的 Client
2. 勾选这 3 个 Client
3. 点击"批量禁用"
4. 确认操作
5. ✅ 验证：Client 仍在列表中，状态标签显示"禁用"

**测试场景 2：批量删除后 Client 可见**
1. 创建 3 个 Client
2. 勾选这 3 个 Client
3. 点击"批量删除"
4. 确认操作
5. ✅ 验证：Client 仍在列表中，状态标签显示"禁用"

**测试场景 3：创建禁用状态的 Client**
1. 点击"创建 Client"
2. 填写信息，将"启用"开关关闭
3. 提交创建
4. ✅ 验证：Client 在列表中可见，状态标签显示"禁用"

**测试场景 4：批量启用禁用的 Client**
1. 勾选多个禁用状态的 Client
2. 点击"批量启用"
3. ✅ 验证：Client 状态变为"启用"

### P1 功能验证

**测试场景 5：全选功能**
1. 列表中有多个 Client
2. 点击"全选"按钮
3. ✅ 验证：所有 Client 被勾选
4. 点击"取消全选"
5. ✅ 验证：所有勾选被清除

### P2 优化验证

**测试场景 6：时间格式显示**
1. 查看请求日志
2. ✅ 验证：时间格式为 `YYYY-MM-DD HH:mm:ss`
3. 查看审计日志
4. ✅ 验证：时间格式一致

**测试场景 7：列选择功能**
1. 打开请求日志
2. 点击"列"按钮
3. ✅ 验证：显示列选择菜单
4. 取消勾选某些列
5. ✅ 验证：对应列被隐藏
6. 刷新页面
7. ✅ 验证：列选择状态被保存

---

## 回归测试

修复完成后，需要验证以下功能未受影响：

- ✅ 单个 Client 的启用/禁用操作
- ✅ 单个 Client 的删除操作
- ✅ 批量启用操作
- ✅ 批量禁用操作
- ✅ 批量删除操作
- ✅ 部分失败场景的错误提示
- ✅ 审计日志记录
- ✅ Client 搜索功能
- ✅ Client 详情查看

---

## 相关文件

**前端文件**：
- `webui/src/views/ClientsKeysView.vue` - Client 列表视图
- `webui/src/views/LogsView.vue` - 日志视图（如果存在）

**后端文件**：
- 无需修改（后端逻辑正确）

**测试文件**：
- `tests/integration/test_batch_clients.py` - 批量操作测试（无需修改）

---

## 预期影响

**用户体验改善**：
- ✅ 用户可以看到所有 Client，包括禁用的
- ✅ 批量操作后不会产生"Client 消失"的困惑
- ✅ 全选功能提升操作效率
- ✅ 时间格式更易读
- ✅ 列选择功能提升灵活性

**风险评估**：
- 🟢 低风险：主要是前端显示逻辑修改
- 🟢 后端无需修改，不影响数据完整性
- 🟢 现有测试用例无需修改

---

## 实施时间估算

- P0 Bug 修复：30 分钟
- P1 功能实现：20 分钟
- P2 优化实现：40 分钟
- 测试验证：30 分钟
- **总计**：约 2 小时

---

## 备注

- 此次修复不涉及后端 API 修改
- 所有修改都在前端 UI 层
- 需要重新构建前端：`cd webui && npm run build`
- 修复完成后需要更新 WORKLOG.md
