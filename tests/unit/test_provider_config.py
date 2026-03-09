"""Unit tests for Provider configuration model."""
from __future__ import annotations

import textwrap

import pytest

from app.config import ProviderConfig, ProvidersConfig, load_config

pytestmark = pytest.mark.unit


def test_provider_config_defaults():
    """ProviderConfig should have sane defaults."""
    cfg = ProviderConfig(base_url="https://api.example.com")
    assert cfg.enabled is True
    assert cfg.auth_mode == "bearer"
    assert cfg.timeout == 30
    assert cfg.max_retries == 3
    assert cfg.route_prefix == ""
    assert cfg.allowed_paths == []


def test_exa_provider_defaults():
    """ProvidersConfig should have Exa defaults matching design doc."""
    providers = ProvidersConfig()
    assert providers.exa.enabled is False
    assert providers.exa.base_url == "https://api.exa.ai"
    assert providers.exa.auth_mode == "x-api-key"
    assert "search" in providers.exa.allowed_paths
    assert "findSimilar" in providers.exa.allowed_paths
    assert "contents" in providers.exa.allowed_paths
    assert "answer" in providers.exa.allowed_paths


def test_load_config_exa_disabled_by_default(tmp_path, monkeypatch):
    """When no providers section is specified, Exa should be disabled."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("security: {}\n", encoding="utf-8")
    monkeypatch.setenv("FCAM_CONFIG", str(cfg))

    config, _ = load_config()
    assert config.providers.exa.enabled is False


def test_load_config_exa_enabled_via_yaml(tmp_path, monkeypatch):
    """Exa can be enabled via config.yaml."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            providers:
              exa:
                enabled: true
                base_url: "https://custom-exa.example.com"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FCAM_CONFIG", str(cfg))

    config, _ = load_config()
    assert config.providers.exa.enabled is True
    assert config.providers.exa.base_url == "https://custom-exa.example.com"
    # auth_mode should still default to x-api-key
    assert config.providers.exa.auth_mode == "x-api-key"


def test_firecrawl_config_unaffected_by_providers(tmp_path, monkeypatch):
    """Adding providers config must not break existing firecrawl config."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            firecrawl:
              base_url: "https://api.firecrawl.dev"
            providers:
              exa:
                enabled: true
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FCAM_CONFIG", str(cfg))

    config, _ = load_config()
    assert config.firecrawl.base_url == "https://api.firecrawl.dev"
    assert config.providers.exa.enabled is True
