from __future__ import annotations

import textwrap

import pytest
from pydantic import ValidationError

from app.config import load_config


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


def test_firecrawl_base_url_must_include_v1(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            firecrawl:
              base_url: "https://api.firecrawl.dev"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FCAM_CONFIG", str(cfg))

    with pytest.raises(ValidationError):
        load_config()


def test_env_override_type_coercion(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("security: {}\n", encoding="utf-8")
    monkeypatch.setenv("FCAM_CONFIG", str(cfg))
    monkeypatch.setenv("FCAM_SECURITY__REQUEST_LIMITS__MAX_BODY_BYTES", "10")

    config, _ = load_config()
    assert config.security.request_limits.max_body_bytes == 10

