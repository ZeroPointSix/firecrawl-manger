"""
后台刷新调度器单元测试

测试 credit_refresh_scheduler.py 中的调度器启动和停止逻辑
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI

from app.config import AppConfig
from app.core.credit_refresh_scheduler import (
    start_credit_refresh_scheduler,
    stop_credit_refresh_scheduler,
)


@pytest.fixture
def test_app():
    """测试 FastAPI 应用"""
    app = FastAPI()

    # 设置配置
    config = AppConfig()
    config.credit_monitoring.enabled = True
    config.credit_monitoring.refresh_check_interval_seconds = 1
    app.state.config = config

    # 设置 secrets
    secrets = MagicMock()
    secrets.master_key = "test_master_key_32_bytes_long___"
    app.state.secrets = secrets

    # 设置 db_session_factory
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
    app.state.db_session_factory = MagicMock(return_value=mock_db)

    return app


class TestStartCreditRefreshScheduler:
    """测试启动刷新调度器"""

    @pytest.mark.asyncio
    async def test_start_scheduler_success(self, test_app):
        """TC-CRS-001: 成功启动调度器"""
        with patch("app.core.credit_refresh_scheduler.credit_refresh_loop") as mock_loop:
            mock_loop.return_value = AsyncMock()

            await start_credit_refresh_scheduler(test_app)

            # 验证任务被创建
            assert hasattr(test_app.state, "credit_refresh_task")
            assert test_app.state.credit_refresh_task is not None
            assert hasattr(test_app.state, "credit_refresh_stop_event")

            # 清理
            await stop_credit_refresh_scheduler(test_app)

    @pytest.mark.asyncio
    async def test_start_scheduler_disabled(self):
        """TC-CRS-002: 监控功能禁用时不启动"""
        app = FastAPI()
        config = AppConfig()
        config.credit_monitoring.enabled = False
        app.state.config = config
        app.state.secrets = MagicMock(master_key="test_key")

        await start_credit_refresh_scheduler(app)

        # 验证任务未被创建
        assert not hasattr(app.state, "credit_refresh_task") or app.state.credit_refresh_task is None

    @pytest.mark.asyncio
    async def test_start_scheduler_no_master_key(self):
        """TC-CRS-003: 缺少 master_key 时不启动"""
        app = FastAPI()
        config = AppConfig()
        config.credit_monitoring.enabled = True
        app.state.config = config

        secrets = MagicMock()
        secrets.master_key = None
        app.state.secrets = secrets

        await start_credit_refresh_scheduler(app)

        # 验证任务未被创建
        assert not hasattr(app.state, "credit_refresh_task") or app.state.credit_refresh_task is None

    @pytest.mark.asyncio
    async def test_start_scheduler_already_running(self, test_app):
        """TC-CRS-004: 已经运行时不重复启动"""
        with patch("app.core.credit_refresh_scheduler.credit_refresh_loop") as mock_loop:
            mock_loop.return_value = AsyncMock()

            # 第一次启动
            await start_credit_refresh_scheduler(test_app)
            first_task = test_app.state.credit_refresh_task

            # 第二次启动
            await start_credit_refresh_scheduler(test_app)
            second_task = test_app.state.credit_refresh_task

            # 验证是同一个任务
            assert first_task is second_task

            # 清理
            await stop_credit_refresh_scheduler(test_app)

    @pytest.mark.asyncio
    async def test_start_scheduler_no_config(self):
        """测试缺少配置时不启动"""
        app = FastAPI()
        # 不设置 config

        await start_credit_refresh_scheduler(app)

        # 验证任务未被创建
        assert not hasattr(app.state, "credit_refresh_task") or app.state.credit_refresh_task is None


class TestStopCreditRefreshScheduler:
    """测试停止刷新调度器"""

    @pytest.mark.asyncio
    async def test_stop_scheduler_success(self, test_app):
        """TC-CRS-005: 成功停止调度器"""
        with patch("app.core.credit_refresh_scheduler.credit_refresh_loop") as mock_loop:
            mock_loop.return_value = AsyncMock()

            # 启动调度器
            await start_credit_refresh_scheduler(test_app)
            assert test_app.state.credit_refresh_task is not None

            # 停止调度器
            await stop_credit_refresh_scheduler(test_app)

            # 验证任务被清理
            assert test_app.state.credit_refresh_task is None
            assert test_app.state.credit_refresh_stop_event is None

    @pytest.mark.asyncio
    async def test_stop_scheduler_not_running(self, test_app):
        """TC-CRS-006: 未运行时停止不报错"""
        # 直接停止（没有启动过）
        await stop_credit_refresh_scheduler(test_app)

        # 应该不会抛出异常

    @pytest.mark.asyncio
    async def test_stop_scheduler_timeout(self, test_app):
        """TC-CRS-007: 停止超时时取消任务"""
        # 创建一个永不结束的任务
        async def never_ending_task():
            try:
                while True:
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                # 任务被取消时正常退出
                pass

        task = asyncio.create_task(never_ending_task())
        test_app.state.credit_refresh_task = task
        test_app.state.credit_refresh_stop_event = asyncio.Event()

        # 停止调度器（应该会超时并取消任务）
        try:
            await stop_credit_refresh_scheduler(test_app)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            # 预期会超时或取消
            pass

        # 验证任务被取消或完成
        assert task.cancelled() or task.done()
        assert test_app.state.credit_refresh_task is None

    @pytest.mark.asyncio
    async def test_stop_scheduler_with_stop_event(self, test_app):
        """测试通过 stop_event 优雅停止"""
        with patch("app.core.credit_refresh_scheduler.credit_refresh_loop") as mock_loop:
            # 创建一个会响应 stop_event 的任务
            async def responsive_task(stop_event, **kwargs):
                await stop_event.wait()

            mock_loop.side_effect = responsive_task

            # 启动调度器
            await start_credit_refresh_scheduler(test_app)

            # 等待任务启动
            await asyncio.sleep(0.1)

            # 停止调度器
            await stop_credit_refresh_scheduler(test_app)

            # 验证任务被清理
            assert test_app.state.credit_refresh_task is None
