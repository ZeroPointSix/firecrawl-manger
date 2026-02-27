"""
E2E tests for Firecrawl API v2 missing endpoints compatibility.

These tests use real Firecrawl API to verify seamless switching.
They are skipped by default and only run when explicitly enabled.

To run these tests:
    export FCAM_E2E="1"
    export FCAM_E2E_ALLOW_UPSTREAM="1"
    export FCAM_E2E_FIRECRAWL_API_KEY="fc-xxx"
    pytest tests/e2e/test_e2e_firecrawl_v2_compatibility.py -v

WARNING: These tests will consume Firecrawl API credits!

Reference:
- PRD: docs/PRD/2026-02-25-firecrawl-v2-missing-endpoints.md
- TDD: docs/TDD/2026-02-25-firecrawl-v2-missing-endpoints-tdd.md
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

# Skip all tests if E2E is not explicitly enabled
pytestmark = pytest.mark.skipif(
    not os.getenv("FCAM_E2E_ALLOW_UPSTREAM"),
    reason="E2E test with real upstream API disabled. Set FCAM_E2E_ALLOW_UPSTREAM=1 to enable.",
)


class TestE2EScrapeEndpoint:
    """E2E tests for scrape endpoint with real Firecrawl API."""

    def test_e2e_scrape_with_real_api(self, client: TestClient, client_headers: dict[str, str]):
        """
        Test scrape endpoint with real Firecrawl API.

        Verifies:
        - Request is successfully forwarded
        - Response format matches official API
        - Markdown content is returned
        """
        response = client.post(
            "/v2/scrape",
            json={
                "url": "https://example.com",
                "formats": ["markdown"],
            },
            headers=client_headers,
        )

        # Verify response
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()

        # Verify response structure
        assert "success" in data, "Response should contain 'success' field"
        assert data["success"] is True, "Response should indicate success"
        assert "data" in data, "Response should contain 'data' field"

        # Verify markdown content
        assert "markdown" in data["data"], "Response should contain markdown content"
        assert isinstance(data["data"]["markdown"], str), "Markdown should be a string"
        assert len(data["data"]["markdown"]) > 0, "Markdown content should not be empty"

    def test_e2e_scrape_with_multiple_formats(self, client: TestClient, client_headers: dict[str, str]):
        """Test scrape endpoint with multiple formats."""
        response = client.post(
            "/v2/scrape",
            json={
                "url": "https://example.com",
                "formats": ["markdown", "html"],
            },
            headers=client_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

        # Verify both formats are returned
        assert "markdown" in data["data"]
        assert "html" in data["data"]


class TestE2ESearchEndpoint:
    """E2E tests for search endpoint with real Firecrawl API."""

    def test_e2e_search_with_real_api(self, client: TestClient, client_headers: dict[str, str]):
        """
        Test search endpoint with real Firecrawl API.

        Verifies:
        - Search query is processed
        - Results are returned
        - Response format matches official API
        """
        response = client.post(
            "/v2/search",
            json={
                "query": "firecrawl api documentation",
                "limit": 3,
            },
            headers=client_headers,
        )

        # Verify response
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()

        # Verify response structure
        assert "success" in data
        assert data["success"] is True
        assert "data" in data

        # Verify search results
        assert isinstance(data["data"], list), "Search results should be a list"
        assert len(data["data"]) > 0, "Search should return at least one result"


class TestE2EMapEndpoint:
    """E2E tests for map endpoint with real Firecrawl API."""

    def test_e2e_map_with_real_api(self, client: TestClient, client_headers: dict[str, str]):
        """
        Test map endpoint with real Firecrawl API.

        Verifies:
        - Website mapping works
        - URL list is returned
        - Response format matches official API
        """
        response = client.post(
            "/v2/map",
            json={
                "url": "https://example.com",
            },
            headers=client_headers,
        )

        # Verify response
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()

        # Verify response structure
        assert "success" in data
        assert data["success"] is True

        # Verify URL list (structure may vary based on API version)
        assert "links" in data or "data" in data, "Response should contain links or data"


class TestE2ETeamEndpoints:
    """E2E tests for team account management endpoints."""

    def test_e2e_team_credit_usage(self, client: TestClient, client_headers: dict[str, str]):
        """
        Test team/credit-usage endpoint with real Firecrawl API.

        Verifies:
        - Credit information is returned
        - Response format matches official API
        - All expected fields are present
        """
        response = client.get(
            "/v2/team/credit-usage",
            headers=client_headers,
        )

        # Verify response
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()

        # Verify response structure
        assert "success" in data
        assert data["success"] is True
        assert "data" in data

        # Verify credit fields
        assert "remainingCredits" in data["data"], "Response should contain remainingCredits"
        assert isinstance(data["data"]["remainingCredits"], (int, float)), "remainingCredits should be numeric"

        # Optional fields (may not always be present)
        if "planCredits" in data["data"]:
            assert isinstance(data["data"]["planCredits"], (int, float))

    def test_e2e_team_queue_status(self, client: TestClient, client_headers: dict[str, str]):
        """
        Test team/queue-status endpoint with real Firecrawl API.

        Verifies:
        - Queue metrics are returned
        - Response format matches official API
        """
        response = client.get(
            "/v2/team/queue-status",
            headers=client_headers,
        )

        # Verify response
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()

        # Verify response structure
        assert "success" in data
        assert data["success"] is True

        # Verify queue metrics (field names may vary)
        # Check for common queue-related fields
        has_queue_info = any(
            key in data
            for key in ["jobsInQueue", "activeJobsInQueue", "waitingJobsInQueue", "data"]
        )
        assert has_queue_info, "Response should contain queue information"


class TestE2ECompatibility:
    """E2E tests for overall API compatibility."""

    def test_e2e_error_handling_401(self, client: TestClient):
        """Test that 401 errors are correctly handled."""
        # Use invalid token
        response = client.post(
            "/v2/scrape",
            json={"url": "https://example.com"},
            headers={"Authorization": "Bearer invalid_token_xyz"},
        )

        # Should return 401 (either from our auth or upstream)
        assert response.status_code == 401

    def test_e2e_seamless_switching(self, client: TestClient, client_headers: dict[str, str]):
        """
        Test seamless switching by verifying multiple endpoints work correctly.

        This test simulates a user switching from direct Firecrawl API
        to our proxy service.
        """
        # Test scrape
        scrape_response = client.post(
            "/v2/scrape",
            json={"url": "https://example.com", "formats": ["markdown"]},
            headers=client_headers,
        )
        assert scrape_response.status_code == 200
        assert scrape_response.json()["success"] is True

        # Test credit usage
        credit_response = client.get(
            "/v2/team/credit-usage",
            headers=client_headers,
        )
        assert credit_response.status_code == 200
        assert credit_response.json()["success"] is True

        # Verify both responses have consistent format
        assert "data" in scrape_response.json()
        assert "data" in credit_response.json()


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def client_headers(test_client_token: str) -> dict[str, str]:
    """Return headers with valid client token for E2E testing."""
    return {
        "Authorization": f"Bearer {test_client_token}",
        "Content-Type": "application/json",
    }
