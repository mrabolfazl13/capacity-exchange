#!/usr/bin/env bash
# clean.sh — remove LOCAL runtime artifacts: .run/ (pids+logs), __pycache__,
# pytest/mypy/ruff caches, backend alembic/pyc leftovers, frontend build output.
# Does NOT delete databases. For Docker volumes:
#   docker compose -f docker/docker-compose.yml down -v   (on a Docker host)
set -euo pipefail
cd "$(dirname "$0")/../.."

echo "[clean] removing .run/ (dev_up/dev_down logs and pid files)"
rm -rf .run

echo "[clean] removing Python caches under backend/ ai/ tests/"
for d in backend ai tests; do
  [ -d "$d" ] || continue
  find "$d" -type d \( -name __pycache__ -o -name .pytest_cache -o -name .mypy_cache -o -name .ruff_cache \) \
       -prune -exec rm -rf {} + 2>/dev/null || true
done

echo "[clean] removing desktop web build output (rebuild with npm --prefix desktop run build)"
[ -d desktop/web/dist ] && rm -rf desktop/web/dist && echo "[clean] removed desktop/web/dist"

echo "[clean] removing Flutter build dirs (rebuild on next flutter build)"
for d in mobile/build mobile/.dart_tool; do
  [ -d "$d" ] && rm -rf "$d" && echo "[clean] removed $d"
done

echo "[clean] OK — dev DB data is NOT deleted (pgserver data dir is managed by backend/scripts/dev_db.py)"
