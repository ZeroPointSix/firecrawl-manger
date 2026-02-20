#!/usr/bin/env sh
set -eu

cd "/app"

DATA_DIR="/app/data"

echo "[fcam] entrypoint starting; uid=$(id -u) gid=$(id -g) user=$(whoami 2>/dev/null || echo '?')" >&2
echo "[fcam] pwd=$(pwd)" >&2
echo "[fcam] data_dir=${DATA_DIR}" >&2

mkdir -p "${DATA_DIR}" || true

is_writable_dir() {
  d="$1"
  mkdir -p "$d" 2>/dev/null || return 1
  t="$d/.fcam_write_test_$$"
  ( : > "$t" ) 2>/dev/null || return 1
  rm -f "$t" 2>/dev/null || true
  return 0
}

os_is_abs_path() {
  case "$1" in
    /*) return 0 ;;
    *) return 1 ;;
  esac
}

redact_dsn() {
  python - "$1" <<'PY'
from __future__ import annotations

import sys

try:
    from sqlalchemy.engine import make_url

    url = make_url(sys.argv[1])
    if url.password:
        url = url.set(password="***")
    print(str(url))
except Exception:
    print("<redacted>")
PY
}

sqlite_url_from_path() {
  p="$1"
  if os_is_abs_path "$p"; then
    echo "sqlite:////${p#/}"
  else
    echo "sqlite:///${p}"
  fi
}

sqlite_path_from_url() {
  url="$1"
  case "$url" in
    sqlite:////*) echo "/${url#sqlite:////}" ;;
    sqlite:///*) echo "${url#sqlite:///}" ;;
    *) echo "" ;;
  esac
}

set_sqlite_path() {
  p="$1"
  sqlite_url="$(sqlite_url_from_path "$p")"
  export FCAM_DATABASE__PATH="$p"
  export FCAM_DATABASE_URL="$sqlite_url"
  export FCAM_DATABASE__URL="$sqlite_url"
}

# Normalize env aliases so migration-time (FCAM_DATABASE_URL) and runtime (FCAM_DATABASE__URL) stay consistent.
if [ -n "${FCAM_DATABASE__URL:-}" ] && [ -z "${FCAM_DATABASE_URL:-}" ]; then
  export FCAM_DATABASE_URL="${FCAM_DATABASE__URL}"
  echo "[fcam] WARN: FCAM_DATABASE_URL not set; aliasing from FCAM_DATABASE__URL." >&2
fi
if [ -n "${FCAM_DATABASE_URL:-}" ] && [ -z "${FCAM_DATABASE__URL:-}" ]; then
  export FCAM_DATABASE__URL="${FCAM_DATABASE_URL}"
  echo "[fcam] WARN: FCAM_DATABASE__URL not set; aliasing from FCAM_DATABASE_URL." >&2
fi
if [ -n "${FCAM_DATABASE_URL:-}" ] && [ -n "${FCAM_DATABASE__URL:-}" ] && [ "${FCAM_DATABASE_URL}" != "${FCAM_DATABASE__URL}" ]; then
  echo "[fcam] ERROR: FCAM_DATABASE_URL and FCAM_DATABASE__URL both set but differ; refusing to start." >&2
  exit 1
fi

DB_SOURCE=""
DB_URL=""
DB_SQLITE_PATH=""

if [ -n "${FCAM_DATABASE_URL:-}" ]; then
  DB_URL="${FCAM_DATABASE_URL}"
  DB_SOURCE="env"
elif [ -n "${FCAM_DATABASE__URL:-}" ]; then
  DB_URL="${FCAM_DATABASE__URL}"
  DB_SOURCE="env"
elif [ -n "${FCAM_DATABASE__PATH:-}" ]; then
  set_sqlite_path "${FCAM_DATABASE__PATH}"
  DB_URL="${FCAM_DATABASE_URL}"
  DB_SOURCE="env"
else
  config_line="$(python - <<'PY'
from __future__ import annotations

from app.config import AppConfig, load_config
from app.db.session import build_database_url

cfg, _ = load_config()
url = build_database_url(cfg)
default_path = AppConfig().database.path
source = "default" if (cfg.database.url is None and cfg.database.path == default_path) else "config"
print(f"{url}\t{source}\t{cfg.database.path}")
PY
  )"
  oldifs="$IFS"
  IFS="$(printf '\t')"
  set -- $config_line
  IFS="$oldifs"
  DB_URL="$1"
  DB_SOURCE="$2"
  DB_SQLITE_PATH="$3"
fi

DB_BACKEND="other"
case "${DB_URL}" in
  sqlite*|"") DB_BACKEND="sqlite" ;;
  postgresql*|postgres*) DB_BACKEND="postgres" ;;
esac

if [ "${DB_BACKEND}" = "sqlite" ]; then
  if [ -n "${FCAM_DATABASE__PATH:-}" ]; then
    DB_SQLITE_PATH="${FCAM_DATABASE__PATH}"
  elif [ -z "${DB_SQLITE_PATH}" ]; then
    DB_SQLITE_PATH="$(sqlite_path_from_url "${DB_URL}")"
  fi

  sqlite_dir="$(dirname "${DB_SQLITE_PATH:-${DATA_DIR}}")"
  if ! is_writable_dir "${sqlite_dir}"; then
    echo "[fcam] WARN: sqlite_dir=${sqlite_dir} is not writable." >&2
    ls -ld "${sqlite_dir}" 2>/dev/null || true

    # If the user explicitly configured database settings (env or config), don't silently override.
    if [ "${DB_SOURCE}" != "default" ]; then
      echo "[fcam] ERROR: database is configured but sqlite_dir is not writable; refusing to start." >&2
      echo "[fcam]        Fix by mounting a writable volume, or configure Postgres via FCAM_DATABASE__URL/FCAM_DATABASE_URL." >&2
      exit 1
    fi

    if [ "${FCAM_DB_FALLBACK_TMP:-1}" = "1" ]; then
      set_sqlite_path "/tmp/api_manager.db"
      DB_URL="${FCAM_DATABASE_URL}"
      DB_SQLITE_PATH="${FCAM_DATABASE__PATH}"
      echo "[fcam] WARN: falling back to SQLite at ${DB_SQLITE_PATH} (NOT persistent)." >&2
      echo "[fcam]       To keep persistence, mount a writable volume or configure Postgres." >&2
    else
      echo "[fcam] ERROR: sqlite_dir not writable and FCAM_DB_FALLBACK_TMP=0; refusing to start." >&2
      exit 1
    fi
  fi
fi

if [ "${DB_BACKEND}" = "sqlite" ]; then
  echo "[fcam] db.backend=sqlite db.source=${DB_SOURCE} db.path=${DB_SQLITE_PATH:-<unknown>}" >&2
else
  echo "[fcam] db.backend=${DB_BACKEND} db.source=${DB_SOURCE} db.url=$(redact_dsn "${DB_URL}")" >&2
fi

if [ "${DB_BACKEND}" != "sqlite" ]; then
  retries="${FCAM_DB_MIGRATE_RETRIES:-30}"
  sleep_seconds="${FCAM_DB_MIGRATE_SLEEP_SECONDS:-2}"

  if [ "${retries}" -gt 0 ]; then
    i=1
    while :; do
      if python - "${DB_URL}" <<'PY'
from __future__ import annotations

import sys

from sqlalchemy import create_engine, text

try:
    engine = create_engine(sys.argv[1], pool_pre_ping=True, future=True)
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
except Exception as e:
    print(f"db probe failed: {e.__class__.__name__}", file=sys.stderr)
    raise SystemExit(1)
PY
      then
        echo "[fcam] db.probe=ok" >&2
        break
      fi

      if [ "${i}" -ge "${retries}" ]; then
        echo "[fcam] ERROR: database not ready after ${retries} attempts; aborting." >&2
        exit 1
      fi

      echo "[fcam] WARN: database not ready; retry ${i}/${retries} in ${sleep_seconds}s" >&2
      i=$((i + 1))
      sleep "${sleep_seconds}"
    done
  fi
fi

if alembic upgrade head; then
  :
else
  code=$?
  if [ "${DB_BACKEND}" = "sqlite" ] && [ "${DB_SOURCE}" = "default" ] && [ "${FCAM_DB_FALLBACK_TMP:-1}" = "1" ] && [ "${FCAM_DATABASE__PATH:-}" != "/tmp/api_manager.db" ]; then
    echo "[fcam] WARN: alembic upgrade failed (exit=${code}); retry with /tmp (NOT persistent)." >&2
    set_sqlite_path "/tmp/api_manager.db"
    alembic upgrade head
  else
    exit "${code}"
  fi
fi

HOST="${FCAM_SERVER__HOST:-0.0.0.0}"
PORT="${FCAM_SERVER__PORT:-8000}"

exec uvicorn "app.main:app" --host "${HOST}" --port "${PORT}"
