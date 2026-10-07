# Current State

Last sync: 2026-10-07 (integration gate re-run green end to end; docs and handoffs below).

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
      `pytest -q` = 97 passed against pgserver PostgreSQL 16.2 (schema via `alembic upgrade
      head`, never SQLite), re-run 2026-10-07 with the `/ai/*` routes and the commit-ordering
      guard mounted.
- [x] Live e2e harness (tests/e2e/journey.py, tests/e2e/concurrency_race.py)
- [x] AI/data surface: `ai/capacity_ai` algorithms + five `/ai/*` routes. Deterministic and
      offline (no model, no network, no writes); the optional LLM enhancement is not
      implemented for v1 and no caller branches on one — docs/AI_SPEC.md states this per
      capability.
- [x] Desktop assistant surface wired: search parses prose, the capacity wizard starts from a
      description, and pricing shows the market band beside the provider's own field. All three
      are advisory — no panel can prefill what it did not read.
- [x] Integration gate run against a live backend on pgserver PostgreSQL 16.2, 2026-10-07:
      `pytest` 97 (backend) + 31 (ai) + 13 (flutter) + 42 (vitest); live journey 39/39;
      concurrency race PASS (capacity 3, 12 attempters → exactly 3 grants, 3 units server-side);
      security matrix 33/33. Commands and the honest gaps are in docs/TESTING_STRATEGY.md.

## In Progress
- [ ] Desktop Windows installer (Tauri build) — code and `npm run build` are green; the
      release binary is a CI job (see Blocked)
- [ ] Mobile Android signed release APK — same reason
- [ ] CI/CD build & release recovery — `.github/workflows/` is authored but still untracked
      until the gate below validates it
- [ ] Admin surface: the platform-operations APIs are tested; no client screen exists yet

## Blocked
- Mobile Android release build: the Gradle SDK home and `GRADLE_USER_HOME` this project is
  configured to use (`G:\SDK`, `G:\gradle-home`) are on a volume that is not mounted, so
  `assembleRelease` cannot resolve the NDK/SDK. `flutter test` runs once `PUB_CACHE` is
  re-pointed at a mounted drive (`E:\pub-cache` — 13 tests pass), so the Dart side is proven;
  only the signed APK is blocked. The keystore itself is gitignored and must come from release
  storage.
- Desktop Windows installer: `cargo` release linking is OOM-killed on this box and F: has ~2 GB
  free after the target directory was cleared, so the MSI is cut in CI rather than here. The
  TypeScript side builds (`npm run build`) and its 42 tests pass.

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
Everything that can run on this machine has been run and is green. The next step is the build
phase per the CI/CD protocol: commit `.github/workflows/`, let CI produce the two artifacts this
box cannot (Windows installer, signed APK), and cut the release from a run that fails honestly —
no `|| true`, no `continue-on-error`, no deleted feature to make a build pass. After that:
performance (phase 14) and the admin screen (phase 12) are the only phases left with no evidence.

