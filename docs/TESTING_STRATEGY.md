# Testing Strategy

Testing is a gate, not a report: a layer that has not been run against the thing it claims to
cover is written here as **not proven**. Every command below is reproducible on a machine with
no Docker, no Redis and no network access to a model, and each one exits non-zero on its first
unsatisfied assertion.

## Layers

### 1. Algorithm layer — `ai/`

Pure functions over frozen dataclasses: no database driver, no HTTP client, no LLM, no clock
injected from outside. Run from `ai/`:

```
python -m pytest -q          # 31 passed
```

This layer is where prose-reading, pricing, matching, utilization and listing-draft rules are
pinned. It is deliberately the cheapest layer, so a rule can be tested without a server.

### 2. Backend API layer — `backend/`

Real PostgreSQL 16.2 through pgserver (embedded), schema created with `alembic upgrade head`.
**SQLite is never used** — the booking concurrency protocol depends on advisory locks, and a
sqlite-backed suite would pass while production double-books. Run:

```
bash infrastructure/scripts/test_backend.sh          # 97 passed
```

`REDIS_URL` is forced empty by the script: the suite must be green without Redis, which is also
the CI and offline-machine case. Covers auth/RBAC, capacity and availability, marketplace search,
the booking state machine, holds and idempotency, orders/payments/refunds, reviews/disputes,
dashboards, admin, notifications/conversations, matching and the five `/ai/*` routes.

`backend/tests/test_transaction_commit.py` is a boundary guard rather than a feature test: an ASGI
spy probes the database at the moment `http.response.start` is emitted, which is the exact instant
a client could follow a `201` with a `GET`. It demonstrably fails without
`backend/app/core/routing.py`, because FastAPI 0.141 runs a dependency-with-yield teardown *after*
the response has already left.

### 3. Client unit/component layer

```
desktop:  npx vitest run                 # 42 passed (13 of them the assistant surface)
mobile:   flutter test                   # 13 passed (8 domain + 5 widget)
```

Mobile resolves its packages into `PUB_CACHE`; the default on this machine points at a volume
that is not mounted, so the gate runs with it re-pointed at a writable drive. The widget tests
boot the real session flow — anonymous start lands on sign-in, sign-in goes through the API and
opens the tabs, a provider lands on the workspace, the language switch flips the catalog and the
direction, and a rejected sign-in shows the server's own message — so "the app builds" is not the
claim being tested.

Desktop tests assert the *honesty* rules of the advisory surfaces: a parsed sentence may only
write the filters it actually read, a listing draft may only merge into boxes the provider left
empty, and a figure is rendered with the axis it was measured on. `npm test` opens vitest in
watch mode — use `npx vitest run` in any scripted gate.

### 4. Live integration gate (the shipped path)

Requires the backend running (`bash infrastructure/scripts/dev_up.sh`, then
`infrastructure/scripts/dev_migrate.sh` and `dev_seed.sh`):

```
python tests/e2e/journey.py            --base http://127.0.0.1:8000/api/v1
python tests/e2e/concurrency_race.py   --base http://127.0.0.1:8000/api/v1 --attempters 12
python tests/security/matrix.py        --base http://127.0.0.1:8000/api/v1 --rate-limit-per-min 120
```

| Harness | Measured (re-run 2026-10-07) |
| --- | --- |
| `journey.py` | 39/39 checks — register → provider setup → publish → search → hold → confirm → pay → fulfillment → review, plus hold expiry, cancellation, cross-tenant denial |
| `concurrency_race.py` | PASS — capacity 3 (`--attempters 12`): 3 holds granted, 9 refused `no_availability`, server-side exactly 3 active bookings totalling 3 units |
| `matrix.py` | 33/33 — unauthenticated access, cross-tenant reads, role escalation, ownership, malformed/oversized payloads, injected and XSS payloads, idempotency misuse, refresh rotation, password storage, per-socket rate limiting |

