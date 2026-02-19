from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.config import AppConfig
from app.core.key_pool import KeyPool
from app.core.time import today_in_timezone
from app.db.models import ApiKey, Base
from app.db.session import create_engine_from_config, create_session_factory
from app.errors import FcamError

pytestmark = pytest.mark.integration


def _db(tmp_path):
    config = AppConfig()
    config.database.path = (tmp_path / "keys.db").as_posix()
    engine = create_engine_from_config(config)
    Base.metadata.create_all(engine)
    SessionLocal = create_session_factory(engine)
    return config, SessionLocal()


def test_key_pool_select_skips_disabled_and_cooling(tmp_path):
    config, db = _db(tmp_path)
    today = today_in_timezone(config.quota.timezone)

    db.add(
        ApiKey(
            api_key_ciphertext=b"x",
            api_key_hash="h1",
            api_key_last4="1111",
            is_active=False,
            status="disabled",
            daily_quota=5,
            daily_usage=0,
            quota_reset_at=today,
            max_concurrent=1,
        )
    )
    db.add(
        ApiKey(
            api_key_ciphertext=b"x",
            api_key_hash="h2",
            api_key_last4="2222",
            is_active=True,
            status="cooling",
            cooldown_until=datetime.now(timezone.utc) + timedelta(seconds=3600),
            daily_quota=5,
            daily_usage=0,
            quota_reset_at=today,
            max_concurrent=1,
        )
    )
    db.add(
        ApiKey(
            api_key_ciphertext=b"x",
            api_key_hash="h3",
            api_key_last4="3333",
            is_active=True,
            status="active",
            daily_quota=5,
            daily_usage=0,
            quota_reset_at=today,
            max_concurrent=1,
        )
    )
    db.commit()

    pool = KeyPool()
    selected = pool.select(db, config)
    assert selected.api_key.api_key_hash == "h3"


def test_key_pool_select_no_keys(tmp_path):
    config, db = _db(tmp_path)
    pool = KeyPool()
    with pytest.raises(FcamError) as e:
        pool.select(db, config)
    assert e.value.code == "NO_KEY_CONFIGURED"


def test_key_pool_select_all_disabled(tmp_path):
    config, db = _db(tmp_path)
    today = today_in_timezone(config.quota.timezone)
    db.add(
        ApiKey(
            api_key_ciphertext=b"x",
            api_key_hash="h1",
            api_key_last4="1111",
            is_active=False,
            status="disabled",
            daily_quota=5,
            daily_usage=0,
            quota_reset_at=today,
            max_concurrent=1,
        )
    )
    db.commit()
    pool = KeyPool()
    with pytest.raises(FcamError) as e:
        pool.select(db, config)
    assert e.value.code == "ALL_KEYS_DISABLED"


def test_key_pool_select_all_quota_exceeded(tmp_path):
    config, db = _db(tmp_path)
    today = today_in_timezone(config.quota.timezone)
    db.add(
        ApiKey(
            api_key_ciphertext=b"x",
            api_key_hash="h1",
            api_key_last4="1111",
            is_active=True,
            status="active",
            daily_quota=1,
            daily_usage=1,
            quota_reset_at=today,
            max_concurrent=1,
        )
    )
    db.commit()
    pool = KeyPool()
    with pytest.raises(FcamError) as e:
        pool.select(db, config)
    assert e.value.code == "ALL_KEYS_QUOTA_EXCEEDED"
    assert e.value.status_code == 429


def test_key_pool_select_all_cooling(tmp_path):
    config, db = _db(tmp_path)
    today = today_in_timezone(config.quota.timezone)

    db.add(
        ApiKey(
            api_key_ciphertext=b"x",
            api_key_hash="h1",
            api_key_last4="1111",
            is_active=True,
            status="cooling",
            cooldown_until=datetime.now(timezone.utc) + timedelta(seconds=60),
            daily_quota=5,
            daily_usage=0,
            quota_reset_at=today,
            max_concurrent=1,
        )
    )
    db.add(
        ApiKey(
            api_key_ciphertext=b"x",
            api_key_hash="h2",
            api_key_last4="2222",
            is_active=True,
            status="cooling",
            cooldown_until=datetime.now(timezone.utc) + timedelta(seconds=120),
            daily_quota=5,
            daily_usage=0,
            quota_reset_at=today,
            max_concurrent=1,
        )
    )
    db.commit()

    pool = KeyPool()
    with pytest.raises(FcamError) as e:
        pool.select(db, config)

    assert e.value.code == "ALL_KEYS_COOLING"
    assert e.value.status_code == 429
    assert int(e.value.retry_after or 0) >= 1
