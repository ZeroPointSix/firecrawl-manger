from __future__ import annotations

import pytest

from app.core.credit_estimator import estimate_request_cost

pytestmark = pytest.mark.unit


def test_estimate_scrape_returns_base_cost():
    cost = estimate_request_cost(method="POST", path="/v1/scrape", payload={})
    assert cost == 1


def test_estimate_scrape_with_formats_adds_cost():
    cost = estimate_request_cost(
        method="POST",
        path="/v1/scrape",
        payload={"formats": ["markdown", "html", "screenshot"]},
    )
    assert cost == 4  # 1 (base) + 1 (markdown) + 1 (html) + 1 (screenshot)


def test_estimate_scrape_with_actions_adds_cost():
    cost = estimate_request_cost(
        method="POST",
        path="/v1/scrape",
        payload={"actions": [{"type": "click"}, {"type": "wait"}]},
    )
    assert cost == 3  # 1 (base) + 2 (actions)


def test_estimate_crawl_returns_base_cost():
    cost = estimate_request_cost(method="POST", path="/v1/crawl", payload={})
    assert cost == 5


def test_estimate_crawl_with_limit_multiplies_cost():
    cost = estimate_request_cost(method="POST", path="/v1/crawl", payload={"limit": 10})
    assert cost == 50  # 5 * 10


def test_estimate_map_returns_base_cost():
    cost = estimate_request_cost(method="POST", path="/v1/map", payload={})
    assert cost == 1


def test_estimate_map_with_search_adds_cost():
    cost = estimate_request_cost(method="POST", path="/v1/map", payload={"search": "test"})
    assert cost == 2  # 1 (base) + 1 (search)


def test_estimate_batch_scrape_multiplies_by_url_count():
    cost = estimate_request_cost(
        method="POST",
        path="/v1/batch/scrape",
        payload={"urls": ["url1", "url2", "url3"]},
    )
    assert cost == 3  # 1 * 3


def test_estimate_extract_returns_base_cost():
    cost = estimate_request_cost(method="POST", path="/v1/extract", payload={})
    assert cost == 3


def test_estimate_extract_with_multiple_urls_multiplies_cost():
    cost = estimate_request_cost(
        method="POST",
        path="/v1/extract",
        payload={"urls": ["url1", "url2"]},
    )
    assert cost == 6  # 3 * 2


def test_estimate_v2_scrape_returns_base_cost():
    cost = estimate_request_cost(method="POST", path="/v2/scrape", payload={})
    assert cost == 1


def test_estimate_v2_crawl_returns_base_cost():
    cost = estimate_request_cost(method="POST", path="/v2/crawl", payload={})
    assert cost == 5


def test_estimate_unknown_endpoint_returns_default_cost():
    cost = estimate_request_cost(method="POST", path="/v1/unknown", payload={})
    assert cost == 1


def test_estimate_get_request_returns_zero():
    cost = estimate_request_cost(method="GET", path="/v1/scrape", payload={})
    assert cost == 0


def test_estimate_handles_none_payload():
    cost = estimate_request_cost(method="POST", path="/v1/scrape", payload=None)
    assert cost == 1


def test_estimate_handles_empty_formats_list():
    cost = estimate_request_cost(method="POST", path="/v1/scrape", payload={"formats": []})
    assert cost == 1


def test_estimate_handles_empty_actions_list():
    cost = estimate_request_cost(method="POST", path="/v1/scrape", payload={"actions": []})
    assert cost == 1


def test_estimate_crawl_with_zero_limit_returns_base_cost():
    cost = estimate_request_cost(method="POST", path="/v1/crawl", payload={"limit": 0})
    assert cost == 5


def test_estimate_crawl_with_negative_limit_returns_base_cost():
    cost = estimate_request_cost(method="POST", path="/v1/crawl", payload={"limit": -5})
    assert cost == 5


def test_estimate_batch_scrape_with_empty_urls_returns_base_cost():
    cost = estimate_request_cost(method="POST", path="/v1/batch/scrape", payload={"urls": []})
    assert cost == 1


def test_estimate_extract_with_empty_urls_returns_base_cost():
    cost = estimate_request_cost(method="POST", path="/v1/extract", payload={"urls": []})
    assert cost == 3