These three are the only layer that can catch a defect the unit tests structurally cannot: the
commit-ordering bug (`GET /auth/me` returning 401 immediately after a successful `201`
registration) appeared in 8 of 14 live calls and in zero unit tests, because in-process tests
never cross the response boundary.

Harness rules learned the hard way and now enforced in the scripts themselves:

- fixtures are anchored to a fixed hour, never `datetime.now()`, so a 09:00–18:00 availability
  rule cannot be published after 18:00 and produce zero windows;
- every fixture call asserts its status before the check that depends on it, so a failed setup
  cannot let a later assertion pass vacuously;
- provider-side reads use `?provider=true`, and the race demands `len(active) == granted` and
  `total_qty == capacity` rather than "some rows exist";
- the rate-limit burst runs **last** (an emptied bucket would corrupt every earlier request) and
  sends `per_min + 15` so it genuinely exceeds the documented limit;
- a check that could not run is reported as a failure, never skipped.

## Mandatory booking behaviours

The booking engine is where a wrong answer costs money, so each of these is pinned by a named
check rather than by the suite passing overall:

| Behaviour | Proof |
| --- | --- |
| Two users take the same unit simultaneously | `concurrency_race.py` — 12 parallel attempters on capacity 3 grant exactly 3, refuse 9 with `no_availability`, and the org view shows 3 active bookings for 3 units |
| Hold expiration | `backend/tests/test_booking_api.py::test_expired_hold_cannot_be_booked`, plus `expire_due_holds` only touching `status = 'hold'` rows (journey asserts the hold is `cancelled`, not lost) |
| Duplicate requests | `test_hold_is_replayed_by_fingerprint_and_idempotency_key`; matrix: same key + same body replays the same booking, same key + different body is refused |
| Retry after a timeout | The replay above is the retry path — a client that lost the response can re-send and get the original booking, never a second one |
| Cancellation | `test_commerce_api.py` cancellation-policy bands + journey step and matrix "customer can cancel own booking" |
| Invalid state transitions | Six assertions on `invalid_state_transition`: confirming before the provider accepts, cancelling mid-service, a late fulfillment, re-accepting an already-accepted match, editing a closed demand, and reviewing an undelivered booking |

## Planned layer: performance

Not started, and the four measurements it owes are fixed here so the work stays honest:
marketplace search, availability queries, booking confirmation under contention, and dashboard
aggregation. Until they exist, no claim about scale is made anywhere in this repository.

## What is not proven

- **Docker topology** — no Docker on the development box; `docker/*.yml` is validated
  structurally, not by booting it. CI is the place it first actually runs.
- **Redis-backed rate limiting** — no Redis locally, so the fixed-window path is exercised only
  by unit-level reasoning; the in-memory token bucket is what the live matrix measured.
- **Brute-force lockout for existing accounts** — the matrix proves identical answers for
  unknown accounts and wrong passwords, and proves rate limiting, but there is no per-account
  lockout to test yet.
- **Dependency vulnerability scanning** — not wired into CI yet.
- **Data-at-rest encryption migration** — documented intent only, no test.
- **Mobile release artifact** — `assembleRelease` is blocked on this machine (the Gradle SDK
  home it is configured to use sits on an unmounted volume); the debug build, `analyze` and the
  test suite are what actually ran.
- **Flutter integration tests for discovery and booking** — the strategy asks for them; what
  exists is domain logic plus widget-level boot. A device-level journey has not been run.
- **Desktop page-level rendering** — the 42 tests cover the booking and assistant logic, the
  envelope handling and i18n. Navigation and form validation across whole pages are reviewed by
  hand, which the quality bar explicitly requires in addition to automation.

## Gate for a release

A release is cut only when layers 1–4 are green in the same run, the migration head is
single and at head, and every failure above is either fixed or recorded here as not proven.
The pipeline must fail honestly: no `|| true`, no `continue-on-error`, and no build problem
solved by deleting a feature, screen or native capability.
