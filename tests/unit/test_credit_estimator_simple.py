from __future__ import annotations

import pytest

from app.core.credit_estimator import estimate_credit_cost, normalize_endpoint

pytestmark = pytest.mark.unit


def test_estimate_credit_cost_returns_base_cost_for_scrape():
    cost = estimate_credit_cost("/v1/scrape")
    assert cost == 1


def test_estimate_credit_cost_returns_base_cost_for_crawl():
    cost = estimate_credit_cost("/v1/crawl")
    assert cost == 5


def test_estimate_credit_cost_multiplies_by_pages_for_crawl():
    cost = estimate_credit_cost("/v1/crawl", {"data": {"total": 10}})
    assert cost == 10


def test_estimate_credit_cost_uses_base_cost_when_pages_less_than_base():
    cost = estimate_credit_cost("/v1/crawl", {"data": {"total": 2}})
    assert cost == 5  # max(5, 2) = 5


def test_estimate_credit_cost_handles_invalid_pages_count():
    cost = estimate_credit_cost("/v1/crawl", {"data": {"total": "invalid"}})
    assert cost == 5


def test_estimate_credit_cost_multiplies_by_count_for_batch():
    cost = estimate_credit_cost("/v2/batch/scrape", {"data": {"count": 5}})
    assert cost == 5  # 1 * 5


def test_estimate_credit_cost_handles_invalid_batch_count():
    cost = estimate_credit_cost("/v2/batch/scrape", {"data": {"count": "invalid"}})
    assert cost == 1


def test_estimate_credit_cost_returns_default_for_unknown_endpoint():
    cost = estimate_credit_cost("/v1/unknown")
    assert cost == 1


def test_estimate_credit_cost_handles_none_response_data():
    cost = estimate_credit_cost("/v1/scrape", None)
    assert cost == 1


def test_estimate_credit_cost_handles_empty_response_data():
    cost = estimate_credit_cost("/v1/crawl", {})
    assert cost == 5


def test_normalize_endpoint_removes_query_parameters():
    result = normalize_endpoint("/v1/scrape?url=https://example.com")
    assert result == "/v1/scrape"


def test_normalize_endpoint_removes_crawl_id():
    result = normalize_endpoint("/v1/crawl/abc123")
    assert result == "/v1/crawl"


def test_normalize_endpoint_removes_batch_scrape_id():
    result = normalize_endpoint("/v2/batch/scrape/xyz789")
    assert result == "/v2/batch/scrape"


def test_normalize_endpoint_handles_empty_path():
    result = normalize_endpoint("")
    assert result == ""


def test_normalize_endpoint_handles_root_path():
    result = normalize_endpoint("/")
    assert result == "/"


def test_normalize_endpoint_preserves_simple_paths():
    result = normalize_endpoint("/v1/scrape")
    assert result == "/v1/scrape"


def test_normalize_endpoint_handles_v2_crawl_with_id():
    result = normalize_endpoint("/v2/crawl/abc123")
    assert result == "/v2/crawl"


def test_normalize_endpoint_handles_path_with_trailing_slash():
    result = normalize_endpoint("/v1/crawl/abc123/")
    assert result == "/v1/crawl"
