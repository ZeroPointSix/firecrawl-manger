from __future__ import annotations

import textwrap

import pytest

from app.config import load_config

pytestmark = pytest.mark.unit


def _write_min_config(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(textwrap.dedent("database: {}\n"), encoding="utf-8")
    return cfg


def test_load_config_accepts_fcam_database_url_as_alias(tmp_path, monkeypatch):
    cfg = _write_min_config(tmp_path)
    monkeypatch.setenv("FCAM_CONFIG", str(cfg))
    monkeypatch.setenv("FCAM_DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")

    config, _ = load_config()
    assert config.database.url == "postgresql+psycopg://u:p@localhost:5432/db"


def test_load_config_accepts_fcam_database__url(tmp_path, monkeypatch):
    cfg = _write_min_config(tmp_path)
    monkeypatch.setenv("FCAM_CONFIG", str(cfg))
    monkeypatch.setenv("FCAM_DATABASE__URL", "postgresql+psycopg://u:p@localhost:5432/db")

    config, _ = load_config()
    assert config.database.url == "postgresql+psycopg://u:p@localhost:5432/db"


def test_load_config_rejects_mismatched_database_urls(tmp_path, monkeypatch):
    cfg = _write_min_config(tmp_path)
    monkeypatch.setenv("FCAM_CONFIG", str(cfg))
    monkeypatch.setenv("FCAM_DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db1")
    monkeypatch.setenv("FCAM_DATABASE__URL", "postgresql+psycopg://u:p@localhost:5432/db2")

    with pytest.raises(ValueError, match="FCAM_DATABASE_URL"):
        load_config()
