from __future__ import annotations

import textwrap

import pytest

from app.config import load_config

pytestmark = pytest.mark.unit


def test_load_config_precedence_yaml_then_env(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            server:
              port: 1234
            security:
              admin:
                token_env: "MY_ADMIN_TOKEN"
              key_encryption:
                master_key_env: "MY_MASTER_KEY"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("FCAM_CONFIG", str(cfg))
    monkeypatch.setenv("FCAM_SERVER__PORT", "5678")
    monkeypatch.setenv("MY_ADMIN_TOKEN", "admin_secret")
    monkeypatch.setenv("MY_MASTER_KEY", "master_secret")

    config, secrets = load_config()
    assert config.server.port == 5678
    assert secrets.admin_token == "admin_secret"
    assert secrets.master_key == "master_secret"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://api.firecrawl.dev", "https://api.firecrawl.dev"),
        ("https://api.firecrawl.dev/", "https://api.firecrawl.dev"),
        ("https://api.firecrawl.dev/v1", "https://api.firecrawl.dev/v1"),
        ("https://api.firecrawl.dev/v2/", "https://api.firecrawl.dev/v2"),
    ],
)
def test_firecrawl_base_url_allows_root_and_versions_and_normalizes(tmp_path, monkeypatch, raw: str, expected: str):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            firecrawl:
              base_url: "{base_url}"
            """
        )
        .format(base_url=raw)
        .strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FCAM_CONFIG", str(cfg))

    config, _ = load_config()
    assert config.firecrawl.base_url == expected


def test_env_override_type_coercion(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("security: {}\n", encoding="utf-8")
    monkeypatch.setenv("FCAM_CONFIG", str(cfg))
    monkeypatch.setenv("FCAM_SECURITY__REQUEST_LIMITS__MAX_BODY_BYTES", "10")

    config, _ = load_config()
    assert config.security.request_limits.max_body_bytes == 10

