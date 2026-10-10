# Capacity Exchange

A marketplace for idle capacity — sports courts, treatment rooms, machines, parking bays,
workshops — where a provider publishes bookable capacity with a real availability model and a
customer finds, holds and books it. Bookings are the property of the capacity engine: two
customers cannot buy the same unit, and a hold that expires frees the slot for the next person.

- **Backend** — FastAPI + SQLAlchemy async on PostgreSQL 16, Celery-or-local job runner, JWT auth
  with RBAC and organization tenancy.
- **Desktop** — Tauri + React + TypeScript (Windows installer).
- **Mobile** — Flutter (Android APK and Play app bundle), Persian and English, RTL-aware.
- **AI/Data** — deterministic, offline algorithms behind five read-only `/ai/*` routes. Nothing
  under `/ai/*` writes, and no booking path ever waits on it.

## Where the truth lives

| Question | Read |
| --- | --- |
| What are the API shapes, error codes, schema and concurrency protocol? | `docs/CONTRACTS.md` (binding) |
| What is built, by whom, and what is blocked? | `CURRENT_STATE.md`, `IMPLEMENTATION_PLAN.md` |
| How is this proven, and what is not proven? | `docs/TESTING_STRATEGY.md` |
| How is a release cut and deployed? | `docs/RELEASE.md` |
| What does it have to do, and why? | `MASTER_PROMPT.md`, `docs/PRODUCT_SPEC.md` |

## Requirements

Python 3.12, Node 22, Git, and either Docker **or** no Docker at all: the dev scripts run a real
PostgreSQL 16 through `pgserver` (embedded) so the suite never needs SQLite. Redis is optional —
without it the rate limiter uses an in-memory bucket and jobs run in-process. Flutter needs the
Android SDK only for APK builds.

## Run it without Docker

```
python -m pip install -i https://pypi.org/simple -e ./backend -e ./ai
cp docker/.env.example .env            # dev defaults; nothing secret
bash infrastructure/scripts/dev_up.sh  # pgserver DB on :5544 + migrate + seed + API + job runner
curl http://127.0.0.1:8000/health      # {"status":"ok","db":true,"redis":false,"version":"1.0.0"}
```

The API is on <http://127.0.0.1:8000>, docs at <http://127.0.0.1:8000/docs>. Everything
`dev_up.sh` started is stopped by `bash infrastructure/scripts/dev_down.sh`; individual steps are
`dev_migrate.sh`, `dev_seed.sh`, `dev_backend.sh`, `dev_jobs.sh`. Seeded accounts and their demo
password are listed in `docs/SEED_DATA.md`.

## Run it with Docker

```
cp docker/.env.example docker/.env
docker compose -f docker/docker-compose.yml up -d          # dev: publishes the API port
docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml up -d   # prod
```

The prod overlay publishes no database or Redis ports and requires real `SECRET_KEY` and
`WEBHOOK_SECRET`; the backend container runs `alembic upgrade head` at startup while worker and
beat skip it, so migrations cannot race.

## Clients

```
cd desktop && npm install
npm run dev                     # browser against the local API
npm run tauri dev               # the actual desktop window
npm run tauri build             # Windows installer (NSIS)

cd mobile && flutter pub get
flutter run -d <device> --dart-define=API_BASE_URL=http://10.0.2.2:8000/api/v1
flutter build apk --release --dart-define=API_BASE_URL=https://api.example.com/api/v1
```

`API_BASE_URL` is a compile-time define in both clients; an APK built without it points at the
emulator host, which is why the release workflow refuses to build one against an unset URL.

## Test it

```
bash infrastructure/scripts/test_backend.sh     # 97 tests, real PostgreSQL, no Redis required
cd ai && python -m pytest -q                    # 31 algorithm tests, no database at all
cd desktop && npx vitest run                    # 42 tests (npm test opens watch mode)
cd mobile && flutter test                       # 13 tests
```

With the API running, the live gate exercises the shipped path over HTTP:

```
python tests/e2e/journey.py          --base http://127.0.0.1:8000/api/v1   # 39 checks
python tests/e2e/concurrency_race.py --base http://127.0.0.1:8000/api/v1 --attempters 12
python tests/security/matrix.py      --base http://127.0.0.1:8000/api/v1 --rate-limit-per-min 120
```

The race is the one that matters most: 12 simultaneous holds against capacity 3 must grant
exactly 3 and refuse the rest with `no_availability`, and the provider's own workspace view must
agree. `.github/workflows/ci.yml` runs all of the above on every push; `release.yml` builds the
installers and signed Android artifacts for a `v*` tag.

## Agent operating rules

`AGENTS.md` defines directory ownership and the shared-file rules (migrations, API schemas, auth,
the booking state machine, DTOs, root config). `MASTER_PROMPT.md` is the task definition. A new
contributor follows: read the contracts, check `CURRENT_STATE.md`, claim a task, implement, test,
and hand off with files changed / contracts changed / migrations / tests run / known issues.
