#!/usr/bin/env bash
# dev_up.sh — bring up the FULL local dev stack WITHOUT Docker:
#   1) real PostgreSQL via pgserver (backend/scripts/dev_db.py, port 5544)
#   2) alembic upgrade head + demo seed
#   3) uvicorn backend on http://127.0.0.1:8000 (background)
#   4) local in-process job runner, CELERY_MODE=local (background)
# Logs/PIDs land in ./.run/. Stop everything with dev_down.sh.
set -euo pipefail
cd "$(dirname "$0")/../.."
RUN=.run; mkdir -p "$RUN"
[ -f .env ] && { set -a; . ./.env; set +a; }
export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://capacity:capacity@localhost:5544/capacity}"
export CELERY_MODE=local   # no Redis on this machine (CONTRACTS §10/§11)

port_open() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
start_bg()  { local n="$1"; shift; nohup "$@" >"$RUN/$n.log" 2>&1 & echo $! >"$RUN/$n.pid"; }

if port_open 5544; then
  echo "[dev-up] dev PostgreSQL already listening on :5544"
else
  if [ ! -f backend/scripts/dev_db.py ]; then
    echo "[dev-up] ERROR: backend/scripts/dev_db.py not found (owned by backend agent)." >&2
    echo "[dev-up] It must start pgserver at localhost:5544 with the CONTRACTS §11 DATABASE_URL." >&2
    exit 1
  fi
  echo "[dev-up] starting pgserver dev database on :5544 (backend/scripts/dev_db.py)"
  start_bg dev_db python backend/scripts/dev_db.py
  for _ in $(seq 1 60); do port_open 5544 && break; sleep 1; done
  port_open 5544 || { echo "[dev-up] ERROR: dev DB not up; see $RUN/dev_db.log" >&2; exit 1; }
fi

echo "[dev-up] migrating + seeding"
bash infrastructure/scripts/dev_migrate.sh
bash infrastructure/scripts/dev_seed.sh

echo "[dev-up] starting backend on http://127.0.0.1:8000"
start_bg backend bash infrastructure/scripts/dev_backend.sh
for _ in $(seq 1 30); do
  python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health/live',timeout=2)" 2>/dev/null && break
  sleep 1
done

echo "[dev-up] starting local job runner (CELERY_MODE=local)"
start_bg jobs bash infrastructure/scripts/dev_jobs.sh

echo "[dev-up] DONE. backend http://127.0.0.1:8000/docs | logs in $RUN/*.log | stop: bash infrastructure/scripts/dev_down.sh"
