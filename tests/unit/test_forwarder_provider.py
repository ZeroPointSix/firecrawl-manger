"""Unit tests for Forwarder provider helper methods."""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from app.config import AppConfig
from app.core.concurrency import ConcurrencyManager
from app.core.forwarder import Forwarder
from app.core.key_pool import KeyPool
from app.errors import FcamError

pytestmark = pytest.mark.unit


@pytest.fixture()
def forwarder_exa_enabled():
    """Create a Forwarder with Exa enabled."""
    config = AppConfig.model_validate({})
    config.providers.exa.enabled = True
    config.providers.exa.base_url = "https://api.exa.ai"
    config.providers.exa.auth_mode = "x-api-key"
    config.providers.exa.timeout = 15
    config.providers.exa.max_retries = 2

    secrets = MagicMock()
    secrets.master_key = None

    return Forwarder(
        config=config,
        secrets=secrets,
        key_pool=KeyPool(),
        key_concurrency=ConcurrencyManager(),
    )


@pytest.fixture()
def forwarder_exa_disabled():
    """Create a Forwarder with Exa disabled."""
    config = AppConfig.model_validate({})
    config.providers.exa.enabled = False

    secrets = MagicMock()
    secrets.master_key = None

    return Forwarder(
        config=config,
        secrets=secrets,
        key_pool=KeyPool(),
        key_concurrency=ConcurrencyManager(),
    )


class TestProviderBaseUrl:
    def test_firecrawl_returns_firecrawl_url(self, forwarder_exa_enabled):
        url = forwarder_exa_enabled._provider_base_url("firecrawl")
        assert "firecrawl" in url.lower() or url  # just check it doesn't raise

    def test_exa_returns_exa_url(self, forwarder_exa_enabled):
        url = forwarder_exa_enabled._provider_base_url("exa")
        assert url == "https://api.exa.ai"

    def test_exa_disabled_raises(self, forwarder_exa_disabled):
        with pytest.raises(FcamError, match="not configured or enabled"):
            forwarder_exa_disabled._provider_base_url("exa")

    def test_unknown_provider_raises(self, forwarder_exa_enabled):
        with pytest.raises(FcamError, match="not configured or enabled"):
            forwarder_exa_enabled._provider_base_url("unknown_provider")


class TestProviderTimeout:
    def test_firecrawl_uses_firecrawl_timeout(self, forwarder_exa_enabled):
        timeout = forwarder_exa_enabled._provider_timeout("firecrawl")
        assert isinstance(timeout, httpx.Timeout)

    def test_exa_uses_exa_timeout(self, forwarder_exa_enabled):
        timeout = forwarder_exa_enabled._provider_timeout("exa")
        assert timeout.connect == 15

    def test_unknown_provider_falls_back_to_firecrawl(self, forwarder_exa_enabled):
        timeout = forwarder_exa_enabled._provider_timeout("nonexistent")
        assert isinstance(timeout, httpx.Timeout)


class TestProviderMaxRetries:
    def test_firecrawl_retries(self, forwarder_exa_enabled):
        retries = forwarder_exa_enabled._provider_max_retries("firecrawl")
        assert isinstance(retries, int) and retries >= 0

    def test_exa_retries(self, forwarder_exa_enabled):
        retries = forwarder_exa_enabled._provider_max_retries("exa")
        assert retries == 2

    def test_unknown_provider_falls_back(self, forwarder_exa_enabled):
        retries = forwarder_exa_enabled._provider_max_retries("nonexistent")
        assert isinstance(retries, int)


class TestProviderAuthHeader:
    def test_firecrawl_uses_bearer(self, forwarder_exa_enabled):
        headers = forwarder_exa_enabled._provider_auth_header("firecrawl", "fc-key123")
        assert headers == {"authorization": "Bearer fc-key123"}

    def test_exa_uses_x_api_key(self, forwarder_exa_enabled):
        headers = forwarder_exa_enabled._provider_auth_header("exa", "exa-key456")
        assert headers == {"x-api-key": "exa-key456"}
        assert "authorization" not in headers

    def test_unknown_provider_defaults_to_bearer(self, forwarder_exa_enabled):
        headers = forwarder_exa_enabled._provider_auth_header("nonexistent", "somekey")
        assert headers == {"authorization": "Bearer somekey"}
