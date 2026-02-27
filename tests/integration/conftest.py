from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.config import AppConfig, Secrets
from app.core.forwarder import Forwarder
from app.core.security import derive_master_key_bytes, encrypt_api_key, hmac_sha256_hex
from app.core.time import today_in_timezone
from app.db.models import ApiKey, Base, Client
from app.db.session import create_engine_from_config, create_session_factory
from app.main import create_app


@pytest.fixture
def admin_headers() -> Callable[[str], dict[str, str]]:
    def _admin_headers(token: str = "admin") -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    return _admin_headers


@pytest.fixture
def client_headers() -> Callable[[str], dict[str, str]]:
    def _client_headers(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    return _client_headers


@pytest.fixture
def make_secrets() -> Callable[..., Secrets]:
    def _make_secrets(
        *, admin_token: str | None = "admin", master_key: str | None = "master"
    ) -> Secrets:
        return Secrets(admin_token=admin_token, master_key=master_key)

    return _make_secrets


@pytest.fixture
def make_config() -> Callable[..., AppConfig]:
    def _make_config(
        tmp_path,
        *,
        db_name: str = "test.db",
        firecrawl_base_url: str = "http://firecrawl.test/v1",
        mutate: Callable[[AppConfig], None] | None = None,
    ) -> AppConfig:
        config = AppConfig()
        config.database.path = (tmp_path / db_name).as_posix()
        config.firecrawl.base_url = firecrawl_base_url
        if mutate is not None:
            mutate(config)
        return config

    return _make_config


@pytest.fixture
def make_app(make_config, make_secrets) -> Callable[..., tuple[FastAPI, AppConfig, Secrets]]:
    def _make_app(
        tmp_path,
        *,
        db_name: str = "test.db",
        firecrawl_base_url: str = "http://firecrawl.test/v1",
        config_mutate: Callable[[AppConfig], None] | None = None,
        secrets: Secrets | None = None,
        admin_token: str | None = "admin",
        master_key: str | None = "master",
        handler: Callable[[httpx.Request], httpx.Response] | None = None,
        create_tables: bool = True,
    ) -> tuple[FastAPI, AppConfig, Secrets]:
        config = make_config(
            tmp_path,
            db_name=db_name,
            firecrawl_base_url=firecrawl_base_url,
            mutate=config_mutate,
        )
        secrets = secrets or make_secrets(admin_token=admin_token, master_key=master_key)

        app = create_app(config=config, secrets=secrets)
        if create_tables:
            Base.metadata.create_all(app.state.db_engine)

        if handler is not None:
            app.state.forwarder = Forwarder(
                config=config,
                secrets=secrets,
                key_pool=app.state.key_pool,
                key_concurrency=app.state.key_concurrency,
                key_rate_limiter=app.state.key_rate_limiter,
                metrics=app.state.metrics,
                cooldown_store=app.state.cooldown_store,
                transport=httpx.MockTransport(handler),
            )

        return app, config, secrets

    return _make_app


@pytest.fixture
def make_db(make_config) -> Callable[..., tuple[AppConfig, Engine, Callable[[], Session]]]:
    def _make_db(
        tmp_path,
        *,
        db_name: str = "test.db",
        firecrawl_base_url: str = "http://firecrawl.test/v1",
        config_mutate: Callable[[AppConfig], None] | None = None,
    ) -> tuple[AppConfig, Engine, Callable[[], Session]]:
        config = make_config(
            tmp_path,
            db_name=db_name,
            firecrawl_base_url=firecrawl_base_url,
            mutate=config_mutate,
        )
        engine = create_engine_from_config(config)
        Base.metadata.create_all(engine)
        SessionLocal = create_session_factory(engine)
        return config, engine, SessionLocal

    return _make_db


@pytest.fixture
def seed_client() -> Callable[..., tuple[Client, str]]:
    def _seed_client(
        db: Session,
        *,
        master_key: str,
        token: str = "fcam_client_token",
        name: str = "svc",
        daily_quota: int = 10_000,
        daily_usage: int = 0,
        rate_limit_per_min: int = 10_000,
        max_concurrent: int = 10,
        is_active: bool = True,
    ) -> tuple[Client, str]:
        token_hash = hmac_sha256_hex(derive_master_key_bytes(master_key), token)
        c = Client(
            name=name,
            token_hash=token_hash,
            is_active=is_active,
            daily_quota=daily_quota,
            daily_usage=daily_usage,
            quota_reset_at=today_in_timezone("UTC"),
            rate_limit_per_min=rate_limit_per_min,
            max_concurrent=max_concurrent,
        )
        db.add(c)
        db.commit()
        db.refresh(c)
        return c, token

    return _seed_client


@pytest.fixture
def seed_api_key() -> Callable[..., ApiKey]:
    def _seed_api_key(
        db: Session,
        *,
        master_key: str,
        api_key_plain: str,
        api_key_hash: str,
        last4: str,
        client_id: int | None = None,
        daily_quota: int = 100_000,
        daily_usage: int = 0,
        rate_limit_per_min: int = 10_000,
        max_concurrent: int = 10,
        is_active: bool = True,
        status: str = "active",
        **extra: Any,
    ) -> ApiKey:
        key_bytes = derive_master_key_bytes(master_key)
        k = ApiKey(
            client_id=client_id,
            api_key_ciphertext=encrypt_api_key(key_bytes, api_key_plain),
            api_key_hash=api_key_hash,
            api_key_last4=last4,
            is_active=is_active,
            status=status,
            daily_quota=daily_quota,
            daily_usage=daily_usage,
            quota_reset_at=today_in_timezone("UTC"),
            max_concurrent=max_concurrent,
            rate_limit_per_min=rate_limit_per_min,
            **extra,
        )
        db.add(k)
        db.commit()
        db.refresh(k)
        return k

    return _seed_api_key
