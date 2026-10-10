# Current State

Last sync: 2026-10-10 (seed made clock-independent and re-runnable; live gate layers re-run green).

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
      `pytest -q` = 403 passed against pgserver PostgreSQL 16.2 (schema via `alembic upgrade
      head`, never SQLite), re-run 2026-10-10 with the `/ai/*` routes, the commit-ordering
      guard and the seed's clock sweep mounted.
- [x] Live e2e harness (tests/e2e/journey.py, tests/e2e/concurrency_race.py)
- [x] Demo seed that survives the clock it runs at: the refund-band window search enumerates
      every hour in the band (proved complete against an independent scan at all 7 × 24
      starting hours), a grade the calendar refuses is printed as `band skipped`, and a
      re-run against a database the job runner already decayed re-mints only what decayed.
      Details and numbers: docs/TESTING_STRATEGY.md, layer 4.
- [x] AI/data surface: `ai/capacity_ai` algorithms + five `/ai/*` routes. Deterministic and
      offline (no model, no network, no writes); the optional LLM enhancement is not
      implemented for v1 and no caller branches on one — docs/AI_SPEC.md states this per
      capability.
- [x] Desktop assistant surface wired: search parses prose, the capacity wizard starts from a
      description, and pricing shows the market band beside the provider's own field. All three
      are advisory — no panel can prefill what it did not read.
- [x] Integration gate run against a live backend on pgserver PostgreSQL 16.2, re-run 2026-10-10:
      `pytest` 403 (backend) + 31 (ai) + 13 (flutter) + 51 (vitest); live journey 39/39;
      concurrency race PASS (capacity 3, 12 attempters → exactly 3 grants, 3 units server-side);
      security matrix 33/33. Commands and the honest gaps are in docs/TESTING_STRATEGY.md.

- [x] Admin surface in the desktop client: seven platform-operations screens (analytics, users,
      providers, disputes, audit log, categories, promotions) over the tested `/admin/*` and
      `/dashboard/admin` APIs, gated by role in the route and in the sidebar. Proven per role in a
      real browser against a live server, and the gate itself is pinned by
      `desktop/src/__tests__/admin-access.test.tsx`.
- [x] Performance layer: `bash infrastructure/scripts/perf_gate.sh` — 8 measured p95s inside
      budget on a 40,000-booking corpus, with booking exactness asserted at volume and an
      `EXPLAIN (ANALYZE, BUFFERS)` printed for every measurement.

## In Progress
- [ ] Desktop Windows installer (Tauri build) — code and `npm run build` are green; the
      release binary is a CI job (see Blocked)
- [ ] Mobile Android signed release APK — same reason
- [ ] CI/CD build & release recovery — the workflows are tracked and the layers that found the
      bugs run in CI; what is left is a green live gate on the seed fixes, then the `v1.0.0` tag
      and the release per docs/RELEASE.md

## Blocked
- Mobile Android release build: the Gradle SDK home and `GRADLE_USER_HOME` this project is
  configured to use (`G:\SDK`, `G:\gradle-home`) are on a volume that is not mounted, so
  `assembleRelease` cannot resolve the NDK/SDK. `flutter test` runs once `PUB_CACHE` is
  re-pointed at a mounted drive (`E:\pub-cache` — 13 tests pass), so the Dart side is proven;
  only the signed APK is blocked. The keystore itself is gitignored and must come from release
  storage.
- Desktop Windows installer: `cargo` release linking is OOM-killed on this box and F: has ~2 GB
  free after the target directory was cleared, so the MSI is cut in CI rather than here. The
  TypeScript side builds (`npm run build`) and its 51 tests pass.

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
Every phase in `IMPLEMENTATION_PLAN.md` now has evidence: the performance layer is measured and the
admin surface is proven in a browser (see the two handoffs below). What is left is the build phase
per the CI/CD protocol: let CI produce the two artifacts this box cannot (Windows installer, signed
APK), and cut the release from a run that fails honestly — no `|| true`, no `continue-on-error`, no
deleted feature to make a build pass.

## Handoff — performance layer (performance/release) (2026-10-11)
- Files changed: `tests/perf/volume.py` (new), `tests/perf/bench.py` (new),
  `infrastructure/scripts/perf_api.py` (new), `infrastructure/scripts/perf_gate.sh` (new),
  `docs/TESTING_STRATEGY.md` (layer 5 replaces "planned, not started"), `README.md`,
  `IMPLEMENTATION_PLAN.md` (phase 14 [x], phase 2 evidence corrected to the suite that runs).
- APIs/contracts changed: none. No route, schema or error code moved; the backend suite was not
  touched and still passes unchanged.
- Migrations added: none. The gate builds its own `capacity_perf` database through `alembic
  upgrade head`, so it cannot be confused with seeded demo data.
- Tests run: the gate end to end at 60 samples — 8 of 8 p95 inside budget, contention proven by
  assertion (12 parallel holds on 6 free units → 6 grants, 6 `no_availability`, 6/6 conversions,
  window then full, 13th refused). Numbers, EXPLAIN evidence and the limits of the claim are in
  `docs/TESTING_STRATEGY.md`.
