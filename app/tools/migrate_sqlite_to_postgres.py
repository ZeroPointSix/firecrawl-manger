from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, Table, create_engine, func, select, text
from sqlalchemy.engine import make_url

from app.db.models import Base

TABLE_ORDER = ["clients", "api_keys", "idempotency_records", "request_logs", "audit_logs"]

DEPENDENCIES: dict[str, set[str]] = {
    "api_keys": {"clients"},
    "idempotency_records": {"clients"},
    "request_logs": {"clients", "api_keys"},
}

SAMPLE_COLUMNS: dict[str, list[str]] = {
    "clients": ["id", "name", "token_hash", "is_active"],
    "api_keys": [
        "id",
        "client_id",
        "api_key_hash",
        "api_key_last4",
        "plan_type",
        "is_active",
        "api_key_ciphertext",
        "account_password_ciphertext",
    ],
    "idempotency_records": ["id", "client_id", "idempotency_key", "request_hash", "status"],
    "request_logs": [
        "id",
        "request_id",
        "client_id",
        "api_key_id",
        "endpoint",
        "method",
        "status_code",
        "success",
        "retry_count",
    ],
    "audit_logs": ["id", "actor_type", "action", "resource_type", "resource_id"],
}


@dataclass(frozen=True)
class TableSummary:
    table: str
    source_rows: int
    migrated_rows: int
    seconds: float


class MigrationError(RuntimeError):
    pass


def _redact_url(url: str) -> str:
    try:
        parsed = make_url(url)
        if parsed.password:
            parsed = parsed.set(password="***")
        return str(parsed)
    except Exception:
        return "<redacted>"


def _sqlite_url_from_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    if normalized.startswith("/"):
        return f"sqlite:////{normalized.lstrip('/')}"
    if len(normalized) >= 3 and normalized[1:3] == ":/":
        return f"sqlite:///{normalized}"
    return f"sqlite:///{normalized}"


def _repo_root() -> Path:
    # /app/app/tools/migrate_sqlite_to_postgres.py -> /app
    return Path(__file__).resolve().parents[2]


def _alembic_head_revision(repo_root: Path) -> str:
    alembic_ini = repo_root / "alembic.ini"
    migrations_dir = repo_root / "migrations"
    if not alembic_ini.exists() or not migrations_dir.exists():
        raise MigrationError("无法定位 alembic.ini 或 migrations/；请在仓库根目录运行")

    cfg = AlembicConfig(alembic_ini.as_posix())
    cfg.set_main_option("script_location", migrations_dir.as_posix())
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    if len(heads) != 1:
        raise MigrationError(f"不支持多 head 的 alembic 版本图（heads={heads}）")
    return heads[0]


