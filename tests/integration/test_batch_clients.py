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
    """测试 client_ids 为空时返回 400（RequestValidationError -> 400）"""
    app, _ = _make_app(tmp_path)

    with TestClient(app) as client:
        resp = client.patch(
            "/admin/clients/batch",
            headers=_admin_headers(),
            json={"client_ids": [], "action": "enable"},
        )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


# ============================================================================
# 2. 鉴权测试
# ============================================================================

@pytest.mark.parametrize(
    "headers",
    [
        None,
        {"Authorization": "Bearer invalid_token"},
    ],
)
def test_batch_clients_requires_admin_auth(tmp_path, headers):
    """测试需要 Admin Token（缺失/无效均返回 401）"""
    app, _ = _make_app(tmp_path)

    with TestClient(app) as client:
        resp = client.patch(
            "/admin/clients/batch",
            headers=headers,
            json={"client_ids": [1, 2, 3], "action": "enable"},
        )

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "ADMIN_UNAUTHORIZED"


# ============================================================================
# 3. 批量操作成功（enable/disable/delete）
# ========================================================================

@pytest.mark.parametrize(
    "action,initial_active,expected_active,expected_status",
    [
        ("enable", False, True, "active"),
        ("disable", True, False, "disabled"),
        ("delete", True, False, "deleted"),
    ],
)
def test_batch_clients_success(tmp_path, action, initial_active, expected_active, expected_status):
    """测试批量操作成功（所有 Client 都存在）"""
    app, _ = _make_app(tmp_path)

    client_ids = [_create_client(app, f"test_client_{i}", is_active=initial_active) for i in range(3)]

    with TestClient(app) as client:
        resp = client.patch(
            "/admin/clients/batch",
            headers=_admin_headers(),
            json={"client_ids": client_ids, "action": action},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success_count"] == 3
    assert data["failed_count"] == 0
    assert len(data["failed_items"]) == 0

    for cid in client_ids:
        db_client = _get_client(app, cid)
        assert db_client is not None
        assert db_client.is_active is expected_active
        assert db_client.status == expected_status


# ============================================================================
# 4. 部分失败（部分 Client 不存在）
# ============================================================================


def test_batch_enable_clients_partial_success(tmp_path):
    """测试批量启用部分成功（部分 Client 不存在）"""
    app, _ = _make_app(tmp_path)

    client_ids = [_create_client(app, f"test_client_{i}", is_active=False) for i in range(2)]
    client_ids.append(9999)  # 不存在的 ID

    with TestClient(app) as client:
        resp = client.patch(
            "/admin/clients/batch",
            headers=_admin_headers(),
            json={
                "client_ids": client_ids,
                "action": "enable",
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success_count"] == 2
    assert data["failed_count"] == 1
    assert len(data["failed_items"]) == 1
    assert data["failed_items"][0]["client_id"] == 9999
    assert "not found" in data["failed_items"][0]["error"].lower()

    for cid in client_ids[:2]:
        db_client = _get_client(app, cid)
        assert db_client is not None
        assert db_client.is_active is True


# ============================================================================
# 5. 事务测试
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
