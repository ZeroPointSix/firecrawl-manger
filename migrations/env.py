from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool, text

from app.config import load_config
from app.db.models import Base
from app.db.session import build_database_url

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    url = os.environ.get("FCAM_DATABASE_URL")
    if url:
        return url

    url = os.environ.get("FCAM_DATABASE__URL")
    if url:
        return url

    path = os.environ.get("FCAM_DATABASE__PATH")
    if path:
        # Support overriding sqlite file location for migration-time bootstrapping.
        # - absolute: /app/data/api_manager.db -> sqlite:////app/data/api_manager.db
        # - relative: ./data/api_manager.db   -> sqlite:///./data/api_manager.db
        if os.path.isabs(path):
            return f"sqlite:////{path.lstrip('/')}"
        return f"sqlite:///{path}"

    cfg, _ = load_config()
    return build_database_url(cfg)


def _ensure_alembic_version_column_capacity(connection) -> None:
    # Alembic's built-in version table uses VARCHAR(32). Our revision ids are longer
    # (e.g. "0002_add_retry_count_to_request_logs" is 36 chars), which will fail on Postgres.
    if connection.dialect.name != "postgresql":
        return

    row = connection.execute(
        text(
            """
            SELECT character_maximum_length
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'alembic_version'
              AND column_name = 'version_num'
            """
        )
    ).one_or_none()

    if row is None:
        # Table doesn't exist yet; create it with a larger column.
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS alembic_version (
                  version_num VARCHAR(255) NOT NULL PRIMARY KEY
                )
                """
            )
        )
        return

    max_len = row[0]
    if max_len is not None and int(max_len) < 64:
        connection.execute(
            text("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(255)")
        )


def run_migrations_offline() -> None:
    url = _database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _database_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            _ensure_alembic_version_column_capacity(connection)
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
