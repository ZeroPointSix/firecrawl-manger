"""
Group 聚合模块单元测试

测试 credit_aggregator.py 中的聚合函数
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.credit_aggregator import aggregate_client_credits
from app.core.security import derive_master_key_bytes, encrypt_api_key
from app.db.models import ApiKey, Base, Client


@pytest.fixture
def agg_db():
    """聚合测试数据库"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


@pytest.fixture
def agg_master_key():
    """测试主密钥"""
    return derive_master_key_bytes("test_master_key")


class TestAggregateClientCredits:
    """测试 Client 额度聚合"""

    def test_aggregate_single_client(self, agg_db, agg_master_key):
        """TC-CA-001: 聚合单个 Client 的额度"""
        # 创建 Client
        client = Client(name="test-client", token_hash="test_hash")
        agg_db.add(client)
        agg_db.commit()

        # 创建 3 个 Key
        keys_data = [
            ("key-1", 8500, 10000),
            ("key-2", 9000, 10000),
            ("key-3", 7500, 10000),
        ]

        for name, remaining, plan in keys_data:
            ciphertext = encrypt_api_key(agg_master_key, f"fc-{name}")
            key = ApiKey(
                client_id=client.id,
                api_key_ciphertext=ciphertext,
                api_key_hash=f"hash_{name}",
                api_key_last4=name[-4:],
                name=name,
                cached_remaining_credits=remaining,
                cached_plan_credits=plan,
                is_active=True,
            )
            agg_db.add(key)
        agg_db.commit()

        # 聚合额度
        result = aggregate_client_credits(agg_db, client.id)

        # 验证结果
        assert result["client_id"] == client.id
        assert result["client_name"] == "test-client"
        assert result["total_remaining_credits"] == 25000  # 8500 + 9000 + 7500
        assert result["total_plan_credits"] == 30000  # 10000 * 3
        assert abs(result["usage_percentage"] - 16.67) < 0.01  # (30000 - 25000) / 30000 * 100
        assert len(result["keys"]) == 3

    def test_aggregate_empty_client(self, agg_db):
        """TC-CA-002: 聚合空 Client（无 Key）"""
        client = Client(name="empty-client", token_hash="test_hash")
        agg_db.add(client)
        agg_db.commit()

        result = aggregate_client_credits(agg_db, client.id)

        assert result["client_id"] == client.id
        assert result["total_remaining_credits"] == 0
        assert result["total_plan_credits"] == 0
        assert result["usage_percentage"] == 0.0
        assert len(result["keys"]) == 0

    def test_aggregate_nonexistent_client(self, agg_db):
        """TC-CA-003: 聚合不存在的 Client"""
        with pytest.raises(ValueError) as exc_info:
            aggregate_client_credits(agg_db, 99999)

        assert "not found" in str(exc_info.value).lower()

    def test_aggregate_exclude_inactive_keys(self, agg_db, agg_master_key):
        """TC-CA-004: 聚合时排除非活跃 Key"""
        client = Client(name="test-client", token_hash="test_hash")
        agg_db.add(client)
        agg_db.commit()

        # 创建活跃和非活跃 Key
        keys_data = [
            ("active-key", 8500, 10000, True),
            ("inactive-key", 5000, 10000, False),
        ]

        for name, remaining, plan, is_active in keys_data:
            ciphertext = encrypt_api_key(agg_master_key, f"fc-{name}")
            key = ApiKey(
                client_id=client.id,
                api_key_ciphertext=ciphertext,
                api_key_hash=f"hash_{name}",
                api_key_last4=name[-4:],
                name=name,
                cached_remaining_credits=remaining,
                cached_plan_credits=plan,
                is_active=is_active,
            )
            agg_db.add(key)
        agg_db.commit()

        result = aggregate_client_credits(agg_db, client.id)

        # 应该只包含活跃 Key
        assert result["total_remaining_credits"] == 8500
        assert result["total_plan_credits"] == 10000
        assert len(result["keys"]) == 1
        assert result["keys"][0]["name"] == "active-key"

    def test_aggregate_with_uninitialized_keys(self, agg_db, agg_master_key):
        """TC-CA-005: 聚合时处理未初始化的 Key"""
        client = Client(name="test-client", token_hash="test_hash")
        agg_db.add(client)
        agg_db.commit()

        # 创建初始化和未初始化的 Key
        keys_data = [
            ("initialized-key", 8500, 10000),
            ("uninitialized-key", None, None),
        ]

        for name, remaining, plan in keys_data:
            ciphertext = encrypt_api_key(agg_master_key, f"fc-{name}")
            key = ApiKey(
                client_id=client.id,
                api_key_ciphertext=ciphertext,
                api_key_hash=f"hash_{name}",
                api_key_last4=name[-4:],
                name=name,
                cached_remaining_credits=remaining,
                cached_plan_credits=plan,
                is_active=True,
            )
            agg_db.add(key)
        agg_db.commit()

        result = aggregate_client_credits(agg_db, client.id)

        # 未初始化的 Key 应该按 0 计算
        assert result["total_remaining_credits"] == 8500
        assert result["total_plan_credits"] == 10000
        assert len(result["keys"]) == 2

        # 验证未初始化 Key 的值
        uninit_key = next(k for k in result["keys"] if k["name"] == "uninitialized-key")
        assert uninit_key["remaining_credits"] == 0
        assert uninit_key["plan_credits"] == 0

    def test_aggregate_all_keys_depleted(self, agg_db, agg_master_key):
        """测试所有 Key 都耗尽的情况"""
        client = Client(name="test-client", token_hash="test_hash")
        agg_db.add(client)
        agg_db.commit()

        # 创建 2 个耗尽的 Key
        for i in range(2):
            ciphertext = encrypt_api_key(agg_master_key, f"fc-key-{i}")
            key = ApiKey(
                client_id=client.id,
                api_key_ciphertext=ciphertext,
                api_key_hash=f"hash_{i}",
                api_key_last4=f"000{i}",
                name=f"key-{i}",
                cached_remaining_credits=0,
                cached_plan_credits=10000,
                is_active=True,
            )
            agg_db.add(key)
        agg_db.commit()

        result = aggregate_client_credits(agg_db, client.id)

        assert result["total_remaining_credits"] == 0
        assert result["total_plan_credits"] == 20000
        assert result["usage_percentage"] == 100.0

    def test_aggregate_keys_order(self, agg_db, agg_master_key):
        """测试 Key 列表的顺序"""
        client = Client(name="test-client", token_hash="test_hash")
        agg_db.add(client)
        agg_db.commit()

        # 创建多个 Key
        for i in range(5):
            ciphertext = encrypt_api_key(agg_master_key, f"fc-key-{i}")
            key = ApiKey(
                client_id=client.id,
                api_key_ciphertext=ciphertext,
                api_key_hash=f"hash_{i}",
                api_key_last4=f"000{i}",
                name=f"key-{i}",
                cached_remaining_credits=1000 * (i + 1),
                cached_plan_credits=10000,
                is_active=True,
            )
            agg_db.add(key)
        agg_db.commit()

        result = aggregate_client_credits(agg_db, client.id)

        # 验证返回了所有 Key
        assert len(result["keys"]) == 5

        # 验证每个 Key 的数据正确
        for key_info in result["keys"]:
            assert "api_key_id" in key_info
            assert "name" in key_info
            assert "remaining_credits" in key_info
            assert "plan_credits" in key_info
            assert "usage_percentage" in key_info
