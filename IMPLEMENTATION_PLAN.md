# Implementation Plan

Status markers: [~] in progress, [x] done, [ ] not started. See CURRENT_STATE.md
for owners and docs/CONTRACTS.md for the binding cross-agent contract.

A phase is [x] only when something runs and proves it — the evidence column names the
check, not the intention.

| Phase | Scope | Status | Verified by |
|---|---|---|---|
| 0 | Repo, docs, environment, Docker, CI baseline | [~] | dev scripts boot the stack on a box with no Docker; `.github/workflows/` is authored but untracked until the CI gate validates it |
| 1 | Architecture, contracts, error model, schema | [x] | docs/CONTRACTS.md is the authority every suite asserts against |
| 2 | Backend core: app, auth, RBAC, migrations, jobs | [x] | 97 backend tests; single alembic head at 0001; local job runner logs five handlers |
| 3 | Capacity engine: resources, units, availability | [x] | E2E journey steps [2] — draft resource, definition, recurring rule, `/availability/free` windows |
| 4 | Marketplace: offers, search, filters, detail | [x] | journey step [3] — published offer discovered by q/city/date/quantity filters |
| 5 | Booking engine: holds, state machine, concurrency | [x] | race: 12 parallel holds against capacity 3 grant exactly 3, and the provider's org view shows 3 holds / 3 units |
| 6 | Orders and payments: lifecycle, mock provider, refunds | [x] | journey step [4] — order, intent, confirm, booking reads `paid`; cancellation policy bands tested |
| 7 | Provider experience: wizard, offers, dashboard | [x] | desktop suite (42 tests) plus the live `/ai/*` draft and price band wired into the wizard |
| 8 | Customer experience: discovery, booking, reviews | [x] | journey steps [3]–[6] — booking, review, dashboards, notifications, RBAC denials |
| 9 | Desktop: Tauri + React production integration, Windows build | [~] | `npm run build` green; the Windows installer is not produced on this box (Rust release build needs more free disk than F: has) |
| 10 | Mobile: Flutter app + Android release | [~] | 13 flutter tests pass (8 domain + 5 widget) and the debug build lands; `assembleRelease` needs the Gradle SDK home on an unmounted volume, so the signed APK is a CI job |
| 11 | AI/Data: search, draft, pricing, insights, copilot | [x] | 31 offline algorithm tests + 10 `/ai/*` endpoint tests; prose fixes re-proven through HTTP |
| 12 | Admin: platform operations | [~] | admin/promotions/roles APIs are tested in `backend/tests/`; there is no admin screen in either client |
| 13 | QA/Security: suites and live verification | [x] | security matrix 33/33 against a running server; error taxonomy, tenant isolation, rotation, burst limiting |
| 14 | Performance: load, indexes, query budgets | [ ] | not started |
| 15 | Release: artifacts, changelog, tags | [ ] | blocked on 9, 10 and the CI gate |

## Phase 0 — Foundation  [~]
Repository, documentation, environment, Docker, CI baseline.
Local reality: no Docker on dev box → pgserver-based dev DB scripts + compose files
validated structurally (infrastructure/scripts/, docker/).

## Phase 1 — Architecture  [x]
Domain boundaries (docs/ARCHITECTURE.md), API conventions + error model + full DB
schema pinned in docs/CONTRACTS.md.

## Phase 2 — Backend Core  [x]
FastAPI, PostgreSQL (pgserver locally / postgres:16 in compose), Redis optional,
Celery-or-local job runner, auth, RBAC, migrations.
Write ordering is contractual, not incidental: a transaction commits before its response
leaves the handler (§2), so a client can always read back what it just created.

## Phase 11 — AI/Data  [x]
Deterministic, offline algorithms in `ai/capacity_ai` over read-only backend adapters.
Advisory only — nothing under `/ai/*` writes, and no booking path waits on it (§12).