def _require_postgres(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        raise MigrationError(f"目标库必须是 Postgres（当前 dialect={engine.dialect.name}）")


def _probe(engine: Engine, *, label: str) -> None:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:
        raise MigrationError(f"{label} 连接失败：{e.__class__.__name__}") from e


def _require_alembic_at_head(engine: Engine, *, repo_root: Path, label: str) -> None:
    head = _alembic_head_revision(repo_root)
    try:
        with engine.connect() as conn:
            current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    except Exception as e:
        raise MigrationError(
            f"{label} 未初始化（缺少 alembic_version）；请先执行 alembic upgrade head"
        ) from e

    if current != head:
        raise MigrationError(
            f"{label} schema 未到 head（current={current} head={head}）；请先执行 alembic upgrade head"
        )


def _table(name: str) -> Table:
    try:
        return Base.metadata.tables[name]
    except KeyError as e:
        raise MigrationError(f"未知表：{name}") from e


def _count_rows(engine: Engine, table: Table) -> int:
    with engine.connect() as conn:
        return int(conn.execute(select(func.count()).select_from(table)).scalar_one())


def _truncate_tables(engine: Engine, tables: list[str]) -> None:
    quoted = ", ".join(f'"{name}"' for name in tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY"))


def _fix_postgres_sequence(conn, table: Table, *, pk_column: str = "id") -> None:
    if pk_column not in table.c:
        return

    seq_name = conn.execute(
        text("SELECT pg_get_serial_sequence(:table, :col)"),
        {"table": table.name, "col": pk_column},
    ).scalar_one_or_none()
    if not seq_name:
        return

    max_id = conn.execute(select(func.max(table.c[pk_column]))).scalar_one()
    if max_id is None:
        conn.execute(text("SELECT setval(CAST(:seq AS regclass), 1, false)"), {"seq": seq_name})
    else:
        conn.execute(
            text("SELECT setval(CAST(:seq AS regclass), :v, true)"),
            {"seq": seq_name, "v": int(max_id)},
        )


def _validate_table_selection(selected: list[str]) -> None:
    selected_set = set(selected)
    for table, deps in DEPENDENCIES.items():
        if table in selected_set and not deps.issubset(selected_set):
            raise MigrationError(f"选择了 {table} 但缺少依赖表：{sorted(deps - selected_set)}")


def _migrate_one_table(
    sqlite_engine: Engine,
    pg_engine: Engine,
    table_name: str,
    *,
    batch_size: int,
    dry_run: bool,
) -> TableSummary:
    table = _table(table_name)
    started = time.perf_counter()

    with sqlite_engine.connect() as s_conn:
        source_rows = int(s_conn.execute(select(func.count()).select_from(table)).scalar_one())

        if dry_run:
            return TableSummary(
                table=table_name, source_rows=source_rows, migrated_rows=0, seconds=0.0
            )

        migrated_rows = 0
        with pg_engine.begin() as p_conn:
            result = s_conn.execute(select(table).order_by(table.c.id))

            batch: list[dict[str, Any]] = []
            for row in result.mappings():
                batch.append(dict(row))
                if len(batch) >= batch_size:
                    p_conn.execute(table.insert(), batch)
                    migrated_rows += len(batch)
                    batch.clear()

            if batch:
                p_conn.execute(table.insert(), batch)
                migrated_rows += len(batch)

            _fix_postgres_sequence(p_conn, table)

    seconds = time.perf_counter() - started
    return TableSummary(
        table=table_name, source_rows=source_rows, migrated_rows=migrated_rows, seconds=seconds
    )


def _verify_counts(sqlite_engine: Engine, pg_engine: Engine, tables: list[str]) -> None:
    for name in tables:
        table = _table(name)
        src = _count_rows(sqlite_engine, table)
        dst = _count_rows(pg_engine, table)
        if src != dst:
            raise MigrationError(f"行数校验失败：{name} source={src} target={dst}")


def _fetch_sample_rows(
    engine: Engine, table: Table, *, columns: list[str], limit: int
) -> list[dict[str, Any]]:
    cols = [table.c[c] for c in columns]
    stmt = select(*cols).order_by(table.c.id).limit(limit)
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(stmt).mappings()]


def _verify_samples(
    sqlite_engine: Engine, pg_engine: Engine, tables: list[str], *, sample_size: int
) -> None:
    for name in tables:
        columns = SAMPLE_COLUMNS.get(name) or ["id"]
        table = _table(name)
        src_rows = _fetch_sample_rows(sqlite_engine, table, columns=columns, limit=sample_size)
        if not src_rows:
            continue

        with pg_engine.connect() as conn:
            for src in src_rows:
                pk = src["id"]
                stmt = select(*[table.c[c] for c in columns]).where(table.c.id == pk)
                dst = conn.execute(stmt).mappings().one_or_none()
                if not dst:
                    raise MigrationError(f"抽样校验失败：{name} id={pk} 目标库缺失")

                for col in columns:
                    if "ciphertext" in col:
                        src_v = src[col]
                        dst_v = dst[col]
                        if src_v is None:
                            if dst_v is not None:
                                raise MigrationError(
                                    f"抽样校验失败：{name} id={pk} {col} source=None target!=None"
                                )
                            continue
                        if dst_v is None:
                            raise MigrationError(
                                f"抽样校验失败：{name} id={pk} {col} source!=None target=None"
                            )

                        # sqlite/psycopg 可能返回 memoryview
                        if not isinstance(src_v, (bytes, bytearray, memoryview)):
                            raise MigrationError(
                                f"抽样校验失败：{name} id={pk} {col} source 类型异常：{type(src_v)}"
                            )
                        if not isinstance(dst_v, (bytes, bytearray, memoryview)):
                            raise MigrationError(
                                f"抽样校验失败：{name} id={pk} {col} target 类型异常：{type(dst_v)}"
                            )

                        src_b = bytes(src_v)
                        dst_b = bytes(dst_v)
                        if len(src_b) <= 0 or len(dst_b) <= 0:
                            raise MigrationError(f"抽样校验失败：{name} id={pk} {col} 长度为 0")
                        if src_b != dst_b:
                            raise MigrationError(f"抽样校验失败：{name} id={pk} {col} 密文不一致")
                        continue

                    if src[col] != dst[col]:
                        raise MigrationError(
                            f"抽样校验失败：{name} id={pk} {col} source={src[col]} target={dst[col]}"
                        )


def _parse_csv_list(value: str | None) -> set[str] | None:
    if value is None:
        return None
    items = [s.strip() for s in value.split(",") if s.strip()]
    return set(items) if items else set()


def _resolve_tables(*, include: set[str] | None, exclude: set[str] | None) -> list[str]:
    unknown = (include or set()) | (exclude or set())
    unknown -= set(TABLE_ORDER)
    if unknown:
        raise MigrationError(f"未知表名：{sorted(unknown)}（可选：{TABLE_ORDER}）")

    tables = list(TABLE_ORDER)
    if include is not None:
        tables = [t for t in tables if t in include]
    if exclude:
        tables = [t for t in tables if t not in exclude]
    if not tables:
        raise MigrationError("未选择任何要迁移的表（检查 --include/--exclude）")
    return tables


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.tools.migrate_sqlite_to_postgres",
        description="一次性把 SQLite 数据迁移到 Postgres（保留主键 ID 并修正序列）",
    )
    parser.add_argument("--sqlite-path", help="源 SQLite 文件路径（可用 FCAM_DATABASE__PATH 兜底）")
    parser.add_argument("--postgres-url", help="目标 Postgres DSN（可用 FCAM_DATABASE__URL 兜底）")
    parser.add_argument("--dry-run", action="store_true", help="只做检查与统计，不写入目标库")
    parser.add_argument(
        "--truncate", action="store_true", help="迁移前 TRUNCATE 目标库的相关表（危险）"
    )
    parser.add_argument(
        "--include",
        help=f"仅迁移这些表（逗号分隔）：{','.join(TABLE_ORDER)}",
    )
    parser.add_argument(
        "--exclude",
        help=f"跳过这些表（逗号分隔）：{','.join(TABLE_ORDER)}",
    )
    parser.add_argument("--batch-size", type=int, default=1000, help="批量写入大小（默认 1000）")
    parser.add_argument(
        "--verify",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="迁移后进行行数与抽样校验（默认开启）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])

    sqlite_path = args.sqlite_path or os.environ.get("FCAM_DATABASE__PATH")
    postgres_url = (
        args.postgres_url
        or os.environ.get("FCAM_DATABASE__URL")
        or os.environ.get("FCAM_DATABASE_URL")
    )

    if not sqlite_path:
        raise MigrationError("缺少 --sqlite-path（或设置 FCAM_DATABASE__PATH）")
    if not postgres_url:
        raise MigrationError("缺少 --postgres-url（或设置 FCAM_DATABASE__URL）")

    sqlite_file = Path(sqlite_path)
    if not sqlite_file.exists():
        raise MigrationError(f"SQLite 文件不存在：{sqlite_file}")

    sqlite_url = _sqlite_url_from_path(sqlite_file.as_posix())

    pg_engine = create_engine(postgres_url, future=True)
    _require_postgres(pg_engine)

    sqlite_engine = create_engine(sqlite_url, future=True)

    print(f"[migrate] sqlite={sqlite_file.as_posix()}")
    print(f"[migrate] postgres={_redact_url(postgres_url)}")

    _probe(sqlite_engine, label="SQLite")
    _probe(pg_engine, label="Postgres")

    repo_root = _repo_root()
    _require_alembic_at_head(sqlite_engine, repo_root=repo_root, label="SQLite")
    _require_alembic_at_head(pg_engine, repo_root=repo_root, label="Postgres")

    include = _parse_csv_list(args.include)
    exclude = _parse_csv_list(args.exclude)
    tables = _resolve_tables(include=include, exclude=exclude)
    _validate_table_selection(tables)

    if args.truncate:
        if args.dry_run:
            print(f"[migrate] dry-run: would truncate target tables: {tables}")
        else:
            print(f"[migrate] truncating target tables (danger): {tables}")
            _truncate_tables(pg_engine, tables)
    else:
        non_empty = []
        for name in tables:
            table = _table(name)
            cnt = _count_rows(pg_engine, table)
            if cnt > 0:
                non_empty.append((name, cnt))
        if non_empty:
            msg = ", ".join(f"{n}={c}" for n, c in non_empty)
            raise MigrationError(f"目标表非空：{msg}；如需覆盖请加 --truncate")

    summaries: list[TableSummary] = []
    for name in tables:
        print(f"[migrate] table={name} start")
        summary = _migrate_one_table(
            sqlite_engine,
            pg_engine,
            name,
            batch_size=args.batch_size,
            dry_run=args.dry_run,
        )
        summaries.append(summary)
        if args.dry_run:
            print(f"[migrate] table={name} source_rows={summary.source_rows} (dry-run)")
        else:
            print(
                f"[migrate] table={name} source_rows={summary.source_rows} migrated_rows={summary.migrated_rows} seconds={summary.seconds:.2f}"
            )

    if not args.dry_run and args.verify:
        _verify_counts(sqlite_engine, pg_engine, tables)
        _verify_samples(sqlite_engine, pg_engine, tables, sample_size=10)
        print("[migrate] verify=ok")

    print("[migrate] summary")
    for s in summaries:
        if args.dry_run:
            print(f"[migrate] {s.table} source_rows={s.source_rows}")
        else:
            print(
                f"[migrate] {s.table} source_rows={s.source_rows} migrated_rows={s.migrated_rows} seconds={s.seconds:.2f}"
            )

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MigrationError as e:
        print(f"[migrate] ERROR: {e}", file=sys.stderr)
        raise SystemExit(2) from None
