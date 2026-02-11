#!/usr/bin/env sh
set -eu

cd "/app"

mkdir -p "/app/data"

alembic upgrade head

HOST="${FCAM_SERVER__HOST:-0.0.0.0}"
PORT="${FCAM_SERVER__PORT:-8000}"

exec uvicorn "app.main:app" --host "${HOST}" --port "${PORT}"
