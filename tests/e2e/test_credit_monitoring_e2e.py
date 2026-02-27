"""
额度监控 E2E 测试

测试完整的业务流程：创建 Client/Key → 刷新额度 → 查询额度 → 模拟消费 → 验证本地计算
"""
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import AppConfig
from app.core.security import derive_master_key_bytes, encrypt_api_key
from app.db.models import ApiKey, Base, Client, CreditSnapshot

pytestmark = pytest.mark.e2e

if not os.getenv("FCAM_E2E"):
    pytest.skip("需要设置环境变量 FCAM_E2E=1 才会运行额度监控 E2E 测试", allow_module_level=True)


@pytest.fixture
def e2e_db():
    """E2E 测试数据库"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


@pytest.fixture
def e2e_config():
    """E2E 测试配置"""
    config = AppConfig()
    config.firecrawl.base_url = "https://api.firecrawl.dev"
    config.credit_monitoring.smart_refresh.enabled = True
    config.credit_monitoring.smart_refresh.high_usage_interval = 15
    config.credit_monitoring.smart_refresh.medium_usage_interval = 30
    config.credit_monitoring.smart_refresh.normal_usage_interval = 60
    config.credit_monitoring.smart_refresh.low_usage_interval = 120
    return config


@pytest.fixture
def e2e_master_key():
    """E2E 测试主密钥"""
    return derive_master_key_bytes("e2e_master_key")


@pytest.fixture
def e2e_admin_token():
    """E2E Admin Token"""
    return "e2e_admin_token_12345"


class TestCreditMonitoringE2E:
    """额度监控 E2E 测试"""

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient.get")
    async def test_complete_credit_monitoring_flow(
        self, mock_get, e2e_db, e2e_master_key, e2e_config, e2e_admin_token
    ):
        """
        TC-E2E-001: 完整的额度监控流程

        流程：
        1. 创建 Client 和 Key
        2. 首次刷新额度（从 Firecrawl 获取）
        3. 查询 Key 额度
        4. 模拟请求消费（本地计算）
        5. 再次查询额度，验证本地计算正确
        6. 查询额度历史
        """
        # Step 1: 创建 Client 和 Key
        client = Client(name="e2e-test-client", token_hash="e2e_hash", is_active=True)
        e2e_db.add(client)
        e2e_db.commit()

        plaintext_key = "fc-e2e-test-key-12345678"
        ciphertext = encrypt_api_key(e2e_master_key, plaintext_key)

        key = ApiKey(
            client_id=client.id,
            api_key_ciphertext=ciphertext,
            api_key_hash="e2e_key_hash",
            api_key_last4="5678",
            name="e2e-test-key",
            is_active=True,
            status="active",
        )
        e2e_db.add(key)
        e2e_db.commit()
        e2e_db.refresh(key)

        # Step 2: Mock crawl API 响应
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "data": {
                "remainingCredits": 10000,
                "planCredits": 10000,
                "billingPeriodStart": "2026-02-01T00:00:00Z",
                "billingPeriodEnd": "2026-03-01T00:00:00Z",
            }
        }
        mock_get.return_value = mock_response

        # 首次刷新额度
        from app.core.credit_fetcher import fetch_credit_from_firecrawl

        snapshot1 = await fetch_credit_from_firecrawl(
            db=e2e_db,
            key=key,
            master_key=e2e_master_key,
            config=e2e_config,
            request_id="e2e-001",
        )

        # 验证首次刷新
        assert snapshot1.fetch_success is True
        assert snapshot1.remaining_credits == 10000
        assert snapshot1.plan_credits == 10000

        # 验证 Key 缓存已更新
        e2e_db.refresh(key)
        assert key.cached_remaining_credits == 10000
        assert key.cached_plan_credits == 10000
        assert key.last_credit_check_at is not None

        # Step 3: 查询 Key 额度
        from app.core.credit_aggregator import get_key_credits

        credits = get_key_credits(e2e_db, key.id)
        assert credits["api_key_id"] == key.id
        assert credits["cached_credits"]["remaining_credits"] == 10000
        assert credits["cached_credits"]["plan_credits"] == 10000

        # Step 4: 模拟请求消费（本地计算）
        from app.core.credit_estimator import estimate_credit_cost

        # 模拟 3 次 scrape 请求（每次 1 credit）
        cost1 = estimate_credit_cost("/v1/scrape")
        cost2 = estimate_credit_cost("/v2/scrape")
        cost3 = estimate_credit_cost("/v1/scrape")

        total_cost = cost1 + cost2 + cost3  # 3 credits

        # 更新本地缓存
        key.cached_remaining_credits -= total_cost
        e2e_db.commit()

        # Step 5: 再次查询额度，验证本地计算
        e2e_db.refresh(key)
        assert key.cached_remaining_credits == 9997  # 10000 - 3

        credits_after = get_key_credits(e2e_db, key.id)
        assert credits_after["cached_credits"]["remaining_credits"] == 9997

        # Step 6: 查询额度历史
        snapshots = e2e_db.query(CreditSnapshot).filter(
            CreditSnapshot.api_key_id == key.id
        ).order_by(CreditSnapshot.snapshot_at.desc()).all()

        assert len(snapshots) == 1  # 只有首次刷新的快照
        assert snapshots[0].remaining_credits == 10000

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient.get")
    async def test_smart_refresh_strategy_e2e(
        self, mock_get, e2e_db, e2e_master_key, e2e_config
    ):
        """
        TC-E2E-002: 智能刷新策略 E2E 测试

        测试不同使用率下的刷新间隔调整
        """
        # 创建 Client 和 Key
        client = Client(name="refresh-test-client", token_hash="refresh_hash", is_active=True)
        e2e_db.add(client)
        e2e_db.commit()

        plaintext_key = "fc-refresh-test-key"
        ciphertext = encrypt_api_key(e2e_master_key, plaintext_key)

        key = ApiKey(
            client_id=client.id,
            api_key_ciphertext=ciphertext,
            api_key_hash="refresh_key_hash",
            api_key_last4="tkey",
            name="refresh-test-key",
            is_active=True,
            status="active",
        )
        e2e_db.add(key)
        e2e_db.commit()
        e2e_db.refresh(key)

        # Mock Firecrawl API 响应（高使用率：剩余 5%）
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "data": {
                "remainingCredits": 500,  # 5%
                "planCredits": 10000,
            }
        }
        mock_get.return_value = mock_response

        # 刷新额度
        from app.core.credit_fetcher import fetch_credit_from_firecrawl

        await fetch_credit_from_firecrawl(
            db=e2e_db,
            key=key,
            master_key=e2e_master_key,
            config=e2e_config,
            request_id="e2e-002",
        )

        # 验证刷新间隔（高使用率应该是 15 分钟）
        from app.core.credit_refresh import calculate_next_refresh_time

        e2e_db.refresh(key)
        next_refresh = calculate_next_refresh_time(key, e2e_config)
        expected = datetime.now(timezone.utc) + timedelta(minutes=15)

        # 允许 1 分钟误差
        assert abs((next_refresh - expected).total_seconds()) < 60

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient.get")
    async def test_client_aggregation_e2e(
        self, mock_get, e2e_db, e2e_master_key, e2e_config
    ):
        """
        TC-E2E-003: Client 聚合 E2E 测试

        测试多个 Key 的聚合计算
        """
        # 创建 Client
        client = Client(name="agg-test-client", token_hash="agg_hash", is_active=True)
        e2e_db.add(client)
        e2e_db.commit()

        # 创建 3 个 Key
        keys_data = [
            ("key-1", 8500, 10000),
            ("key-2", 9000, 10000),
            ("key-3", 7500, 10000),
        ]

        for name, remaining, plan in keys_data:
            ciphertext = encrypt_api_key(e2e_master_key, f"fc-{name}")
            key = ApiKey(
                client_id=client.id,
                api_key_ciphertext=ciphertext,
                api_key_hash=f"hash_{name}",
                api_key_last4=name[-4:],
                name=name,
                cached_remaining_credits=remaining,
                cached_plan_credits=plan,
                is_active=True,
                status="active",
            )
            e2e_db.add(key)
        e2e_db.commit()

        # 聚合 Client 额度
        from app.core.credit_aggregator import aggregate_client_credits

        result = aggregate_client_credits(e2e_db, client.id)

        # 验证聚合结果
        assert result["client_id"] == client.id
        assert result["total_remaining_credits"] == 25000  # 8500 + 9000 + 7500
        assert result["total_plan_credits"] == 30000  # 10000 * 3
        assert len(result["keys"]) == 3

        # 验证使用率计算
        expected_usage = (30000 - 25000) / 30000 * 100  # 16.67%
        assert abs(result["usage_percentage"] - expected_usage) < 0.01

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient.get")
    async def test_error_handling_e2e(
        self, mock_get, e2e_db, e2e_master_key, e2e_config
    ):
        """
        TC-E2E-004: 错误处理 E2E 测试

        测试 API Key 失效时的处理流程
        """
        # 创建 Client 和 Key
        client = Client(name="error-test-client", token_hash="error_hash", is_active=True)
        e2e_db.add(client)
        e2e_db.commit()

        plaintext_key = "fc-error-test-key"
        ciphertext = encrypt_api_key(e2e_master_key, plaintext_key)

        key = ApiKey(
            client_id=client.id,
            api_key_ciphertext=ciphertext,
            api_key_hash="error_key_hash",
            api_key_last4="tkey",
            name="error-test-key",
            is_active=True,
            status="active",
        )
        e2e_db.add(key)
        e2e_db.commit()
        e2e_db.refresh(key)

        # Mock 401 响应（Key 失效）
        mock_response = AsyncMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"
        mock_get.return_value = mock_response

        # 尝试刷新额度
        from app.core.credit_fetcher import fetch_credit_from_firecrawl
        from app.errors import FcamError

        with pytest.raises(FcamError) as exc_info:
            await fetch_credit_from_firecrawl(
                db=e2e_db,
                key=key,
                master_key=e2e_master_key,
                config=e2e_config,
                request_id="e2e-004",
            )

        # 验证异常
        assert exc_info.value.code == "INVALID_API_KEY"
        assert exc_info.value.status_code == 401

        # 验证 Key 状态被更新为 failed
        e2e_db.refresh(key)
        assert key.status == "failed"

        # 验证失败快照被创建
        snapshot = e2e_db.query(CreditSnapshot).filter(
            CreditSnapshot.api_key_id == key.id
        ).first()
        assert snapshot is not None
        assert snapshot.fetch_success is False
        assert "401" in snapshot.error_message

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient.get")
    async def test_batch_refresh_e2e(
        self, mock_get, e2e_db, e2e_master_key, e2e_config
    ):
        """
        TC-E2E-005: 批量刷新 E2E 测试

        测试批量刷新所有 Key 的流程
        """
        # 创建 Client
        client = Client(name="batch-test-client", token_hash="batch_hash", is_active=True)
        e2e_db.add(client)
        e2e_db.commit()

        # 创建 5 个 Key
        for i in range(5):
            ciphertext = encrypt_api_key(e2e_master_key, f"fc-batch-key-{i}")
            key = ApiKey(
                client_id=client.id,
                api_key_ciphertext=ciphertext,
                api_key_hash=f"batch_hash_{i}",
                api_key_last4=f"000{i}",
                name=f"batch-key-{i}",
                is_active=True,
                status="active",
            )
            e2e_db.add(key)
        e2e_db.commit()

        # Mock Firecrawl API 响应
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "data": {
                "remainingCredits": 9000,
                "planCredits": 10000,
            }
        }
        mock_get.return_value = mock_response

        # 批量刷新
        from app.core.credit_fetcher import fetch_credit_from_firecrawl

        keys = e2e_db.query(ApiKey).filter(ApiKey.client_id == client.id).all()

        success_count = 0
        for key in keys:
            try:
                await fetch_credit_from_firecrawl(
                    db=e2e_db,
                    key=key,
                    master_key=e2e_master_key,
                    config=e2e_config,
                    request_id=f"e2e-005-{key.id}",
                )
                success_count += 1
            except Exception:
                pass

        # 验证批量刷新结果
        assert success_count == 5

        # 验证所有 Key 的缓存都已更新
        for key in keys:
            e2e_db.refresh(key)
            assert key.cached_remaining_credits == 9000
            assert key.cached_plan_credits == 10000

        # 验证快照数量
        snapshots = e2e_db.query(CreditSnapshot).filter(
            CreditSnapshot.api_key_id.in_([k.id for k in keys])
        ).all()
        assert len(snapshots) == 5
