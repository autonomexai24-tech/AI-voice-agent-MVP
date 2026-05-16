#!/usr/bin/env sh
set -eu

: "${HOST:=0.0.0.0}"
: "${PORT:=8000}"

exec python -m uvicorn api.main:app --host "$HOST" --port "$PORT"
