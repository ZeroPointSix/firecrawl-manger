# TDD：Client 批量管理功能测试驱动开发

> **PRD**：`docs/PRD/2026-02-25-client-batch-operations.md`
> **FD**：`docs/FD/2026-02-25-client-batch-operations-fd.md`
> **创建时间**：2026-02-25
> **状态**：Draft
> **优先级**：P2（功能增强/管理效率）

---

## 0. 结论先行（我们要交付什么）

P2 交付物分两条线：

1) **后端批量操作 API**
   - 新增 `PATCH /admin/clients/batch` 端点
   - 支持 `enable`、`disable`、`delete` 三种操作
   - 返回详细的成功/失败统计
   - 使用数据库事务保证原子性
   - 记录审计日志

2) **前端批量选择与操作 UI**
   - Client 列表添加复选框列（支持单选/多选/全选）
   - 批量操作按钮区域（启用、禁用、删除）
   - 危险操作二次确认（禁用、删除）
   - 部分失败时显示详细提示

---

## 1. 约束与假设

- 代码基于 SQLAlchemy ORM + FastAPI（见 `app/db/models.py`、`app/api/control_plane.py`）
- 前端基于 Vue 3 + Naive UI（见 `webui/src/views/ClientsKeysView.vue`）
- 测试覆盖率要求 ≥ 80%（与项目整体要求一致）
- 批量操作限制最多 100 个 Client（防止性能问题）
- 批量操作需要 Admin Token 鉴权
- 批量删除为软删除（设置 `is_active=False`），不是物理删除

---

## 2. 当前实现要点（作为设计输入）

### 2.1 数据模型（app/db/models.py）

**Client 模型**：
```python
class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # ... 其他字段
```

**关键字段**：
- `id`：主键，批量操作的标识符
- `is_active`：启用/禁用状态，批量操作的目标字段

### 2.2 现有 API（app/api/control_plane.py）

- `GET /admin/clients`：列出所有 Client
- `POST /admin/clients`：创建 Client
- `PUT /admin/clients/{id}`：更新 Client
- `DELETE /admin/clients/{id}`：删除 Client（软删除）

### 2.3 前端实现（webui/src）

- 使用 Naive UI 的 `n-data-table` 组件
- 当前不支持复选框选择
- API 调用封装在 `webui/src/api/clients.ts`

---

## 3. 测试策略

### 3.1 测试金字塔

```
       E2E 测试 (5%)
      /          \
     /  集成测试  \  (25%)
    /              \
   /   单元测试     \  (70%)
  /________________\
```

**单元测试（70%）**：
- 批量操作核心逻辑
- 参数验证
- 错误处理

**集成测试（25%）**：
- API 端点完整流程
- 数据库事务
- 审计日志

**E2E 测试（5%）**：
- 完整的用户操作流程
- 前后端集成

### 3.2 测试覆盖率要求

- 整体覆盖率：≥ 80%
- 核心模块覆盖率：≥ 90%
  - `app/api/control_plane.py`（批量操作 API）
  - `app/core/batch_operations.py`（如果抽取核心逻辑）

---

## 4. 后端测试设计

### 4.1 单元测试

#### 4.1.1 参数验证测试

**测试用例 1：client_ids 为空**
```python
def test_batch_clients_empty_ids():
    """测试 client_ids 为空时返回 400"""
    response = client.patch("/admin/clients/batch", json={
        "client_ids": [],
        "action": "enable"
    }, headers=admin_headers)

    assert response.status_code == 400
    assert "client_ids" in response.json()["detail"].lower()
```

**测试用例 2：action 无效**
```python
def test_batch_clients_invalid_action():
    """测试 action 无效时返回 400"""
    response = client.patch("/admin/clients/batch", json={
        "client_ids": [1, 2, 3],
        "action": "invalid_action"
    }, headers=admin_headers)

    assert response.status_code == 400
    assert "action" in response.json()["detail"].lower()
```

**测试用例 3：client_ids 超过上限**
```python
def test_batch_clients_exceed_limit():
    """测试 client_ids 超过 100 个时返回 400"""
    response = client.patch("/admin/clients/batch", json={
        "client_ids": list(range(1, 102)),  # 101 个
        "action": "enable"
    }, headers=admin_headers)

    assert response.status_code == 400
    assert "100" in response.json()["detail"]
```

#### 4.1.2 鉴权测试

**测试用例 4：未提供 Admin Token**
```python
def test_batch_clients_no_auth():
    """测试未提供 Admin Token 时返回 401"""
    response = client.patch("/admin/clients/batch", json={
        "client_ids": [1, 2, 3],
        "action": "enable"
    })

    assert response.status_code == 401
```

