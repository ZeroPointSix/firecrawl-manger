"""Unit tests for KeyPool provider-aware selection."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppConfig
from app.core.key_pool import KeyPool
from app.db.models import ApiKey, Base
from app.errors import FcamError

pytestmark = pytest.mark.unit


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def config():
    cfg, _ = AppConfig.model_validate({}), None
    return cfg


def _add_key(db: Session, *, provider: str = "firecrawl", is_active: bool = True, name: str | None = None) -> ApiKey:
    key = ApiKey(
        api_key_ciphertext=b"cipher_placeholder",
        api_key_hash=f"hash_{uuid.uuid4().hex}",
        api_key_last4="0000",
        is_active=is_active,
        status="active" if is_active else "disabled",
        provider=provider,
        name=name,
    )
    db.add(key)
    db.commit()
    db.refresh(key)
    return key


def test_select_returns_only_firecrawl_keys(db, config):
    """KeyPool.select(provider='firecrawl') should only return firecrawl keys."""
    _add_key(db, provider="firecrawl", name="fc1")
    _add_key(db, provider="exa", name="exa1")

    pool = KeyPool()
    result = pool.select(db, config, provider="firecrawl")
    assert result.api_key.provider == "firecrawl"


def test_select_returns_only_exa_keys(db, config):
    """KeyPool.select(provider='exa') should only return exa keys."""
    _add_key(db, provider="firecrawl", name="fc1")
    _add_key(db, provider="exa", name="exa1")

    pool = KeyPool()
    result = pool.select(db, config, provider="exa")
    assert result.api_key.provider == "exa"


def test_select_no_key_for_provider_raises(db, config):
    """KeyPool.select() should raise NO_KEY_CONFIGURED when no keys for provider exist."""
    _add_key(db, provider="firecrawl", name="fc1")

    pool = KeyPool()
    with pytest.raises(FcamError, match="No key configured"):
        pool.select(db, config, provider="exa")


def test_select_provider_scoped_round_robin(db, config):
    """Round-robin index should be scoped by provider."""
    fc1 = _add_key(db, provider="firecrawl", name="fc1")
    fc2 = _add_key(db, provider="firecrawl", name="fc2")
    _add_key(db, provider="exa", name="exa1")

    pool = KeyPool()

    # First firecrawl selection
    r1 = pool.select(db, config, provider="firecrawl")
    # First exa selection — should start from exa's own index, not firecrawl's
    r_exa = pool.select(db, config, provider="exa")
    assert r_exa.api_key.provider == "exa"

    # Second firecrawl selection should advance firecrawl's index
    r2 = pool.select(db, config, provider="firecrawl")
    assert r2.api_key.provider == "firecrawl"
    # Should be different from first (round-robin)
    assert r1.api_key.id != r2.api_key.id or len([fc1, fc2]) == 1


def test_select_all_disabled_for_provider(db, config):
    """ALL_KEYS_DISABLED should be raised when all keys of a provider are disabled."""
    _add_key(db, provider="exa", name="exa_disabled", is_active=False)
    _add_key(db, provider="firecrawl", name="fc_active")

    pool = KeyPool()
    with pytest.raises(FcamError, match="All keys disabled"):
        pool.select(db, config, provider="exa")

    # Firecrawl should still work
    result = pool.select(db, config, provider="firecrawl")
    assert result.api_key.provider == "firecrawl"
