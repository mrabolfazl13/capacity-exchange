# Current State

Last sync: 2026-10-07 (AI/Data `/ai/*` surface landed; see Handoff below).

## Status
Implementation started. Shared contracts authored (docs/CONTRACTS.md); parallel
domain agents building against them. Machine reality: no Docker/Redis — dev DB is
pgserver (embedded PostgreSQL), jobs run via CELERY_MODE=local fallback.

## Completed
- [x] Product concept
- [x] Technology stack
- [x] Agent operating model
- [x] Documentation pack
- [x] Shared contracts: docs/CONTRACTS.md (schema, API envelope, RBAC, booking
      concurrency protocol, idempotency, env, seed, testing contracts)
- [x] Contract validation on the real engine (pgserver PostgreSQL 16.2): no contrib
      extensions → advisory-lock protocol is the authoritative concurrency guard
      (docs/CONTRACTS.md §5.5, §14)
- [x] Docker topology + docker-free dev scripts (docker/, infrastructure/scripts/)
- [x] Backend core + domains: identity/RBAC, capacity/availability, marketplace, booking
      engine, orders/payments, reviews/disputes, dashboards, admin, seed. Evidence:
      `pytest -q` = 94 passed against pgserver PostgreSQL 16.2 (schema via `alembic upgrade
      head`, never SQLite), re-run 2026-10-07 with the `/ai/*` routes mounted.
- [x] Live e2e harness (tests/e2e/journey.py, tests/e2e/concurrency_race.py)
- [x] AI/data surface: `ai/capacity_ai` algorithms + five `/ai/*` routes. Deterministic and
      offline (no model, no network, no writes); the optional LLM enhancement is not
      implemented for v1 and no caller branches on one — docs/AI_SPEC.md states this per
      capability.

## In Progress
- [ ] Desktop app (Tauri+React+TS, vitest, vite build) — agent, desktop/
- [ ] Mobile app completion (Flutter) — agent, mobile/
- [ ] QA/security pass, integration gate, CI/CD build & release recovery

## Blocked
- Mobile Android release build: the Gradle SDK home this project is configured to use
  (`G:\SDK`, `GRADLE_USER_HOME=G:\gradle-home`) is on a volume that is not mounted on this
  machine, so `assembleRelease` cannot resolve the NDK/SDK. Everything else in the Flutter
  app (analyze, debug build, icon/permission/keystore config) is landed; the signing
  keystore itself is gitignored and must come from release storage.

## Agent incidents (operational note)
The session that carried the first agent batch was restarted; the backend and AI
agents were killed by an idle-stream timeout and the mobile agent mid-run, leaving
partial output (backend/ and ai/ empty, mobile/ had api+core only). Mitigation now
in force for every delegated agent: write files to disk incrementally (one per tool
call), run one heavy process at a time (RAM ~1GB free with 4 agents), and keep the
build/bundle steps for the main-agent build phase. Infra output survived and is
committed.

## Active Agents
- desktop → desktop/
- mobile → mobile/
- main agent → contracts, e2e harness, CI, integration gate, docs

## Next Sync Point
AI/Data is landed, so the remaining backend-side surface is the integration gate: boot the
API, run migrations + seed + the live e2e journey, then the QA/security pass and the
build/release phase per the attached CI/CD protocol (`.github/workflows/` is still untracked
until that gate validates it).

## Handoff — AI/Data (2026-10-07)
- Files changed: `ai/` (new package: `pyproject.toml`, `capacity_ai/{__init__,db,schemas,
  semantic_query,pricing,insights,listing_draft}.py`, `tests/test_algorithms.py`);
  `backend/app/services/assistant.py`, `backend/app/api/v1/{assistant,router}.py`,
  `backend/tests/{conftest,test_assistant_api}.py`; `docker/Dockerfile.backend`,
  `infrastructure/scripts/dev_backend.sh`; `docs/CONTRACTS.md`, `docs/AI_SPEC.md`.
- Contracts changed: five `/ai/*` response shapes documented in CONTRACTS §8 (previously
  listed as routes that "may 503"); copilot money is cash-dated while dashboard revenue stays
  service-dated, and both are labelled. No request or response field was removed.
- Migrations added: none. This surface is read-only.
- Tests run: `pytest -q` in `backend/` → 94 passed (9 of them new `/ai/*` cases);
  `pytest -q` in `ai/` → 22 passed. Import path proved by hiding the editable install of
  `capacity_ai` and re-running the backend suite green.
- Known issues: `infrastructure/scripts/dev_jobs.sh` advertises `ai:*` job handlers that do
  not exist (no AI job is registered; the `/ai/*` routes are synchronous reads); pricing
  ignores seasonality/time-of-year, which docs/AI_SPEC.md now states rather than implies;
  copilot suggestions need ≥2 observed weeks before they name an idle block, so a brand-new
  account sees "no recurring idle block stands out yet".
- Next dependent task: the desktop client can wire the wizard to `POST /ai/listing-draft`
  and search to `POST /ai/parse-search`; the integration gate (#9) owns the live journey, and
  CI (#11) must run `pytest` in both `backend/` and `ai/`.

## Known Risks
- Booking concurrency
- Flexible capacity modeling
- Cross-platform build dependencies
- Payment-provider differences
- Geospatial search scalability
