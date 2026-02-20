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

DB_EXPLICIT=0
if [ -n "${FCAM_DATABASE_URL:-}" ] || [ -n "${FCAM_DATABASE__URL:-}" ] || [ -n "${FCAM_DATABASE__PATH:-}" ]; then
  DB_EXPLICIT=1
fi

set_sqlite_path() {
  p="$1"
  export FCAM_DATABASE__PATH="$p"
  if os_is_abs_path "$p"; then
    export FCAM_DATABASE_URL="sqlite:////${p#/}"
  else
    export FCAM_DATABASE_URL="sqlite:///${p}"
  fi
}

os_is_abs_path() {
  case "$1" in
    /*) return 0 ;;
    *) return 1 ;;
  esac
}

if [ "${DB_EXPLICIT}" -eq 0 ]; then
  set_sqlite_path "/app/data/api_manager.db"
  echo "[fcam] db.mode=sqlite db.path=${FCAM_DATABASE__PATH}" >&2
fi

if ! is_writable_dir "${DATA_DIR}"; then
  echo "[fcam] WARN: ${DATA_DIR} is not writable." >&2
  ls -ld "${DATA_DIR}" 2>/dev/null || true

  # If the user explicitly configured database settings, don't silently override.
  # Otherwise, fall back to a writable tmp location so the service can boot.
  if [ "${DB_EXPLICIT}" -eq 1 ]; then
    echo "[fcam] ERROR: database is configured but ${DATA_DIR} is not writable; refusing to start." >&2
    echo "[fcam]        Fix by mounting a writable volume to ${DATA_DIR}, or set FCAM_DATABASE__URL/FCAM_DATABASE_URL to Postgres." >&2
    exit 1
  fi

  if [ "${FCAM_DB_FALLBACK_TMP:-1}" = "1" ]; then
    set_sqlite_path "/tmp/api_manager.db"
    echo "[fcam] WARN: falling back to SQLite at ${FCAM_DATABASE__PATH} (NOT persistent)." >&2
    echo "[fcam]       To keep persistence, mount a writable volume to ${DATA_DIR} or configure Postgres." >&2
  else
    echo "[fcam] ERROR: ${DATA_DIR} not writable and FCAM_DB_FALLBACK_TMP=0; refusing to start." >&2
    exit 1
  fi
fi

if alembic upgrade head; then
  :
else
  code=$?
  if [ "${DB_EXPLICIT}" -eq 0 ] && [ "${FCAM_DB_FALLBACK_TMP:-1}" = "1" ] && [ "${FCAM_DATABASE__PATH:-}" != "/tmp/api_manager.db" ]; then
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
