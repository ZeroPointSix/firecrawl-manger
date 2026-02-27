"""
额度估算模块单元测试

测试 credit_estimator.py 中的函数
"""
from app.core.credit_estimator import (
    CREDIT_COST_MAP,
    estimate_credit_cost,
    normalize_endpoint,
)


class TestEstimateCreditCost:
    """测试额度消耗估算"""

    def test_estimate_scrape_cost(self):
        """TC-CE-001: 估算 scrape 请求消耗"""
        assert estimate_credit_cost("/v1/scrape") == 1
        assert estimate_credit_cost("/v2/scrape") == 1
        assert estimate_credit_cost("/v1/scrape", {"data": {}}) == 1

    def test_estimate_crawl_cost_base(self):
        """TC-CE-002: 估算 crawl 请求消耗（基础）"""
        assert estimate_credit_cost("/v1/crawl") == 5
        assert estimate_credit_cost("/v2/crawl") == 5

    def test_estimate_crawl_cost_with_pages(self):
        """TC-CE-003: 估算 crawl 请求消耗（按页数）"""
        # 10 页
        response_data = {"data": {"total": 10}}
        assert estimate_credit_cost("/v1/crawl", response_data) == 10

        # 100 页
        response_data = {"data": {"total": 100}}
        assert estimate_credit_cost("/v1/crawl", response_data) == 100

        # 页数小于基础成本
        response_data = {"data": {"total": 2}}
        assert estimate_credit_cost("/v1/crawl", response_data) == 5

    def test_estimate_batch_cost(self):
        """TC-CE-004: 估算 batch 请求消耗"""
        response_data = {"data": {"count": 5}}
        assert estimate_credit_cost("/v2/batch/scrape", response_data) == 5

        response_data = {"data": {"count": 10}}
        assert estimate_credit_cost("/v2/batch/scrape", response_data) == 10

    def test_estimate_unknown_endpoint(self):
        """测试未知端点（使用默认值）"""
        assert estimate_credit_cost("/v1/unknown") == 1
        assert estimate_credit_cost("/v2/unknown") == 1

    def test_estimate_with_none_response(self):
        """测试 response_data 为 None"""
        assert estimate_credit_cost("/v1/scrape", None) == 1
        assert estimate_credit_cost("/v1/crawl", None) == 5


class TestNormalizeEndpoint:
    """测试端点路径规范化"""

    def test_normalize_with_query_params(self):
        """TC-CE-005: 移除查询参数"""
        assert normalize_endpoint("/v1/scrape?url=https://example.com") == "/v1/scrape"
        assert normalize_endpoint("/v2/map?search=test") == "/v2/map"

    def test_normalize_with_dynamic_path(self):
        """移除动态路径参数"""
        assert normalize_endpoint("/v1/crawl/abc123") == "/v1/crawl"
        assert normalize_endpoint("/v2/batch/scrape/xyz789") == "/v2/batch/scrape"

    def test_normalize_simple_path(self):
        """普通路径不变"""
        assert normalize_endpoint("/v1/scrape") == "/v1/scrape"
        assert normalize_endpoint("/v2/map") == "/v2/map"
        assert normalize_endpoint("/v1/search") == "/v1/search"

    def test_normalize_empty_path(self):
        """测试空路径"""
        assert normalize_endpoint("") == ""
        assert normalize_endpoint("/") == "/"


class TestCreditCostMap:
    """测试 CREDIT_COST_MAP 配置"""

    def test_cost_map_has_common_endpoints(self):
        """验证常用端点都在映射表中"""
        assert "/v1/scrape" in CREDIT_COST_MAP
        assert "/v1/crawl" in CREDIT_COST_MAP
        assert "/v2/scrape" in CREDIT_COST_MAP
        assert "/v2/crawl" in CREDIT_COST_MAP
        assert "/v2/map" in CREDIT_COST_MAP

    def test_cost_map_values_are_positive(self):
        """验证所有消耗值都是正数"""
        for endpoint, cost in CREDIT_COST_MAP.items():
            assert cost > 0, f"Endpoint {endpoint} has invalid cost: {cost}"


