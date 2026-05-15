#!/usr/bin/env sh
set -eu

: "${HOST:=0.0.0.0}"
: "${PORT:=8000}"
: "${INITIALIZE_DATABASE_ON_STARTUP:=true}"

if [ "$INITIALIZE_DATABASE_ON_STARTUP" = "true" ]; then
  python scripts/initialize_database.py
fi

exec python -m uvicorn api.main:app --host "$HOST" --port "$PORT"
