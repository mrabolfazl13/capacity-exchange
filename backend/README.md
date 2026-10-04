# Capacity Exchange — Backend

FastAPI + SQLAlchemy 2.0 (async) + PostgreSQL 16 (pgserver embedded on this box).
Binding contract: `docs/CONTRACTS.md` (envelope §2, auth §3, RBAC §4, schema §5, env §11, testing §13).

## Layout

```
app/
  core/        config (pydantic-settings), db, errors+handlers, security, deps, ratelimit, audit, jobs registry
  models/      SQLAlchemy 2.0 declarative models (add new domain model modules + import in __init__.py)
  schemas/     Pydantic request/response DTOs (snake_case per §1)
  services/    business logic (auth service here; domain agents add their own modules)
  api/v1/      routers; every router is mounted with ONE line in app/api/v1/__init__.py
  workers/     celery_app.py (Celery object; inert without Redis)
  main.py      create_app(): middleware, error handlers, health, /api/v1 mount
jobs/
  local_runner.py  in-process asyncio job loop (CELERY_MODE=local; `python -m jobs.local_runner`)
alembic.ini, migrations/   schema; 0001_core_schema matches CONTRACTS §5 identity/catalog tables
scripts/
  dev_db.py    start/stop persistent dev pgserver on :5544 (capacity/capacity/capacity) in .pgdata/
tests/         pytest + pytest-asyncio against a real pgserver DB `capacity_test` (never SQLite)
```

## Dev commands (from backend/, Windows + Git Bash)

```bash
python scripts/dev_db.py                 # start dev Postgres on :5544 (idempotent; prints DATABASE_URL)
python scripts/dev_db.py --stop          # stop it
python -m alembic upgrade head           # apply schema (needs dev_db running)
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
python -m jobs.local_runner              # background jobs without Redis (CELERY_MODE=local)
```

Or the full stack from repo root: `bash infrastructure/scripts/dev_up.sh` (and `dev_down.sh`).

## Test commands

```bash
python -m pytest -q                 # all green, offline, real pgserver test DB
python -m pytest -q -m "not slow"   # skip migration round-trip
```

Tests boot ONE ephemeral pgserver on a random free port, run `alembic upgrade head` into
`capacity_test`, truncate between tests, and talk to the app over ASGI (httpx) — no ports.

## Adding a domain (instructions for the follow-up agent)

1. Models: create `app/models/<domain>.py`, import it in `app/models/__init__.py` (one line).
2. Migration: add `migrations/versions/000X_<domain>.py` chained after `0001_core_schema`.
3. Schemas/services: `app/schemas/<domain>.py`, `app/services/<domain>.py`.
4. Router: `app/api/v1/<domain>.py` exposing `router = APIRouter()`; mount it with ONE line in
   `app/api/v1/__init__.py` (see the marked MOUNT POINTS block).
5. Booking writes MUST follow CONTRACTS §5.5 (advisory lock + in-lock revalidation, same tx).
6. Jobs: register async handlers in `app/core/jobs.py` (used by both local runner and Celery).
