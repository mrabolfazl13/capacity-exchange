#!/usr/bin/env bash
# dev_backend.sh — run the FastAPI dev server in the FOREGROUND:
# http://127.0.0.1:8000 (docs at /docs). Auto-reloads on code changes.
# Redis is NOT required: REDIS_URL empty => DB-only behaviour (CONTRACTS §11).
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"
[ -f .env ] && { set -a; . ./.env; set +a; }
export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://capacity:capacity@localhost:5544/capacity}"
export CELERY_MODE="${CELERY_MODE:-local}"
export APP_ENV="${APP_ENV:-dev}"
export CORS_ORIGINS="${CORS_ORIGINS:-http://localhost:5173,http://127.0.0.1:5173,tauri://localhost,http://tauri.localhost}"

cd backend
# `/ai/*` imports capacity_ai from the sibling ai/ package (local, not on PyPI).
export PYTHONPATH="$REPO_ROOT/ai${PYTHONPATH:+:$PYTHONPATH}"
echo "[backend] uvicorn app.main:app on http://127.0.0.1:8000 (Ctrl+C to stop)"
exec python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
