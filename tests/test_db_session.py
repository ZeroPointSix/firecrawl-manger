from __future__ import annotations

from sqlalchemy import text

from app.config import AppConfig
from app.db.models import Base
from app.db.session import create_engine_from_config, create_session_factory


def test_create_session_factory_can_query(tmp_path):
    config = AppConfig()
    config.database.path = (tmp_path / "session.db").as_posix()
    engine = create_engine_from_config(config)
    Base.metadata.create_all(engine)

    SessionLocal = create_session_factory(engine)
    with SessionLocal() as session:
        assert session.execute(text("SELECT 1")).scalar() == 1

