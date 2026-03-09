from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine

from app.db.models import Base
from app.tools import migrate_sqlite_to_postgres as m

pytestmark = pytest.mark.unit


def _sqlite_engine(tmp_path, name: str):
    db_path = tmp_path / name
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
    Base.metadata.create_all(engine)
    return engine


def _seed_minimal_dataset(engine):
    now = datetime.now(timezone.utc)
    tables = Base.metadata.tables
    with engine.begin() as conn:
        conn.execute(
            tables["clients"].insert(),
            {
                "id": 1,
                "name": "c1",
                "token_hash": "a" * 64,
                "is_active": True,
                "daily_usage": 0,
                "rate_limit_per_min": 60,
                "max_concurrent": 10,
                "created_at": now,
            },
        )
        conn.execute(
            tables["api_keys"].insert(),
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
            },
        )
        conn.execute(
            tables["idempotency_records"].insert(),
            {
                "id": 100,
                "client_id": 1,
                "idempotency_key": "idem-1",
                "request_hash": "c" * 64,
                "status": "ok",
                "created_at": now,
            },
        )
        conn.execute(
            tables["request_logs"].insert(),
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
            },
        )
        conn.execute(
            tables["audit_logs"].insert(),
            {
                "id": 2000,
                "actor_type": "admin",
                "actor_id": "1",
                "action": "create",
                "resource_type": "client",
                "resource_id": "1",
                "created_at": now,
            },
        )


def test_sqlite_url_from_path_handles_posix_and_windows_paths():
    assert m._sqlite_url_from_path("/tmp/a.db") == "sqlite:////tmp/a.db"
    assert m._sqlite_url_from_path(r"C:\data\a.db") == "sqlite:///C:/data/a.db"
    assert m._sqlite_url_from_path("./data/a.db") == "sqlite:///./data/a.db"


def test_parse_csv_list_and_resolve_tables():
    assert m._parse_csv_list(None) is None
    assert m._parse_csv_list("") == set()
    assert m._parse_csv_list("clients, api_keys") == {"clients", "api_keys"}

    assert m._resolve_tables(include={"clients", "api_keys"}, exclude=None) == [
        "clients",
        "api_keys",
    ]
    assert "audit_logs" not in m._resolve_tables(include=None, exclude={"audit_logs"})

    with pytest.raises(m.MigrationError, match="未知表名"):
        m._resolve_tables(include={"nope"}, exclude=None)


def test_validate_table_selection_requires_dependencies():
    with pytest.raises(m.MigrationError, match="缺少依赖表"):
        m._validate_table_selection(["api_keys"])

    with pytest.raises(m.MigrationError, match="缺少依赖表"):
        m._validate_table_selection(["request_logs", "clients"])

    m._validate_table_selection(["clients", "api_keys", "request_logs"])


def test_alembic_head_revision_is_available():
    head = m._alembic_head_revision(m._repo_root())
    assert isinstance(head, str) and head


def test_require_postgres_rejects_non_postgres_engines(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'x.db').as_posix()}", future=True)
    try:
        with pytest.raises(m.MigrationError, match="目标库必须是 Postgres"):
            m._require_postgres(engine)
    finally:
        engine.dispose()


def test_migrate_one_table_dry_run_counts_rows(tmp_path):
    engine = _sqlite_engine(tmp_path, "source.db")
    try:
        _seed_minimal_dataset(engine)

        summary = m._migrate_one_table(
            engine, engine, "clients", batch_size=1000, dry_run=True
        )  # pg_engine unused
        assert summary.table == "clients"
        assert summary.source_rows == 1
        assert summary.migrated_rows == 0
    finally:
        engine.dispose()


def test_verify_counts_and_samples_pass_when_data_matches(tmp_path):
    source = _sqlite_engine(tmp_path, "source.db")
    target = _sqlite_engine(tmp_path, "target.db")
    try:
        _seed_minimal_dataset(source)
        _seed_minimal_dataset(target)

        tables = ["clients", "api_keys", "idempotency_records", "request_logs", "audit_logs"]
        m._verify_counts(source, target, tables)
        m._verify_samples(source, target, tables, sample_size=10)
    finally:
        source.dispose()
        target.dispose()


def test_verify_samples_fails_on_ciphertext_mismatch(tmp_path):
    source = _sqlite_engine(tmp_path, "source.db")
    target = _sqlite_engine(tmp_path, "target.db")
    try:
        _seed_minimal_dataset(source)
        _seed_minimal_dataset(target)

        api_keys = Base.metadata.tables["api_keys"]
        with target.begin() as conn:
            conn.execute(
                api_keys.update().where(api_keys.c.id == 10).values(api_key_ciphertext=b"cipher2")
            )

        with pytest.raises(m.MigrationError, match="密文不一致"):
            m._verify_samples(source, target, ["api_keys"], sample_size=10)
    finally:
        source.dispose()
        target.dispose()


def test_fix_postgres_sequence_noop_for_missing_pk_column(tmp_path):
    engine = _sqlite_engine(tmp_path, "source.db")
    try:
        table = Base.metadata.tables["clients"]
        with engine.begin() as conn:
            m._fix_postgres_sequence(conn, table, pk_column="nope")
    finally:
        engine.dispose()


def test_redact_url_masks_password_and_handles_parse_errors(monkeypatch):
    masked = m._redact_url("postgresql+psycopg://user:secret@localhost:5432/dbname")
    assert "secret" not in masked
    assert "***" in masked

    monkeypatch.setattr(m, "make_url", lambda _: (_ for _ in ()).throw(ValueError("boom")))
    assert m._redact_url("invalid-url") == "<redacted>"


def test_parse_args_and_main_validate_required_inputs(tmp_path):
    args = m._parse_args(
        [
            "--sqlite-path",
            "source.db",
            "--postgres-url",
            "postgresql://user:pass@localhost/db",
            "--dry-run",
            "--truncate",
            "--include",
            "clients,api_keys",
            "--exclude",
            "audit_logs",
            "--batch-size",
            "99",
            "--no-verify",
        ]
    )

    assert args.sqlite_path == "source.db"
    assert args.postgres_url == "postgresql://user:pass@localhost/db"
    assert args.dry_run is True
    assert args.truncate is True
    assert args.include == "clients,api_keys"
    assert args.exclude == "audit_logs"
    assert args.batch_size == 99
    assert args.verify is False

    with pytest.raises(m.MigrationError, match="缺少 --sqlite-path"):
        m.main([])

    with pytest.raises(m.MigrationError, match="SQLite 文件不存在"):
        m.main(
            [
                "--sqlite-path",
                str(tmp_path / "missing.db"),
                "--postgres-url",
                "postgresql://user:pass@localhost/db",
            ]
        )


def test_probe_and_require_alembic_report_clear_errors(tmp_path, monkeypatch):
    class BrokenEngine:
        def connect(self):
            raise RuntimeError("down")

    with pytest.raises(m.MigrationError, match="SQLite 连接失败：RuntimeError"):
        m._probe(BrokenEngine(), label="SQLite")

    engine = _sqlite_engine(tmp_path, "source.db")
    try:
        monkeypatch.setattr(m, "_alembic_head_revision", lambda _: "head-1")
        with pytest.raises(m.MigrationError, match="缺少 alembic_version"):
            m._require_alembic_at_head(engine, repo_root=m._repo_root(), label="SQLite")
    finally:
        engine.dispose()