class TestUpdateLocalCredits:
    """测试本地额度更新"""

    def test_update_local_credits_decrease(self):
        """TC-CE-006: 减少本地额度"""
        from unittest.mock import MagicMock

        from app.core.credit_estimator import update_local_credits
        from app.db.models import ApiKey

        # 创建测试 Key
        key = ApiKey(id=1, cached_remaining_credits=1000)

        # 创建 mock db
        mock_db = MagicMock()

        # 更新额度（消耗 100）
        update_local_credits(
            db=mock_db,
            key=key,
            delta=-100,
            endpoint="/v1/scrape",
            request_id="test-001",
        )

        # 验证额度被更新
        assert key.cached_remaining_credits == 900
        assert mock_db.commit.called

    def test_update_local_credits_increase(self):
        """测试增加本地额度"""
        from unittest.mock import MagicMock

        from app.core.credit_estimator import update_local_credits
        from app.db.models import ApiKey

        key = ApiKey(id=1, cached_remaining_credits=500)
        mock_db = MagicMock()

        # 增加额度
        update_local_credits(
            db=mock_db,
            key=key,
            delta=200,
            endpoint="/v1/scrape",
            request_id="test-002",
        )

        assert key.cached_remaining_credits == 700
        assert mock_db.commit.called

    def test_update_local_credits_not_initialized(self):
        """TC-CE-007: 缓存未初始化时跳过"""
        from unittest.mock import MagicMock

        from app.core.credit_estimator import update_local_credits
        from app.db.models import ApiKey

        key = ApiKey(id=1, cached_remaining_credits=None)
        mock_db = MagicMock()

        # 尝试更新
        update_local_credits(
            db=mock_db,
            key=key,
            delta=-100,
            endpoint="/v1/scrape",
            request_id="test-003",
        )

        # 验证额度未被更新
        assert key.cached_remaining_credits is None
        assert not mock_db.commit.called

    def test_update_local_credits_lower_bound_zero(self):
        """TC-CE-008: 额度下限为 0"""
        from unittest.mock import MagicMock

        from app.core.credit_estimator import update_local_credits
        from app.db.models import ApiKey

        key = ApiKey(id=1, cached_remaining_credits=50)
        mock_db = MagicMock()

        # 消耗超过剩余额度
        update_local_credits(
            db=mock_db,
            key=key,
            delta=-100,
            endpoint="/v1/scrape",
            request_id="test-004",
        )

        # 验证额度不会变成负数
        assert key.cached_remaining_credits == 0
        assert mock_db.commit.called

    def test_update_local_credits_no_change(self):
        """测试额度无变化时不提交"""
        from unittest.mock import MagicMock

        from app.core.credit_estimator import update_local_credits
        from app.db.models import ApiKey

        key = ApiKey(id=1, cached_remaining_credits=0)
        mock_db = MagicMock()

        # 尝试减少已经为 0 的额度
        update_local_credits(
            db=mock_db,
            key=key,
            delta=-100,
            endpoint="/v1/scrape",
            request_id="test-005",
        )

        # 验证额度仍为 0，但不提交（因为没有变化）
        assert key.cached_remaining_credits == 0
        assert not mock_db.commit.called

    def test_update_local_credits_commit_failure(self):
        """测试提交失败时回滚"""
        from unittest.mock import MagicMock

        from app.core.credit_estimator import update_local_credits
        from app.db.models import ApiKey

        key = ApiKey(id=1, cached_remaining_credits=1000)

        # 创建会抛出异常的 mock db
        mock_db = MagicMock()
        mock_db.commit.side_effect = Exception("Database error")

        # 更新额度
        update_local_credits(
            db=mock_db,
            key=key,
            delta=-100,
            endpoint="/v1/scrape",
            request_id="test-006",
        )

        # 验证回滚被调用
        assert mock_db.rollback.called

    def test_update_local_credits_zero_delta(self):
        """测试 delta 为 0 的情况"""
        from unittest.mock import MagicMock

        from app.core.credit_estimator import update_local_credits
        from app.db.models import ApiKey

        key = ApiKey(id=1, cached_remaining_credits=1000)
        mock_db = MagicMock()

        # delta 为 0
        update_local_credits(
            db=mock_db,
            key=key,
            delta=0,
            endpoint="/v1/scrape",
            request_id="test-007",
        )

        # 验证额度未变化，不提交
        assert key.cached_remaining_credits == 1000
        assert not mock_db.commit.called
