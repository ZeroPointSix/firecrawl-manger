"""
额度采集模块单元测试

测试 credit_fetcher.py 中的 Firecrawl API 调用和错误处理
"""
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import AppConfig
from app.core.credit_fetcher import fetch_credit_from_firecrawl
from app.core.security import derive_master_key_bytes, encrypt_api_key
from app.db.models import ApiKey, Base, Client, CreditSnapshot
from app.errors import FcamError


@pytest.fixture
def fetcher_db():
    """额度采集测试数据库"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


@pytest.fixture
def fetcher_config():
    """测试配置"""
    config = AppConfig()
    config.firecrawl.base_url = "https://api.firecrawl.dev"
    config.firecrawl.timeout = 30
    return config


@pytest.fixture
def fetcher_master_key():
    """测试主密钥"""
    return derive_master_key_bytes("test_master_key")


@pytest.fixture
def fetcher_test_key(fetcher_db, fetcher_master_key):
    """测试 API Key"""
    client = Client(name="fetcher-test-client", token_hash="test_hash", is_active=True)
    fetcher_db.add(client)
    fetcher_db.commit()

    plaintext = "fc-fetcher-test-key-12345678"
    ciphertext = encrypt_api_key(fetcher_master_key, plaintext)

    key = ApiKey(
        client_id=client.id,
        api_key_ciphertext=ciphertext,
        api_key_hash="fetcher_test_hash",
        api_key_last4="5678",
        name="fetcher-test-key",
        is_active=True,
        status="active",
    )
    fetcher_db.add(key)
    fetcher_db.commit()
    fetcher_db.refresh(key)
    return key


class TestFetchCreditFromFirecrawl:
    """测试从 Firecrawl 获取额度"""

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient.get")
    async def test_fetch_credit_success(
        self, mock_get, fetcher_db, fetcher_test_key, fetcher_master_key, fetcher_config
    ):
        """TC-CF-001: 成功获取额度"""
        # Mock Firecrawl API 响应
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "data": {
                "remainingCredits": 8500,
                "planCredits": 10000,
                "billingPeriodStart": "2026-02-01T00:00:00Z",
                "billingPeriodEnd": "2026-03-01T00:00:00Z",
            }
        }
        mock_get.return_value = mock_response

        # 调用函数
        snapshot = await fetch_credit_from_firecrawl(
            db=fetcher_db,
            key=fetcher_test_key,
            master_key=fetcher_master_key,
            config=fetcher_config,
            request_id="test-cf-001",
        )

        # 验证结果
        assert snapshot is not None
        assert snapshot.fetch_success is True
        assert snapshot.remaining_credits == 8500
        assert snapshot.plan_credits == 10000
        assert snapshot.api_key_id == fetcher_test_key.id
        assert snapshot.billing_period_start is not None
        assert snapshot.billing_period_end is not None

        # 验证数据库记录
        db_snapshot = fetcher_db.query(CreditSnapshot).filter(
            CreditSnapshot.id == snapshot.id
        ).one()
        assert db_snapshot.remaining_credits == 8500
        assert db_snapshot.fetch_success is True

        # 验证 API 调用参数
        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert "https://api.firecrawl.dev/v2/team/credit-usage" in str(call_args)

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient.get")
    async def test_fetch_credit_unauthorized(
        self, mock_get, fetcher_db, fetcher_test_key, fetcher_master_key, fetcher_config
    ):
        """TC-CF-002: API Key 无效 (401)"""
        # Mock 401 响应
        mock_response = AsyncMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"
        mock_get.return_value = mock_response

        # 调用函数，应该抛出异常
        with pytest.raises(FcamError) as exc_info:
            await fetch_credit_from_firecrawl(
                db=fetcher_db,
                key=fetcher_test_key,
                master_key=fetcher_master_key,
                config=fetcher_config,
                request_id="test-cf-002",
            )

        # 验证异常
        assert exc_info.value.code == "INVALID_API_KEY"
        assert exc_info.value.status_code == 401

        # 验证 Key 状态被更新
        fetcher_db.refresh(fetcher_test_key)
        assert fetcher_test_key.status == "failed"

        # 验证失败快照被创建
        snapshot = fetcher_db.query(CreditSnapshot).filter(
            CreditSnapshot.api_key_id == fetcher_test_key.id
        ).first()
        assert snapshot is not None
        assert snapshot.fetch_success is False
        assert "401" in snapshot.error_message

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient.get")
    async def test_fetch_credit_forbidden(
        self, mock_get, fetcher_db, fetcher_test_key, fetcher_master_key, fetcher_config
    ):
        """测试 403 Forbidden"""
        mock_response = AsyncMock()
        mock_response.status_code = 403
        mock_response.text = "Forbidden"
        mock_get.return_value = mock_response

        with pytest.raises(FcamError) as exc_info:
            await fetch_credit_from_firecrawl(
                db=fetcher_db,
                key=fetcher_test_key,
                master_key=fetcher_master_key,
                config=fetcher_config,
                request_id="test-cf-003",
            )

        assert exc_info.value.code == "INVALID_API_KEY"
        assert exc_info.value.status_code == 403

        # 验证 Key 状态
        fetcher_db.refresh(fetcher_test_key)
        assert fetcher_test_key.status == "failed"

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient.get")
    async def test_fetch_credit_rate_limited(
        self, mock_get, fetcher_db, fetcher_test_key, fetcher_master_key, fetcher_config
    ):
        """TC-CF-003: API 限流 (429)"""
        mock_response = AsyncMock()
        mock_response.status_code = 429
        mock_response.text = "Rate Limited"
        mock_get.return_value = mock_response

        with pytest.raises(FcamError) as exc_info:
            await fetch_credit_from_firecrawl(
                db=fetcher_db,
                key=fetcher_test_key,
                master_key=fetcher_master_key,
                config=fetcher_config,
                request_id="test-cf-004",
            )

        assert exc_info.value.code == "RATE_LIMITED"
        assert exc_info.value.status_code == 429

        # 验证失败快照
        snapshot = fetcher_db.query(CreditSnapshot).filter(
            CreditSnapshot.api_key_id == fetcher_test_key.id
        ).first()
        assert snapshot.fetch_success is False
        assert "429" in snapshot.error_message

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient.get")
    async def test_fetch_credit_timeout(
        self, mock_get, fetcher_db, fetcher_test_key, fetcher_master_key, fetcher_config
    ):
        """TC-CF-004: 请求超时"""
        # Mock 超时异常
        mock_get.side_effect = httpx.TimeoutException("Request timeout")

        with pytest.raises(FcamError) as exc_info:
            await fetch_credit_from_firecrawl(
                db=fetcher_db,
                key=fetcher_test_key,
                master_key=fetcher_master_key,
                config=fetcher_config,
                request_id="test-cf-005",
            )

        assert exc_info.value.code == "TIMEOUT"
        assert exc_info.value.status_code == 504

        # 验证失败快照
        snapshot = fetcher_db.query(CreditSnapshot).filter(
            CreditSnapshot.api_key_id == fetcher_test_key.id
        ).first()
        assert snapshot.fetch_success is False
        assert "timeout" in snapshot.error_message.lower()

    @pytest.mark.asyncio
    async def test_fetch_credit_decryption_failed(
        self, fetcher_db, fetcher_test_key, fetcher_config
    ):
        """TC-CF-005: 解密失败"""
        # 使用错误的 master_key
        wrong_master_key = derive_master_key_bytes("wrong_master_key")

        with pytest.raises(FcamError) as exc_info:
            await fetch_credit_from_firecrawl(
                db=fetcher_db,
                key=fetcher_test_key,
                master_key=wrong_master_key,
                config=fetcher_config,
                request_id="test-cf-006",
            )

        assert exc_info.value.code == "DECRYPTION_FAILED"
        assert exc_info.value.status_code == 500

        # 验证失败快照
        snapshot = fetcher_db.query(CreditSnapshot).filter(
            CreditSnapshot.api_key_id == fetcher_test_key.id
        ).first()
        assert snapshot.fetch_success is False
        assert "Decryption failed" in snapshot.error_message

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient.get")
    async def test_fetch_credit_server_error(
        self, mock_get, fetcher_db, fetcher_test_key, fetcher_master_key, fetcher_config
    ):
        """测试 5xx 服务器错误"""
        mock_response = AsyncMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_get.return_value = mock_response

        with pytest.raises(FcamError) as exc_info:
            await fetch_credit_from_firecrawl(
                db=fetcher_db,
                key=fetcher_test_key,
                master_key=fetcher_master_key,
                config=fetcher_config,
                request_id="test-cf-007",
            )

        assert exc_info.value.code == "UPSTREAM_ERROR"
        assert exc_info.value.status_code == 500

        # 验证失败快照
        snapshot = fetcher_db.query(CreditSnapshot).filter(
            CreditSnapshot.api_key_id == fetcher_test_key.id
        ).first()
        assert snapshot.fetch_success is False
        assert "500" in snapshot.error_message

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient.get")
    async def test_fetch_credit_invalid_response_format(
        self, mock_get, fetcher_db, fetcher_test_key, fetcher_master_key, fetcher_config
    ):
        """测试无效的响应格式"""
        # Mock 无效响应
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": False,
            "error": "Invalid request"
        }
        mock_get.return_value = mock_response

        with pytest.raises(FcamError) as exc_info:
            await fetch_credit_from_firecrawl(
                db=fetcher_db,
                key=fetcher_test_key,
                master_key=fetcher_master_key,
                config=fetcher_config,
                request_id="test-cf-008",
            )

        assert exc_info.value.code == "UPSTREAM_ERROR"

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient.get")
    async def test_fetch_credit_missing_data_fields(
        self, mock_get, fetcher_db, fetcher_test_key, fetcher_master_key, fetcher_config
    ):
        """测试响应缺少必要字段"""
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "data": {
                # 缺少 remainingCredits 和 planCredits
            }
        }
        mock_get.return_value = mock_response

        # 应该使用默认值 0
        snapshot = await fetch_credit_from_firecrawl(
            db=fetcher_db,
            key=fetcher_test_key,
            master_key=fetcher_master_key,
            config=fetcher_config,
            request_id="test-cf-009",
        )

        assert snapshot.remaining_credits == 0
        assert snapshot.plan_credits == 0
        assert snapshot.fetch_success is True

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient.get")
    async def test_fetch_credit_with_billing_period(
        self, mock_get, fetcher_db, fetcher_test_key, fetcher_master_key, fetcher_config
    ):
        """测试账期时间解析"""
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "data": {
                "remainingCredits": 8500,
                "planCredits": 10000,
                "billingPeriodStart": "2026-02-01T00:00:00Z",
                "billingPeriodEnd": "2026-03-01T00:00:00Z",
            }
        }
        mock_get.return_value = mock_response

        snapshot = await fetch_credit_from_firecrawl(
            db=fetcher_db,
            key=fetcher_test_key,
            master_key=fetcher_master_key,
            config=fetcher_config,
            request_id="test-cf-010",
        )

        assert snapshot.billing_period_start is not None
        assert snapshot.billing_period_end is not None
        assert snapshot.billing_period_start.year == 2026
        assert snapshot.billing_period_start.month == 2
        assert snapshot.billing_period_end.month == 3

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient.get")
    async def test_fetch_credit_request_headers(
        self, mock_get, fetcher_db, fetcher_test_key, fetcher_master_key, fetcher_config
    ):
        """测试请求头设置"""
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "data": {
                "remainingCredits": 8500,
                "planCredits": 10000,
            }
        }
        mock_get.return_value = mock_response

        request_id = "test-request-id-12345"
        await fetch_credit_from_firecrawl(
            db=fetcher_db,
            key=fetcher_test_key,
            master_key=fetcher_master_key,
            config=fetcher_config,
            request_id=request_id,
        )

        # 验证请求头
        call_kwargs = mock_get.call_args.kwargs
        headers = call_kwargs.get("headers")
        assert headers is not None
        assert "Authorization" in headers
        assert headers["Authorization"].startswith("Bearer ")
        assert "X-Request-Id" in headers or "x-request-id" in headers
