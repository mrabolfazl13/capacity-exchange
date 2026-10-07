#!/usr/bin/env bash
# dev_migrate.sh — apply all Alembic migrations (`alembic upgrade head`)
# against the dev database (pgserver at localhost:5544 by default).
set -euo pipefail
cd "$(dirname "$0")/../.."
[ -f .env ] && { set -a; . ./.env; set +a; }
export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://capacity:capacity@localhost:5544/capacity}"

if [ ! -d backend ]; then
  echo "[migrate] ERROR: backend/ not populated yet (backend agent owns Alembic)." >&2
  exit 1
fi

echo "[migrate] alembic upgrade head -> $DATABASE_URL"
cd backend
python -m alembic upgrade head
echo "[migrate] OK — current revision:"
python -m alembic current
