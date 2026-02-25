"""
集成测试：Client 批量操作 API

测试 PATCH /admin/clients/batch 端点的完整功能，包括：
- 批量启用
- 批量禁用
- 批量删除
- 参数验证
- 鉴权
- 部分失败处理
- 并发操作
- 事务回滚
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig, Secrets
from app.core.security import hmac_sha256_hex
from app.db.models import Base, Client
from app.main import create_app

pytestmark = pytest.mark.integration


def _make_app(tmp_path):
    """创建测试应用"""
    config = AppConfig()
    config.database.path = (tmp_path / "test_batch.db").as_posix()
    secrets = Secrets(admin_token="test_admin", master_key="test_master_key_32_bytes_min____")
    app = create_app(config=config, secrets=secrets)
    Base.metadata.create_all(app.state.db_engine)
    return app, secrets


def _admin_headers():
    """返回 Admin 鉴权头"""
    return {"Authorization": "Bearer test_admin"}


def _create_client(app, name: str, is_active: bool = True) -> int:
    """创建测试 Client 并返回 ID"""
    from sqlalchemy.orm import Session

    SessionLocal = app.state.db_session_factory
    db: Session = SessionLocal()
    try:
        client = Client(
            name=name,
            token_hash=hmac_sha256_hex(b"master_key", f"token_{name}"),
            is_active=is_active,
            rate_limit_per_min=60,
            max_concurrent=10,
        )
        db.add(client)
        db.commit()
        db.refresh(client)
        return client.id
    finally:
        db.close()


def _get_client(app, client_id: int) -> Client | None:
    """获取 Client"""
    from sqlalchemy.orm import Session

    SessionLocal = app.state.db_session_factory
    db: Session = SessionLocal()
    try:
        return db.query(Client).filter(Client.id == client_id).first()
    finally:
        db.close()


# ============================================================================
# 1. 参数验证测试
# ============================================================================


def test_batch_clients_empty_ids(tmp_path):
    """测试 client_ids 为空时返回 400"""
    app, _ = _make_app(tmp_path)

    with TestClient(app) as client:
        resp = client.patch(
            "/admin/clients/batch",
            headers=_admin_headers(),
            json={"client_ids": [], "action": "enable"},
        )

    assert resp.status_code == 400
    assert "client_ids" in resp.text.lower()


def test_batch_clients_invalid_action(tmp_path):
    """测试 action 无效时返回 400"""
    app, _ = _make_app(tmp_path)

    with TestClient(app) as client:
        resp = client.patch(
            "/admin/clients/batch",
            headers=_admin_headers(),
            json={"client_ids": [1, 2, 3], "action": "invalid_action"},
        )

    assert resp.status_code == 400  # 验证错误被中间件转换为 400


def test_batch_clients_exceed_limit(tmp_path):
    """测试 client_ids 超过 100 个时返回 400"""
    app, _ = _make_app(tmp_path)

    with TestClient(app) as client:
        resp = client.patch(
            "/admin/clients/batch",
            headers=_admin_headers(),
            json={"client_ids": list(range(1, 102)), "action": "enable"},  # 101 个
        )

    assert resp.status_code == 400
    assert "100" in resp.text


# ============================================================================
# 2. 鉴权测试
# ============================================================================


def test_batch_clients_no_auth(tmp_path):
    """测试未提供 Admin Token 时返回 401"""
    app, _ = _make_app(tmp_path)

    with TestClient(app) as client:
        resp = client.patch(
            "/admin/clients/batch",
            json={"client_ids": [1, 2, 3], "action": "enable"},
        )

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "ADMIN_UNAUTHORIZED"


def test_batch_clients_invalid_auth(tmp_path):
    """测试 Admin Token 无效时返回 401"""
    app, _ = _make_app(tmp_path)

    with TestClient(app) as client:
        resp = client.patch(
            "/admin/clients/batch",
            headers={"Authorization": "Bearer invalid_token"},
            json={"client_ids": [1, 2, 3], "action": "enable"},
        )

    assert resp.status_code == 401


# ============================================================================
# 3. 批量启用测试
# ========================================================================


def test_batch_enable_clients_success(tmp_path):
    """测试批量启用成功（所有 Client 都存在且为禁用状态）"""
    app, _ = _make_app(tmp_path)

    # 创建 3 个禁用的 Client
    client_ids = [
        _create_client(app, f"test_client_{i}", is_active=False) for i in range(3)
    ]

    with TestClient(app) as client:
        resp = client.patch(
            "/admin/clients/batch",
            headers=_admin_headers(),
            json={"client_ids": client_ids, "action": "enable"},
        )

    # 断言响应
    assert resp.status_code == 200
    data = resp.json()
    assert data["success_count"] == 3
    assert data["failed_count"] == 0
    assert len(data["failed_items"]) == 0

    # 验证数据库状态
    for cid in client_ids:
        db_client = _get_client(app, cid)
        assert db_client is not None
        assert db_client.is_active is True


def test_batch_enable_clients_partial_success(tmp_path):
    """测试批量启用部分成功（部分 Client 不存在）"""
    app, _ = _make_app(tmp_path)

    # 创建 2 个禁用的 Client
    client_ids = [
        _create_client(app, f"test_client_{i}", is_active=False) for i in range(2)
    ]
    client_ids.append(9999)  # 不存在的 ID

    with TestClient(app) as client:
        resp = client.patch(
            "/admin/clients/batch",
            headers=_admin_headers(),
            json={"client_ids": client_ids, "action": "enable"},
        )

    # 断言响应
    assert resp.status_code == 200
    data = resp.json()
    assert data["success_count"] == 2
    assert data["failed_count"] == 1
    assert len(data["failed_items"]) == 1
    assert data["failed_items"][0]["client_id"] == 9999
    assert "not found" in data["failed_items"][0]["error"].lower()

    # 验证数据库状态
    for cid in client_ids[:2]:
        db_client = _get_client(app, cid)
        assert db_client.is_active is True


def test_batch_enable_clients_all_failed(tmp_path):
    """测试批量启用全部失败（所有 Client 都不存在）"""
    app, _ = _make_app(tmp_path)

    client_ids = [9997, 9998, 9999]  # 都不存在

    with TestClient(app) as client:
        resp = client.patch(
            "/admin/clients/batch",
            headers=_admin_headers(),
            json={"client_ids": client_ids, "action": "enable"},
        )

    # 断言响应
    assert resp.status_code == 200
    data = resp.json()
    assert data["success_count"] == 0
    assert data["failed_count"] == 3
    assert len(data["failed_items"]) == 3


# ============================================================================
# 4. 批量禁用测试
# ============================================================================


def test_batch_disable_clients_success(tmp_path):
    """测试批量禁用成功（所有 Client 都存在且为启用状态）"""
    app, _ = _make_app(tmp_path)

    # 创建 3 个启用的 Client
    client_ids = [
        _create_client(app, f"test_client_{i}", is_active=True) for i in range(3)
    ]

    with TestClient(app) as client:
        resp = client.patch(
            "/admin/clients/batch",
            headers=_admin_headers(),
            json={"client_ids": client_ids, "action": "disable"},
        )

    # 断言响应
    assert resp.status_code == 200
    data = resp.json()
    assert data["success_count"] == 3
    assert data["failed_count"] == 0

    # 验证数据库状态
    for cid in client_ids:
        db_client = _get_client(app, cid)
        assert db_client.is_active is False


# ============================================================================
# 5. 批量删除测试
# ============================================================================


def test_batch_delete_clients_success(tmp_path):
    """测试批量删除成功（软删除）"""
    app, _ = _make_app(tmp_path)

    # 创建 3 个 Client
    client_ids = [
        _create_client(app, f"test_client_{i}", is_active=True) for i in range(3)
    ]

    with TestClient(app) as client:
        resp = client.patch(
            "/admin/clients/batch",
            headers=_admin_headers(),
            json={"client_ids": client_ids, "action": "delete"},
        )

    # 断言响应
    assert resp.status_code == 200
    data = resp.json()
    assert data["success_count"] == 3
    assert data["failed_count"] == 0

    # 验证数据库状态（软删除，记录仍存在但已禁用）
    for cid in client_ids:
        db_client = _get_client(app, cid)
        assert db_client is not None  # 记录仍存在
        assert db_client.is_active is False  # 但已禁用


# ============================================================================
# 6. 边界测试
# ============================================================================


def test_batch_enable_single_client(tmp_path):
    """测试批量操作 1 个 Client"""
    app, _ = _make_app(tmp_path)

    client_id = _create_client(app, "test_client", is_active=False)

    with TestClient(app) as client:
        resp = client.patch(
            "/admin/clients/batch",
            headers=_admin_headers(),
            json={"client_ids": [client_id], "action": "enable"},
        )

    assert resp.status_code == 200
    assert resp.json()["success_count"] == 1

    db_client = _get_client(app, client_id)
    assert db_client.is_active is True


def test_batch_enable_max_clients(tmp_path):
    """测试批量操作 100 个 Client（上限）"""
    app, _ = _make_app(tmp_path)

    # 创建 100 个 Client
    client_ids = [
        _create_client(app, f"test_client_{i}", is_active=False) for i in range(100)
    ]

    with TestClient(app) as client:
        resp = client.patch(
            "/admin/clients/batch",
            headers=_admin_headers(),
            json={"client_ids": client_ids, "action": "enable"},
        )

    assert resp.status_code == 200
    assert resp.json()["success_count"] == 100


def test_batch_enable_duplicate_ids(tmp_path):
    """测试重复的 Client ID（应该去重）"""
    app, _ = _make_app(tmp_path)

    client_id = _create_client(app, "test_client", is_active=False)

    with TestClient(app) as client:
        resp = client.patch(
            "/admin/clients/batch",
            headers=_admin_headers(),
            json={
                "client_ids": [client_id, client_id, client_id],  # 重复 ID
                "action": "enable",
            },
        )

    # 应该去重处理，只操作一次
    assert resp.status_code == 200
    data = resp.json()
    assert data["success_count"] == 1  # 只成功一次

    db_client = _get_client(app, client_id)
    assert db_client.is_active is True


# ============================================================================
# 7. 并发测试
# ============================================================================


def test_concurrent_batch_enable(tmp_path):
    """测试并发批量启用的数据一致性"""
    app, _ = _make_app(tmp_path)

    # 创建 5 个禁用的 Client
    client_ids = [
        _create_client(app, f"test_client_{i}", is_active=False) for i in range(5)
    ]

    def batch_enable():
        with TestClient(app) as client:
            resp = client.patch(
                "/admin/clients/batch",
                headers=_admin_headers(),
                json={"client_ids": client_ids, "action": "enable"},
            )
            return resp.json()

    # 两个管理员同时批量启用
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(batch_enable) for _ in range(2)]
        results = [f.result() for f in futures]

    # 验证数据一致性：所有 Client 都应该是启用状态
    for cid in client_ids:
        db_client = _get_client(app, cid)
        assert db_client.is_active is True

    # 两次操作都应该成功（幂等性）
    for result in results:
        assert result["success_count"] == 5


def test_concurrent_batch_delete(tmp_path):
    """测试并发批量删除的数据一致性"""
    app, _ = _make_app(tmp_path)

    # 创建 5 个 Client
    client_ids = [
        _create_client(app, f"test_client_{i}", is_active=True) for i in range(5)
    ]

    def batch_delete():
        with TestClient(app) as client:
            resp = client.patch(
                "/admin/clients/batch",
                headers=_admin_headers(),
                json={"client_ids": client_ids, "action": "delete"},
            )
            return resp.json()

    # 两个管理员同时批量删除
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(batch_delete) for _ in range(2)]
        results = [f.result() for f in futures]

    # 验证数据一致性：所有 Client 都应该是禁用状态
    for cid in client_ids:
        db_client = _get_client(app, cid)
        assert db_client.is_active is False


# ============================================================================
# 8. 事务测试
# ============================================================================


def test_batch_enable_rollback_on_error(tmp_path):
    """测试批量操作中途出错时应该回滚"""
    app, _ = _make_app(tmp_path)

    # 创建 3 个 Client
    client_ids = [
        _create_client(app, f"test_client_{i}", is_active=False) for i in range(3)
    ]

    # 模拟数据库错误
    from sqlalchemy.orm import Session

    original_commit = Session.commit

    def mock_commit_error(self):
        raise Exception("Database error")

    with TestClient(app) as client:
        with patch.object(Session, "commit", mock_commit_error):
            resp = client.patch(
                "/admin/clients/batch",
                headers=_admin_headers(),
                json={"client_ids": client_ids, "action": "enable"},
            )

        # 应该返回 503 错误（数据库不可用）
        assert resp.status_code == 503

    # 验证数据库状态：所有 Client 应该仍然是禁用状态（回滚成功）
    for cid in client_ids:
        db_client = _get_client(app, cid)
        assert db_client.is_active is False


# ============================================================================
# 9. 审计日志测试
# ============================================================================


def test_batch_enable_audit_log(tmp_path):
    """测试批量操作记录审计日志"""
    app, _ = _make_app(tmp_path)

    # 创建 3 个 Client
    client_ids = [
        _create_client(app, f"test_client_{i}", is_active=False) for i in range(3)
    ]

    with TestClient(app) as client:
        resp = client.patch(
            "/admin/clients/batch",
            headers=_admin_headers(),
            json={"client_ids": client_ids, "action": "enable"},
        )

    assert resp.status_code == 200

    # 验证审计日志
    with TestClient(app) as client:
        audit_resp = client.get("/admin/audit-logs", headers=_admin_headers())

    assert audit_resp.status_code == 200
    audit_logs = audit_resp.json()["items"]

    # 查找批量操作的审计日志
    batch_logs = [log for log in audit_logs if "batch" in log.get("action", "").lower()]
    assert len(batch_logs) > 0

    # 验证日志内容
    batch_log = batch_logs[0]
    assert "enable" in batch_log["action"].lower()
    assert batch_log.get("resource_type") == "client"