**测试用例 5：Admin Token 无效**
```python
def test_batch_clients_invalid_auth():
    """测试 Admin Token 无效时返回 401"""
    response = client.patch("/admin/clients/batch", json={
        "client_ids": [1, 2, 3],
        "action": "enable"
    }, headers={"Authorization": "Bearer invalid_token"})

    assert response.status_code == 401
```

---

### 4.2 集成测试

#### 4.2.1 批量启用测试

**测试用例 6：批量启用成功（所有 Client 都存在且为禁用状态）**
```python
def test_batch_enable_clients_success(db: Session, admin_headers: dict):
    """测试批量启用成功"""
    # 准备测试数据：创建 3 个禁用的 Client
    clients = []
    for i in range(3):
        client = Client(
            name=f"test_client_{i}",
            token_hash=f"hash_{i}",
            is_active=False
        )
        db.add(client)
        db.commit()
        db.refresh(client)
        clients.append(client)

    client_ids = [c.id for c in clients]

    # 执行批量启用
    response = client.patch("/admin/clients/batch", json={
        "client_ids": client_ids,
        "action": "enable"
    }, headers=admin_headers)

    # 断言响应
    assert response.status_code == 200
    data = response.json()
    assert data["success_count"] == 3
    assert data["failed_count"] == 0
    assert len(data["failed_items"]) == 0

    # 验证数据库状态
    for client_id in client_ids:
        db_client = db.query(Client).filter(Client.id == client_id).first()
        assert db_client is not None
        assert db_client.is_active is True
```

**测试用例 7：批量启用部分成功（部分 Client 不存在）**
```python
def test_batch_enable_clients_partial_success(db: Session, admin_headers: dict):
    """测试批量启用部分成功"""
    # 准备测试数据：创建 2 个禁用的 Client
    clients = []
    for i in range(2):
        client = Client(
            name=f"test_client_{i}",
            token_hash=f"hash_{i}",
            is_active=False
        )
        db.add(client)
        db.commit()
        db.refresh(client)
        clients.append(client)

    client_ids = [c.id for c in clients] + [9999]  # 9999 不存在

    # 执行批量启用
    response = client.patch("/admin/clients/batch", json={
        "client_ids": client_ids,
        "action": "enable"
    }, headers=admin_headers)

    # 断言响应
    assert response.status_code == 200
    data = response.json()
    assert data["success_count"] == 2
    assert data["failed_count"] == 1
    assert len(data["failed_items"]) == 1
    assert data["failed_items"][0]["client_id"] == 9999
    assert "not found" in data["failed_items"][0]["error"].lower()
```

#### 4.2.2 批量禁用测试

**测试用例 9：批量禁用成功**
```python
def test_batch_disable_clients_success(db: Session, admin_headers: dict):
    """测试批量禁用成功"""
    # 准备测试数据：创建 3 个启用的 Client
    clients = []
    for i in range(3):
        client = Client(
            name=f"test_client_{i}",
            token_hash=f"hash_{i}",
            is_active=True
        )
        db.add(client)
        db.commit()
        db.refresh(client)
        clients.append(client)

    client_ids = [c.id for c in clients]

    # 执行批量禁用
    response = client.patch("/admin/clients/batch", json={
        "client_ids": client_ids,
        "action": "disable"
    }, headers=admin_headers)

    # 断言响应
    assert response.status_code == 200
    data = response.json()
    assert data["success_count"] == 3
    assert data["failed_count"] == 0
```

#### 4.2.3 批量删除测试

**测试用例 10：批量删除成功**
```python
def test_batch_delete_clients_success(db: Session, admin_headers: dict):
    """测试批量删除成功（软删除）"""
    # 准备测试数据
    clients = []
    for i in range(3):
        client = Client(
            name=f"test_client_{i}",
            token_hash=f"hash_{i}     is_active=True
        )
        db.add(client)
        db.commit()
        db.refresh(client)
        clients.append(client)

    client_ids = [c.id for c in clients]

    # 执行批量删除
    response = client.patch("/admin/clients/batch", json={
        "client_ids": client_ids,
        "action": "delete"
    }, headers=admin_headers)

    # 断言响应
    assert response.status_code == 200
    assert response.json()["success_count"] == 3

    # 验证数据库状态（软删除）
    for client_id in client_ids:
        db_client = db.query(Client).filter(Client.id == client_id).first()
        assert db_client is not None  # 记录仍存在
        assert db_client.is_active is False  # 但已禁用
```

