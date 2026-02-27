"""
智能刷新模块单元测试

测试 credit_refresh.py 中的刷新策略函数
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.config import AppConfig
from app.core.credit_refresh import calculate_next_refresh_time
from app.db.models import ApiKey


@pytest.fixture
def test_config():
    """测试配置"""
    config = AppConfig()
    config.credit_monitoring.smart_refresh.enabled = True
    config.credit_monitoring.smart_refresh.high_usage_interval = 15
    config.credit_monitoring.smart_refresh.medium_usage_interval = 30
    config.credit_monitoring.smart_refresh.normal_usage_interval = 60
    config.credit_monitoring.smart_refresh.low_usage_interval = 120
    config.credit_monitoring.fixed_refresh.interval_minutes = 60
    return config


class TestCalculateNextRefreshTime:
    """测试刷新间隔计算"""

    def test_high_usage_interval(self, test_config):
        """TC-CR-001: 高使用率（剩余 < 10%）"""
        key = ApiKey(
            cached_remaining_credits=50,
            cached_plan_credits=1000,
        )

        next_refresh = calculate_next_refresh_time(key, test_config)
        expected = datetime.now(timezone.utc) + timedelta(minutes=15)

        # 允许 1 分钟误差
        assert abs((next_refresh - expected).total_seconds()) < 60

    def test_medium_usage_interval(self, test_config):
        """TC-CR-002: 中使用率（剩余 10%-30%）"""
        key = ApiKey(
            cached_remaining_credits=200,
            cached_plan_credits=1000,
        )

        next_refresh = calculate_next_refresh_time(key, test_config)
        expected = datetime.now(timezone.utc) + timedelta(minutes=30)

        assert abs((next_refresh - expected).total_seconds()) < 60

    def test_normal_usage_interval(self, test_config):
        """TC-CR-003: 正常使用率（剩余 30%-50%）"""
        key = ApiKey(
            cached_remaining_credits=400,
            cached_plan_credits=1000,
        )

        next_refresh = calculate_next_refresh_time(key, test_config)
        expected = datetime.now(timezone.utc) + timedelta(minutes=60)

        assert abs((next_refresh - expected).total_seconds()) < 60

    def test_low_usage_interval(self, test_config):
        """TC-CR-004: 低使用率（剩余 > 50%）"""
        key = ApiKey(
            cached_remaining_credits=800,
            cached_plan_credits=1000,
        )

        next_refresh = calculate_next_refresh_time(key, test_config)
        expected = datetime.now(timezone.utc) + timedelta(minutes=120)

        assert abs((next_refresh - expected).total_seconds()) < 60

    def test_depleted_credits(self, test_config):
        """TC-CR-005: 额度耗尽"""
        key = ApiKey(
            cached_remaining_credits=0,
            cached_plan_credits=1000,
        )

        next_refresh = calculate_next_refresh_time(key, test_config)

        # 应该等待到下个月 1 号
        now = datetime.now(timezone.utc)
        next_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0) + timedelta(days=32)
        expected = next_month.replace(day=1)

        assert next_refresh.day == 1
        assert next_refresh.month == expected.month

    def test_not_initialized(self, test_config):
        """TC-CR-006: 缓存未初始化"""
        key = ApiKey(
            cached_remaining_credits=None,
            cached_plan_credits=None,
        )

        next_refresh = calculate_next_refresh_time(key, test_config)

        # 应该立即刷新
        now = datetime.now(timezone.utc)
        assert abs((next_refresh - now).total_seconds()) < 5

    def test_fixed_refresh_strategy(self, test_config):
        """TC-CR-007: 固定刷新策略"""
        # 禁用智能刷新
        test_config.credit_monitoring.smart_refresh.enabled = False

        key = ApiKey(
            cached_remaining_credits=50,  # 高使用率
            cached_plan_credits=1000,
        )

        next_refresh = calculate_next_refresh_time(key, test_config)
        expected = datetime.now(timezone.utc) + timedelta(minutes=60)

        # 应该使用固定间隔，而不是智能间隔
        assert abs((next_refresh - expected).total_seconds()) < 60

    def test_zero_plan_credits(self, test_config):
        """测试计划额度为 0 的边界情况"""
        key = ApiKey(
            cached_remaining_credits=0,
            cached_plan_credits=0,
        )

        next_refresh = calculate_next_refresh_time(key, test_config)

        # 应该使用固定间隔
        expected = datetime.now(timezone.utc) + timedelta(minutes=60)
        assert abs((next_refresh - expected).total_seconds()) < 60

    def test_boundary_10_percent(self, test_config):
        """测试 10% 边界值"""
        # 正好 10%
        key = ApiKey(
            cached_remaining_credits=100,
            cached_plan_credits=1000,
        )

        next_refresh = calculate_next_refresh_time(key, test_config)
        expected = datetime.now(timezone.utc) + timedelta(minutes=30)  # 应该是中频

        assert abs((next_refresh - expected).total_seconds()) < 60

    def test_boundary_30_percent(self, test_config):
        """测试 30% 边界值"""
        # 正好 30%
        key = ApiKey(
            cached_remaining_credits=300,
            cached_plan_credits=1000,
        )

        next_refresh = calculate_next_refresh_time(key, test_config)
        expected = datetime.now(timezone.utc) + timedelta(minutes=60)  # 应该是正常频率

        assert abs((next_refresh - expected).total_seconds()) < 60

    def test_boundary_50_percent(self, test_config):
        """测试 50% 边界值"""
        # 正好 50%
        key = ApiKey(
            cached_remaining_credits=500,
            cached_plan_credits=1000,
        )

        next_refresh = calculate_next_refresh_time(key, test_config)
        expected = datetime.now(timezone.utc) + timedelta(minutes=120)  # 应该是低频

        assert abs((next_refresh - expected).total_seconds()) < 60


class TestCreditRefreshLoop:
    """测试后台刷新循环"""

    @pytest.mark.asyncio
    async def test_refresh_loop_basic(self):
        """测试基本的刷新循环"""
        from unittest.mock import AsyncMock, MagicMock, patch
        from app.core.credit_refresh import credit_refresh_loop
        import asyncio

        # 创建测试配置
        config = AppConfig()
        config.credit_monitoring.refresh_check_interval_seconds = 1

        # 创建 mock db_factory
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        db_factory = MagicMock(return_value=mock_db)

        # 创建停止事件
        stop_event = asyncio.Event()

        # 创建任务
        task = asyncio.create_task(
            credit_refresh_loop(
                db_factory=db_factory,
                master_key=b"test_master_key_32_bytes_long___",
                config=config,
                stop_event=stop_event,
            )
        )

        # 等待一小段时间
        await asyncio.sleep(0.5)

        # 停止循环
        stop_event.set()
        await asyncio.wait_for(task, timeout=2)

        # 验证 db_factory 被调用
        assert db_factory.called

    @pytest.mark.asyncio
    async def test_refresh_loop_handles_exception(self):
        """测试刷新循环处理异常"""
        from unittest.mock import MagicMock, patch
        from app.core.credit_refresh import credit_refresh_loop
        import asyncio

        config = AppConfig()
        config.credit_monitoring.refresh_check_interval_seconds = 1

        # 创建会抛出异常的 db_factory
        def failing_db_factory():
            raise RuntimeError("Database connection failed")

        stop_event = asyncio.Event()

        # 创建任务
        task = asyncio.create_task(
            credit_refresh_loop(
                db_factory=failing_db_factory,
                master_key=b"test_master_key_32_bytes_long___",
                config=config,
                stop_event=stop_event,
            )
        )

        # 等待一小段时间（循环应该继续运行，不会崩溃）
        await asyncio.sleep(0.5)

        # 停止循环
        stop_event.set()
        await asyncio.wait_for(task, timeout=2)

        # 如果能到这里，说明异常被正确处理了


class TestRefreshOnce:
    """测试单次刷新逻辑"""

    @pytest.mark.asyncio
    async def test_refresh_once_no_keys(self):
        """测试没有需要刷新的 Key"""
        from unittest.mock import MagicMock
        from app.core.credit_refresh import _refresh_once

        config = AppConfig()
        config.credit_monitoring.batch_size = 10
        config.credit_monitoring.batch_delay_seconds = 0

        # 创建空的数据库
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        db_factory = MagicMock(return_value=mock_db)

        # 调用函数
        await _refresh_once(
            db_factory=db_factory,
            master_key=b"test_master_key_32_bytes_long___",
            config=config,
        )

        # 验证查询被调用
        assert mock_db.query.called
        # 验证 db.close 被调用
        assert mock_db.close.called

    @pytest.mark.asyncio
    async def test_refresh_once_with_keys(self):
        """测试刷新多个 Key"""
        from unittest.mock import AsyncMock, MagicMock, patch
        from app.core.credit_refresh import _refresh_once
        from app.db.models import ApiKey

        config = AppConfig()
        config.credit_monitoring.batch_size = 2
        config.credit_monitoring.batch_delay_seconds = 0
        config.credit_monitoring.retry_delay_minutes = 5

        # 创建测试 Key
        keys = [
            ApiKey(id=1, is_active=True, status="active"),
            ApiKey(id=2, is_active=True, status="active"),
        ]

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = keys
        db_factory = MagicMock(return_value=mock_db)

        # Mock fetch_credit_from_firecrawl（在 _refresh_once 内部导入）
        with patch("app.core.credit_fetcher.fetch_credit_from_firecrawl") as mock_fetch:
            mock_fetch.return_value = AsyncMock()

            await _refresh_once(
                db_factory=db_factory,
                master_key=b"test_master_key_32_bytes_long___",
                config=config,
            )

            # 验证 fetch 被调用了 2 次
            assert mock_fetch.call_count == 2


class TestCleanupOldSnapshots:
    """测试快照清理"""

    @pytest.mark.asyncio
    async def test_cleanup_old_snapshots_disabled(self):
        """测试清理功能禁用"""
        from unittest.mock import MagicMock
        from app.core.credit_refresh import cleanup_old_snapshots

        config = AppConfig()
        config.credit_monitoring.retention_days = 0  # 禁用清理

        mock_db = MagicMock()

        await cleanup_old_snapshots(db=mock_db, config=config)

        # 验证没有执行删除操作
        assert not mock_db.query.called

    @pytest.mark.asyncio
    async def test_cleanup_old_snapshots_success(self):
        """测试成功清理旧快照"""
        from unittest.mock import MagicMock
        from app.core.credit_refresh import cleanup_old_snapshots

        config = AppConfig()
        config.credit_monitoring.retention_days = 30

        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_query.filter.return_value.delete.return_value = 5  # 删除了 5 条记录
        mock_db.query.return_value = mock_query

        await cleanup_old_snapshots(db=mock_db, config=config)

        # 验证删除操作被调用
        assert mock_query.filter.called
        assert mock_query.filter.return_value.delete.called
        # 验证提交被调用
        assert mock_db.commit.called
