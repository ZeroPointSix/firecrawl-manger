from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig

from app.config import AppConfig
from app.db.session import check_db_ready, create_engine_from_config


def test_alembic_upgrade_head_creates_required_tables(tmp_path, monkeypatch):
    db_path = (tmp_path / "alembic.db").as_posix()
    monkeypatch.setenv("FCAM_DATABASE_URL", f"sqlite:///{db_path}")

    repo_root = Path(__file__).resolve().parents[1]
    alembic_ini = (repo_root / "alembic.ini").as_posix()
    migrations_dir = (repo_root / "migrations").as_posix()

    cfg = AlembicConfig(alembic_ini)
    cfg.set_main_option("script_location", migrations_dir)

    command.upgrade(cfg, "head")

    config = AppConfig()
    config.database.url = f"sqlite:///{db_path}"
    engine = create_engine_from_config(config)
    ok, message = check_db_ready(engine)
    assert ok, message