---

### 4.3 并发测试

**测试用例 11：并发批量启用**
```python
from concurrent.futures import ThreadPoolExecutor

def test_concurrent_batch_enable(db: Session, admin_headers: dict):
    """测试并发批量启用的数据一致性"""
    # 准备测试数据
    clients = []
    for i in range(5):
        client = Client(name=f"test_{i}", token_hash=f"hash_{i}", is_active=False)
        db.add(client)
        db.commit()
        db.refresh(client)
        clients.append(client)

    client_ids = [c.id for c in clients]

    def batch_enable():
        return client.patch("/admin/clients/batch", json={
            "client_ids": client_ids, "action": "enable"
        }, headers=admin_headers).json()

    # 并发执行
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [executor.submit(batch_enable).result() for _ in range(2)]

    # 验证一致性
    for cid in client_ids:
        assert db.query(Client).get(cid).is_active is True
```

### 4.4 事务测试

**测试用例 12：数据库错误应该回滚**
```python
from unittest.mock import patch

def test_batch_rollback_on_error(db: Session, admin_headers: dict):
    """测试批量操作出错时回滚"""
    clients = [Client(name=f"t_{i}", token_hash=f"h_{i}", is_active=False) 
               for i in range(3)]
    for c in clients:
        db.add(c)
    db.commit()

    with patch.object(db, 'commit', side_effect=Exception("DB error")):
        response = client.patch("/admin/clients/batch", json={
            "client_ids": [c.id for c in clients], "action": "enable"
        }, headers=admin_headers)
        assert response.status_code == 500

    # 验证回滚
    for c in clients:
        assert db.query(Client).get(c.id).is_active is False
```

---

## 5. 前端测试设计

### 5.1 组件测试

**测试用例 13：复选框选择**
```typescript
import { mount } from '@vue/test-utils';

describe('ClientsKeysView', () => {
  it('应该支持单选', async () => {
    const wrapper = mount(ClientsKeysView);
    await wrapper.find('input[type="checkbox"]').setChecked(true);
    expect(wrapper.vm.selecteength).toBeGreaterThan(0);
  });

  it('未选择时批量按钮应禁用', () => {
    const wrapper = mount(ClientsKeysView);
    expect(wrapper.find('.batch-enable-btn').attributes('disabled')).toBeDefined();
  });
});
```

---

## 6. 实施任务清单

### 6.1 后端任务（P0）

- [ ] 新增 BatchClientRequest/Response 模型
- [ ] 实现 PATCH /admin/clients/batch API
- [ ] 添加参数验证
- [ ] 实现批量启用/禁用/删除逻辑
- [ ] 添加事务处理
- [ ] 添加审计日志
- [ ] 单元测试（覆盖率 ≥ 90%）
- [ ] 集成测试
- [ ] 并发测试

### 6.2 前端任务（P0）

- [ ] 添加复选框列
- [ ] 实现选择状态管理
- [ ] 添加批量操作按钮
- [ ] 实现批量启用/禁用/删除
- [ ] 添加二次确认
- [ ] 实现部分失败处理
- [ ] 封装 API
- [ ] 组件测试

### 6.3 文档任务（P1）

- [ ] 更新 API 契约
- [ ] 更新使用指南
- [ ] 更新变更日志

---

## 7. 测试执行计划

### 7.1 开发阶段

**单元测试**
- 时机：每次提交前
- 覆盖率：≥ 80%
- 命令：`pytest tests/unit/ -v --cov`

**集成测试**
- 时机：功能完成后
- 命令：`pytest tests/integration/test_batch_clients.py -v`

**E2E 测试**
- 时机：前后端集成后
- 命令：`pytest tests/e2e/ -v`

### 7.2 验收标准

- [ ] 所有测试通过
- [ ] 覆盖率 ≥ 80%
- [ ] 核心模块 ≥ 90%
- [ ] 无已知[ ] 性能达标（100 个 Client < 5s）

---

## 8. 发布与回滚

### 8.1 发布流程

1. 代码审查
2. 测试通过
3. 部署测试环境
4. 部署生产环境

### 8.2 回滚策略

- 前端：回滚代码重新构建
- 后端：回滚代码重启服务
- 数据：通过审计日志恢复

---

## 9. 参考资料

- PRD: `docs/PRD/2026-02-25-client-batch-operations.md`
- FD: `docs/FD/2026-02-25-client-batch-operations-fd.md`
- API 契约: `docs/MVP/Firecrawl-API-Manager-API-Contract.md`
