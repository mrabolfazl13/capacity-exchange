#!/usr/bin/env bash
# perf_gate.sh — the performance layer end to end: volume dataset, a live API against it, and
# the four measurements docs/TESTING_STRATEGY.md names. Exits non-zero on a budget breach.
#
#   bash infrastructure/scripts/perf_gate.sh                # scale 1.0 (40k bookings)
#   PERF_SCALE=0.2 bash infrastructure/scripts/perf_gate.sh # quick local pass
#
# Needs the pgserver cluster on :5544 (start it with infrastructure/scripts/dev_up.sh; the
# script says so rather than starting a database behind your back).
set -euo pipefail
cd "$(dirname "$0")/../.."
RUN=.run; mkdir -p "$RUN"
[ -f .env ] && { set -a; . ./.env; set +a; }

DB="${PERF_DB:-capacity_perf}"
SCALE="${PERF_SCALE:-1.0}"
PORT="${PERF_PORT:-8010}"
DSN="postgresql://capacity:capacity@127.0.0.1:5544/${DB}"

if ! (exec 3<>/dev/tcp/127.0.0.1/5544) 2>/dev/null; then
  echo "[perf] ERROR: no PostgreSQL on 127.0.0.1:5544." >&2
  echo "[perf] start the dev cluster first: bash infrastructure/scripts/dev_up.sh" >&2
  exit 1
fi

echo "[perf] building the volume dataset ($DB, scale=$SCALE)"
python tests/perf/volume.py --database "$DB" --scale "$SCALE"

# The limiter must stay on the measured path, but a bench sends far more than a browser would,
# so its per-minute capacity is raised for this process only. No product default changes.
export DATABASE_URL="postgresql+psycopg://capacity:capacity@127.0.0.1:5544/${DB}"
export CELERY_MODE=local APP_ENV=dev REDIS_URL="" RATE_LIMIT_PER_MIN=1000000
export PYTHONPATH="$PWD/ai${PYTHONPATH:+:$PYTHONPATH}"

echo "[perf] booting the API on :$PORT against $DB (job runner deliberately not started)"
# Not `python -m uvicorn`: on Windows that hard-codes a proactor loop and psycopg3 refuses it,
# so every request would 500. infrastructure/scripts/perf_api.py sets the selector policy first.
nohup python infrastructure/scripts/perf_api.py --port "$PORT" \
  > "$RUN/perf_api.log" 2>&1 &
API_PID=$!
trap 'kill "$API_PID" 2>/dev/null || true' EXIT

for _ in $(seq 1 60); do
  if python -c "import urllib.request;urllib.request.urlopen( \
      'http://127.0.0.1:${PORT}/health/live', timeout=2)" 2>/dev/null; then
    break
  fi
  sleep 1
done
python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:${PORT}/health/live', \
    timeout=5)" || { echo "[perf] ERROR: API did not come up; see $RUN/perf_api.log" >&2; exit 1; }

echo "[perf] measuring"
python tests/perf/bench.py --base "http://127.0.0.1:${PORT}/api/v1" --dsn "$DSN"
echo "[perf] done"