## Handoff — Desktop assistant surface (2026-10-07)
- Files changed: `desktop/src/features/assistant/{logic.ts,hooks.ts,components/AssistantCards.tsx}`,
  `desktop/src/pages/{SearchPage,CapacityWizardPage}.tsx` (prose search, wizard draft prefill,
  price band beside the provider's field), `desktop/src/types/api.ts`, `desktop/src/i18n/*`,
  `desktop/src/__tests__/assistant.test.tsx`.
- Contracts changed: none — the client consumes the §8 `/ai/*` shapes as documented. One rule
  was added to `docs/CONTRACTS.md` §8 reasoning: a parsed sentence may only write the filters it
  actually read.
- Migrations added: none.
- Tests run: `npx vitest run` → 42 passed (13 new assistant cases: mapping honesty, draft merge
  into empty boxes only, panels render only when data exists, money shown with its axis).
- Known issues: the wizard's `source_phrase` labels were renamed with the AI field change, so an
  older draft response leaves the day chips unlabelled rather than wrong.
- Next dependent task: none blocking; the mobile client may surface `/ai/copilot` when it wants
  a provider briefing.

## Handoff — AI prose reading + price ladder (2026-10-07)
- Files changed: `ai/capacity_ai/{semantic_query,listing_draft}.py`,
  `ai/tests/test_algorithms.py`, `backend/app/api/v1/assistant.py` (`_tiers`, `_scope`).
- Contracts changed: `/ai/price-suggest` `rationale` now names the scope it measured on
  (`"<category> per <unit>"` or `"<category>, any billing unit"`); no field added or removed.
- Migrations added: none.
- Tests run: `pytest -q` in `ai/` → 31 passed (9 new). Re-proven through HTTP on the live
  backend for the four phrasings the unit tests had missed.
- Known issues: a day-span crossed with several ranges over-generates slots (recorded in
  `docs/AI_SPEC.md`, mitigated by `source_phrase` + provider confirmation); no seasonality.
- Next dependent task: none.

## Handoff — write/commit ordering (backend correctness) (2026-10-07)
- Files changed: `backend/app/core/routing.py` (new `TransactionalRoute`),
  `backend/app/core/db.py`, and `route_class=` on the 16 routers in `backend/app/api/v1/`;
  `backend/tests/test_transaction_commit.py` (new).
- Contracts changed: `docs/CONTRACTS.md` §2 gained the **read-your-write** rule — a write's
  transaction commits before its response leaves the handler, and a commit that fails is a 5xx,
  never a 201 that is then rolled back.
- Migrations added: none.
- Tests run: backend `pytest -q` → 97 passed; live probe of `POST /auth/register` → `GET
  /auth/me` went from 6/14 lost-and-late to 42/42 visible. The new test fails without
  `routing.py`, which is the point of it: FastAPI 0.141 runs a dependency-with-yield teardown
  *after* the response is sent, and its lazy `include_router` means `route_class` must be set on
  each leaf router at decoration time.
- Known issues: `uvicorn --reload` did not fire for these `backend/` edits under nohup, so a
  stale worker kept serving the old code until it was killed — restart, don't trust, the
  reloader on this box.
- Next dependent task: CI must run the live gate, not only the in-process suite, because no unit
  test crosses the response boundary.

## Handoff — live harness corrections (QA/Security) (2026-10-07)
- Files changed: `tests/e2e/journey.py`, `tests/e2e/concurrency_race.py`,
  `tests/security/matrix.py`.
- Contracts changed: none; the harnesses were corrected to test the contract as written —
  `invalid_credentials` is 409 by design, and unknown-account and wrong-password answers must be
  byte-identical.
- Migrations added: none.
- Tests run: journey 39/39, race PASS, matrix 33/33 (all re-run on 2026-10-07 after the backend
  fix).
- Known issues, all of them previously *passing vacuously*: fixtures were anchored to
  `datetime.now()` (a 09:00–18:00 rule published after 18:00 yields zero windows); the journey
  followed the consumed hold's id instead of the new booking id, so a conversion looked like a
  cancellation; the cross-tenant check never authenticated the stranger; the race omitted
  `provider=true`, so its server-side count read zero rows and "passed"; the burst ran mid-suite
  with 90 requests under the documented 120 limit. Rules now enforced in the scripts are written
  up in `docs/TESTING_STRATEGY.md`.
- Next dependent task: port these three harnesses into the CI job so a vacuous pass cannot
  survive a head change.

## Handoff — docs (this sync) (2026-10-07)
- Files changed: `IMPLEMENTATION_PLAN.md` (evidence table), `docs/CONTRACTS.md` (§2 read-your-write,
  409 credentials), `docs/SECURITY_SPEC.md` (controls the live matrix proves + "Not yet proven"),
  `docs/AI_SPEC.md` (price ladder, prose rules, slot cross-join limitation),
  `docs/TESTING_STRATEGY.md` (layers, commands, measured numbers, mandatory booking behaviours),
  `CURRENT_STATE.md`.
- Contracts changed: documentation only; no schema or route moved.
- Migrations added: none.
- Tests run: every number quoted in these documents was re-measured on 2026-10-07.
- Known issues: the security spec records four controls with no test yet (per-account lockout,
  dependency scanning in CI, client crawl, transport-level body size).
- Next dependent task: #11 CI/CD build & release.

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
