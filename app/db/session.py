from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool, StaticPool

from app.config import AppConfig

REQUIRED_TABLES = {
    "api_keys",
    "clients",
    "request_logs",
    "audit_logs",
    "idempotency_records",
    "upstream_resource_bindings",
    "credit_snapshots",
}


def build_database_url(config: AppConfig) -> str:
    if config.database.url:
        return config.database.url
    path = config.database.path.replace("\\", "/")
    if path.startswith("/"):
        return f"sqlite:////{path.lstrip('/')}"
    if len(path) >= 3 and path[1:3] == ":/":
        return f"sqlite:///{path}"
    return f"sqlite:///./{path.lstrip('./')}"


def create_engine_from_config(config: AppConfig) -> Engine:
    url = build_database_url(config)
    connect_args: dict[str, Any] = {}
    engine_kwargs: dict[str, Any] = {"future": True}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        engine_kwargs["poolclass"] = StaticPool if ":memory:" in url else NullPool
    return create_engine(url, connect_args=connect_args, **engine_kwargs)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _sqlite_tables(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        rows: Iterable[tuple[str]] = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")
        )
        return {row[0] for row in rows}


def check_db_ready(engine: Engine) -> tuple[bool, str]:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))

        if engine.url.get_backend_name() == "sqlite":
            tables = _sqlite_tables(engine)
            missing = REQUIRED_TABLES - tables
            if missing:
                return False, f"Database not initialized (missing tables: {sorted(missing)})"

    except Exception:
        return False, "Database unavailable"

    return True, "ok"
