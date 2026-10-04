#!/usr/bin/env bash
# dev_jobs.sh — run the LOCAL in-process job runner (CONTRACTS §10).
# This machine has NO Redis: with CELERY_MODE=local the same job handlers
# (expire_holds, send_notifications, rebuild_offer_search,
# aggregate_analytics, cleanup_idempotency, ai:*) run in an asyncio loop
# from backend/jobs/local_runner.py. FOREGROUND; Ctrl+C to stop.
set -euo pipefail
cd "$(dirname "$0")/../.."
[ -f .env ] && { set -a; . ./.env; set +a; }
export DATABASE_URL="${DATA…city}"
export CELERY_MODE=local
export REDIS_URL=""   # deliberate: no Redis binary on this dev box

cd backend
if [ ! -f jobs/local_runner.py ]; then
  echo "[jobs] ERROR: backend/jobs/local_runner.py not found (owned by backend agent, CONTRACTS §10)." >&2
  exit 1
fi
echo "[jobs] starting local job runner (CELERY_MODE=local, DB-only)"
exec python -m jobs.local_runner "$@"
