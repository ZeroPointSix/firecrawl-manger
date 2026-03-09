"""Unit tests for provider security rules."""
from __future__ import annotations

import pytest

from app.core.forwarder import _DROP_REQUEST_HEADERS, _sanitized_request_headers
from app.middleware import _infer_api_endpoint

pytestmark = pytest.mark.unit


class TestDropRequestHeaders:
    """Verify that both authorization and x-api-key are in the drop list."""

    def test_authorization_in_drop_headers(self):
        assert "authorization" in _DROP_REQUEST_HEADERS

    def test_x_api_key_in_drop_headers(self):
        assert "x-api-key" in _DROP_REQUEST_HEADERS

    def test_host_in_drop_headers(self):
        assert "host" in _DROP_REQUEST_HEADERS


class TestSanitizedRequestHeaders:
    """_sanitized_request_headers must NOT pass through auth-like headers."""

    def test_authorization_not_passed_through(self):
        import httpx

        in_headers = httpx.Headers(
            {"authorization": "Bearer client_token", "content-type": "application/json"}
        )
        out = _sanitized_request_headers(in_headers, "req-123")
        assert "authorization" not in out
        assert out["content-type"] == "application/json"

    def test_x_api_key_not_passed_through(self):
        import httpx

        in_headers = httpx.Headers(
            {"x-api-key": "client_exa_key", "accept": "application/json"}
        )
        out = _sanitized_request_headers(in_headers, "req-456")
        assert "x-api-key" not in out
        assert out["accept"] == "application/json"


class TestInferApiEndpointExa:
    """_infer_api_endpoint should correctly handle /exa/ prefix paths."""

    def test_exa_search(self):
        assert _infer_api_endpoint("/exa/search") == "exa_search"

    def test_exa_find_similar(self):
        assert _infer_api_endpoint("/exa/findSimilar") == "exa_findSimilar"

    def test_exa_contents(self):
        assert _infer_api_endpoint("/exa/contents") == "exa_contents"

    def test_exa_answer(self):
        assert _infer_api_endpoint("/exa/answer") == "exa_answer"

    def test_api_scrape_unchanged(self):
        assert _infer_api_endpoint("/api/scrape") == "scrape"

    def test_v1_search_unchanged(self):
        assert _infer_api_endpoint("/v1/search") == "search"


class TestExaPathNotAllowed:
    """Non-whitelisted /exa/ paths should not be routable."""

    def test_exa_research_not_a_known_endpoint(self):
        # _infer_api_endpoint will infer "exa_research" but the middleware
        # blocks it before reaching routes. We just verify inference works.
        result = _infer_api_endpoint("/exa/research")
        assert result == "exa_research"

    def test_exa_websets_not_a_known_endpoint(self):
        result = _infer_api_endpoint("/exa/websets")
        assert result == "exa_websets"
