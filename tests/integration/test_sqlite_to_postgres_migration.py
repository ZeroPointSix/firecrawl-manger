from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, text

from app.db.models import Base

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _env_flag(name: str) -> bool:
    v = (os.environ.get(name) or "").strip().lower()
    return v in {"1", "true", "yes", "y", "on"}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture()
def postgres_url():
    if not _env_flag("FCAM_TEST_POSTGRES"):
        pytest.skip("需要设置环境变量 FCAM_TEST_POSTGRES=1 才会运行 Postgres 迁移集成测试")
    if not shutil.which("docker"):
        pytest.skip("未检测到 docker，跳过 Postgres 迁移集成测试")

    port = _free_port()
    container = subprocess.check_output(
        [
            "docker",
            "run",
            "--rm",
            "-d",
            "-e",
            "POSTGRES_USER=fcam",
            "-e",
            "POSTGRES_PASSWORD=fcam_password",
            "-e",
            "POSTGRES_DB=fcam",
            "-p",
            f"{port}:5432",
            "postgres:16-alpine",
        ],
        text=True,
    ).strip()

    url = f"postgresql+psycopg://fcam:fcam_password@127.0.0.1:{port}/fcam"
    engine = create_engine(url, future=True)

    deadline = time.time() + 45
    while True:
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            break
        except Exception:
            if time.time() > deadline:
                subprocess.run(["docker", "rm", "-f", container], check=False)
                pytest.fail("Postgres 容器启动超时")
            time.sleep(1)

    try:
        yield url
    finally:
        subprocess.run(["docker", "rm", "-f", container], check=False)


def _alembic_upgrade_head(monkeypatch, *, database_url: str) -> None:
    monkeypatch.setenv("FCAM_DATABASE_URL", database_url)

    alembic_ini = (_REPO_ROOT / "alembic.ini").as_posix()
    migrations_dir = (_REPO_ROOT / "migrations").as_posix()

    cfg = AlembicConfig(alembic_ini)
    cfg.set_main_option("script_location", migrations_dir)

    command.upgrade(cfg, "head")


def test_migrate_sqlite_to_postgres_smoke(tmp_path, monkeypatch, postgres_url: str):
    _alembic_upgrade_head(monkeypatch, database_url=postgres_url)

    sqlite_path = tmp_path / "source.db"
    _alembic_upgrade_head(monkeypatch, database_url=f"sqlite:///{sqlite_path.as_posix()}")
    sqlite_engine = create_engine(f"sqlite:///{sqlite_path.as_posix()}", future=True)

    tables = Base.metadata.tables
    now = datetime.utcnow()

    with sqlite_engine.begin() as conn:
        conn.execute(
            tables["clients"].insert(),
            [
                {
                    "id": 1,
                    "name": "c1",
                    "token_hash": "a" * 64,
                    "is_active": True,
                    "daily_usage": 0,
                    "rate_limit_per_min": 60,
                    "max_concurrent": 10,
                    "created_at": now,
                }
            ],
        )
        conn.execute(
            tables["api_keys"].insert(),
            [
                {
                    "id": 10,
                    "client_id": 1,
                    "api_key_ciphertext": b"cipher",
                    "api_key_hash": "b" * 64,
                    "api_key_last4": "0001",
                    "plan_type": "free",
                    "is_active": True,
                    "daily_quota": 5,
                    "daily_usage": 0,
                    "max_concurrent": 2,
                    "current_concurrent": 0,
                    "rate_limit_per_min": 10,
                    "total_requests": 0,
                    "created_at": now,
                    "status": "active",
                }
            ],
        )
        conn.execute(
            tables["idempotency_records"].insert(),
            [
                {
                    "id": 100,
                    "client_id": 1,
                    "idempotency_key": "idem-1",
                    "request_hash": "c" * 64,
                    "status": "ok",
                    "created_at": now,
                }
            ],
        )
        conn.execute(
            tables["request_logs"].insert(),
            [
                {
                    "id": 1000,
                    "request_id": "req-1",
                    "client_id": 1,
                    "api_key_id": 10,
                    "endpoint": "scrape",
                    "method": "POST",
                    "status_code": 200,
                    "response_time_ms": 12,
                    "success": True,
                    "retry_count": 0,
                    "created_at": now,
                }
            ],
        )
        conn.execute(
            tables["audit_logs"].insert(),
            [
                {
                    "id": 2000,
                    "actor_type": "admin",
                    "actor_id": "1",
                    "action": "create",
                    "resource_type": "client",
                    "resource_id": "1",
                    "created_at": now,
                }
            ],
        )

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.tools.migrate_sqlite_to_postgres",
            "--sqlite-path",
            sqlite_path.as_posix(),
            "--postgres-url",
            postgres_url,
            "--batch-size",
            "2",
            "--verify",
        ],
        cwd=str(_REPO_ROOT),
        env=os.environ.copy(),
        check=True,
        capture_output=True,
        text=True,
    )
    assert "verify=ok" in proc.stdout

    pg_engine = create_engine(postgres_url, future=True)
    with pg_engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM clients")).scalar_one() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM api_keys")).scalar_one() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM idempotency_records")).scalar_one() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM request_logs")).scalar_one() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM audit_logs")).scalar_one() == 1

    # 序列修正后应可继续插入（不显式指定 id）
    clients_table = Base.metadata.tables["clients"]
    with pg_engine.begin() as conn:
        new_id = conn.execute(
            clients_table.insert()
            .values(
                name="c2",
                token_hash="d" * 64,
                is_active=True,
                daily_usage=0,
                rate_limit_per_min=60,
                max_concurrent=10,
                created_at=datetime.utcnow(),
            )
            .returning(clients_table.c.id)
        ).scalar_one()
        assert int(new_id) > 1
