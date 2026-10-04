#!/bin/sh
# backend-entrypoint.sh — migrate-then-serve for the backend image.
# worker/beat services set SKIP_MIGRATIONS=1 so only one container
# runs `alembic upgrade head` at startup (avoids migration races).
set -e

cd /app/backend

if [ "${SKIP_MIGRATIONS:-0}" != "1" ]; then
    echo "[entrypoint] alembic upgrade head"
    alembic upgrade head
else
    echo "[entrypoint] SKIP_MIGRATIONS=1 — skipping alembic"
fi

exec "$@"
