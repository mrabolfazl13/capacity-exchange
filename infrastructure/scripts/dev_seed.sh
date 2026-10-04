#!/usr/bin/env bash
# dev_seed.sh — load demo data (CONTRACTS §9: backend/scripts/seed.py).
# Password for ALL seeded accounts: Demo1234!   (dev only).
# Pass-through args, e.g.:  bash dev_seed.sh --reset
set -euo pipefail
cd "$(dirname "$0")/../.."
[ -f .env ] && { set -a; . ./.env; set +a; }
export DATABASE_URL="${DATA…city}"

if [ ! -f backend/scripts/seed.py ]; then
  echo "[seed] ERROR: backend/scripts/seed.py not found (owned by backend agent)." >&2
  exit 1
fi

echo "[seed] running backend/scripts/seed.py $* (idempotent upserts; --reset to rebuild)"
python backend/scripts/seed.py "$@"
echo "[seed] OK — demo accounts: see docs/SEED_DATA.md and CONTRACTS §9 (password Demo1234!)"