- Known issues: `search?from&to` answers in ~700 ms because it fans out ~2,400 tiny queries over
  the candidate page; inside its budget, and the honest fix (bulk-load the page's definitions,
  rules and commitments) touches the shared availability engine, so it is recorded not done. The
  local job runner in the API process logs a ProactorEventLoop warning per job on Windows; the
  runner itself works and no endpoint depends on it.
- Next dependent task: phase 12, the admin surface in `desktop/`.

## Handoff — platform overview screen (desktop) (2026-10-11)
- Files changed: `desktop/src/features/admin/pages/AdminAnalyticsPage.tsx` (new), `desktop/src/App.tsx`
  (route), `desktop/src/components/AppLayout.tsx` (sidebar link), `desktop/src/i18n/locales/{en,fa}.ts`,
  `desktop/src/__tests__/admin-access.test.tsx` (the link list the gate must show), and the phase-12
  records in `docs/TESTING_STRATEGY.md`, `IMPLEMENTATION_PLAN.md`, `CURRENT_STATE.md`.
- APIs/contracts changed: none. `/dashboard/admin` and `GET /admin/analytics` were already specified
  in `docs/CONTRACTS.md` §8 and already tested in `backend/tests/`; this screen consumes them.
- Migrations added: none.
- Tests run: `npx vitest run` → 51 passed, `tsc -b` exit 0, `npm run build` green. In a real browser
  against the seeded backend an admin renders 11 cards and 39 table rows with live figures, support
  is redirected and does not see the link, a customer is redirected; changing the window moved the
  window-owned numbers (100 bookings created → 15, $18,645.80 → $5,193.00) while the all-time cards
  stayed, which is what separates a live read from a painted constant.
- Known issues: the analytics DTO carries no currency, so its money is rendered on the axis the
  platform totals report — correct while the platform settles in one currency, and the first thing
  to revisit if that stops being true. The browser pass is a throwaway driver, as recorded in §4b.
- Next dependent task: #11 — the release.

## Handoff — admin surface, proven per role (desktop/QA) (2026-10-11)
- Files changed: `desktop/src/__tests__/admin-access.test.tsx` (new, 9 tests), `README.md`,
  `IMPLEMENTATION_PLAN.md` (phase 12 [x], phase 7 evidence count), `docs/TESTING_STRATEGY.md`
  (layer 3 count and the new §4b browser record), `CURRENT_STATE.md`. The screens themselves
  (`desktop/src/features/admin/pages/*.tsx`), the `/admin/*` client in `desktop/src/api/endpoints.ts`,
  the `RequireRole` gate and the role-filtered sidebar were already committed; this task proved them
  rather than adding to them.
- APIs/contracts changed: none. No route, DTO or role definition moved; the backend was not touched.
- Migrations added: none.
- Tests run: `npx vitest run` → 51 passed (42 before: 9 new cases pin the gate — a platform admin
  reaches all seven screens, a support account reaches the dispute queue and nothing else, a customer
  reaches none, and the sidebar offers exactly the links the guard allows). `tsc -b` exit 0. Then
  every screen was opened in a real browser against the live API on the seeded demo database and the
  rows it rendered were counted per role (§4b of `docs/TESTING_STRATEGY.md`).
- Known issues: the browser pass was driven by a throwaway CDP script on this box, not a committed
  harness, so it is evidence for today's SHA rather than a CI gate — the nine vitest tests are what
  CI can re-run. Mobile has no admin surface; support and platform operations are desktop-only by
  design.
- Next dependent task: #11 — the release. Everything the platform-operations phase needed was proven;
  the only phases still `[~]` (9, 10) are waiting on CI to produce the installer and the signed APK.

## Handoff — seed clock independence (backend) (2026-10-10)
- Files changed: `backend/scripts/seed.py` (`_clock_window` enumerates the whole hour range,
  `_late_window`/`_late_cancellation` for the closed-window 0% grade, `POLICY_WIDE`/
  `POLICY_NARROW`/`REFUND_BANDS` constants, `Ctx.bands_skipped`, per-phase status table so a
  re-run re-mints only what decayed, catalog and review-average reads scoped to seeded rows),
  `backend/tests/test_seed_windows_are_clock_independent.py` (new), `docs/TESTING_STRATEGY.md`,
  `CURRENT_STATE.md`.
- Contracts changed: none. `docs/CONTRACTS.md` §9 promises journeys, statuses and idempotency,
  and the seed still meets it; the refund-band demonstration is now reported when the calendar
  refuses it instead of aborting the run.
- Migrations added: none.
- Tests run: backend `pytest -q` → 403 passed (306 of them the new clock sweep, 1m32s); the
  search was checked against an independent oracle at 504 starting clocks; `seed.py --reset
  --verify` on a fresh schema, plus a fresh schema whose whole-stack clock was moved to Friday
  22:30 UTC (9.5s, eight §5.5 statuses, review average 4.2, 7160 cents refunded); live journey
  39/39, race 3 grants of 12 attempters, security matrix 33/33 over HTTP afterwards.
- Known issues: the 50% band cannot be demonstrated between Friday 21:37 and Saturday 00:00
  (9 of 504 clocks) — the rest of Saturday starts inside 24 hours and Monday past 48, so no
  window grades that way. The seed prints `band skipped: 50% over 24-48h ...` rather than
  faking it, and still raises if no band above 0% can be shown.
- Next dependent task: #11 — a green live gate on this SHA, then the `v1.0.0` tag.

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
