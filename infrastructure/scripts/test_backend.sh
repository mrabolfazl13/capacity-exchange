#!/usr/bin/env bash
# test_backend.sh — run backend tests (CONTRACTS §13): real pgserver
# PostgreSQL (test DB capacity_test, never SQLite), fully offline-capable.
# Usage: bash infrastructure/scripts/test_backend.sh            # pytest -q
#        bash infrastructure/scripts/test_backend.sh -m "not slow"
set -euo pipefail
cd "$(dirname "$0")/../.."
[ -f .env ] && { set -a; . ./.env; set +a; }
export APP_ENV=test
export CELERY_MODE=local
export REDIS_URL=""   # tests must pass without Redis

if [ ! -d backend ]; then
  echo "[test] ERROR: backend/ not populated yet." >&2
  exit 1
fi

echo "[test] pytest -q $* (pgserver test DB capacity_test; schema via alembic upgrade head)"
cd backend
python -m pytest -q "$@"
