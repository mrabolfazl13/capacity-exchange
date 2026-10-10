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
bash infrastructure/scripts/test_backend.sh          # 403 passed in 3m22s
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
desktop:  npx vitest run                 # 51 passed (13 assistant, 9 the admin RBAC gate)
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

Nine of them pin the gate in front of the moderation surface instead of the screens: a
`platform_admin` reaches a platform-only route, a `support` session is redirected off it and
still reaches the dispute queue, an ordinary account reaches neither, and the sidebar renders the
admin group as seven links for a platform admin, exactly `Disputes` for support, and nothing for a
customer. That is the part of phase 12 a scripted CI run can actually prove; the screens rendering
real rows is proven one layer down, in a browser.

### 4. Live integration gate (the shipped path)

Requires the backend running (`bash infrastructure/scripts/dev_up.sh`, then
`infrastructure/scripts/dev_migrate.sh` and `dev_seed.sh`):

```
python tests/e2e/journey.py            --base http://127.0.0.1:8000/api/v1
python tests/e2e/concurrency_race.py   --base http://127.0.0.1:8000/api/v1 --attempters 12
python tests/security/matrix.py        --base http://127.0.0.1:8000/api/v1 --rate-limit-per-min 120
```

| Harness | Measured (re-run 2026-10-10, against a dataset re-seeded the same hour) |
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

#### The demo seed and the clock

`backend/scripts/seed.py` pins part of the dataset to the wall clock, because a refund is graded by
hours-before-start (CONTRACTS §5.6) and the demo has to show each band. That makes the seed a
function of the hour it runs at, and the live gate starts at whatever hour the runner woke up in —
which is how one SHA came out green and the next red on identical code.

What keeps it honest is measured, not assumed:

- `_clock_window` enumerates every whole hour between a band's floor and ceiling and tries every
  unspent listing on it. `backend/tests/test_seed_windows_are_clock_independent.py` walks all
  7 × 24 starting hours and compares the search with an independent oracle over the same space; the
  two agree at every clock, so a band is reported unreachable only when no legal window exists.
- A band the calendar genuinely refuses is printed instead of hidden. The wide policy grades 50%
  only between 24 and 48 hours, and from Friday 21:37 until Saturday 00:00 the rest of Saturday
  starts inside 24 hours while Monday starts past 48 — 9 of the 504 clocks checked. The seed writes
  the other bands, prints `band skipped: 50% over 24-48h ...` with the search that failed, and
  still raises when no band above 0% can be demonstrated at all.
- The seed can run against a database the job runner has already used. A seeded hold lapses after
  its own `hold_minutes`, so "some seeded booking exists" is not evidence the dataset is complete:
  each phase now declares the booking statuses it is responsible for, and a re-run re-mints only
  the shape that decayed (measured: six holds in 0.06s, forty past journeys left intact).

Verified 2026-10-10 on a fresh migrated schema whose clock — the seed's and every service's — was
moved to Friday 22:30 UTC: 9.5s, all eight §5.5 statuses, review average 4.2, 7160 cents refunded
through the shipped services, the 50% band reported as skipped rather than fatal. On the machine's
own clock the same seed writes 100%, 50% and closed-window 0% cancellations and `--verify` reads
the dataset back over the shipped routes, after which journey 39/39, the race and the security
matrix were re-run against it.

### 4b. Admin surface, driven in a real browser (2026-10-11)

Phase 12 is moderation, and moderation is only real if a person can click it. The seven admin
screens were opened in headless Edge against the dev server and the seeded backend, per role:

| Session | `/admin/analytics` | `/admin/users` | `/admin/providers` | `/admin/disputes` | `/admin/audit-logs` | `/admin/categories` | `/admin/promotions` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| platform admin | 11 cards, 39 table rows | 20 rows | 9 rows | 1 card, full context | 30 of 692 rows | 12 rows | 1 card |
| support | redirected to `/dashboard` | redirected to `/dashboard` | redirected | renders | redirected | redirected | redirected |
| customer | redirected | redirected | redirected | redirected | redirected | redirected | redirected |

Support and customer were signed out through the UI's own control between sessions, not by
clearing storage: the session is server-side, and the first pass over this table read
`FIELDS_MISSING` for two roles simply because the previous admin was still logged in. An
audit that does not sign out proves only that one session can see everything.

The analytics screen was also asked to change: entering a 9-day window moved the figures it
owns (86 new accounts → 0, 100 bookings created → 15, $18,645.80 → $5,193.00 GMV) while the
all-time cards stayed put, which is the only way to tell a live read from a painted constant.

The dispute card renders what a decision needs without leaving the screen — service name,
complainant, org, age, order total, refunded amount, payment and booking status — and the
promotion card renders code, discount, thresholds, usage 3/500 and window.

Two reversible writes were pushed through the screens rather than the API, to prove the mutation
wiring and the refetch after it: granting `provider` to `customer01@example.test` moved the row
from `Customer` to `Provider ×`, and revoking moved it back; retiring the `equipment` category
swapped its `Retire` control for `Restore`, and restoring it swapped back. The database was read
back afterwards and holds exactly what the seed wrote — one `customer` role on that account,
`equipment` active — so the proof left no residue in the demo dataset.

Unlike the other layers this one was driven by a throwaway CDP script on the development box, so it
is evidence for this SHA rather than a check CI can repeat. The part CI *can* repeat — which role
reaches which screen — is committed as the nine `admin-access.test.tsx` cases in layer 3.

### 5. Performance layer — `tests/perf/`

Closes the four measurements this document used to describe as owed: marketplace search,
availability queries, booking confirmation under contention, dashboard aggregation. Run:

```
bash infrastructure/scripts/perf_gate.sh              # scale 1.0, 60 samples per measurement
PERF_SCALE=0.15 bash infrastructure/scripts/perf_gate.sh   # quick pass while iterating
```

The gate needs the local PostgreSQL cluster up (`infrastructure/scripts/dev_up.sh`); it says so
instead of starting one. It builds its own database, `capacity_perf`, so a run cannot be confused
with seeded demo data, and tears nothing else down.

**Fixture.** `tests/perf/volume.py` writes a corpus with `COPY` and real schema validation: 400
organizations, 1,226 resources, 2,452 definitions and published offers, 14,712 recurring rules,
3,401 users, 40,000 bookings, 35,260 orders/payments, 23,377 fulfillments, 7,396 reviews, 1,313
disputes — 86 MB. Bookings are capacity-checked while they are generated, so no hour is ever
over-subscribed; an availability or contention number measured on an impossible corpus would be
worth nothing. A fixed RNG seed makes the corpus reproducible, and one org (`perf-heavy-org`) is
deliberately given 24 resources so the provider dashboard aggregates over real traffic.

**Measured, at scale 1.0, single client on the development box (p50 / p95 / max ms, budget ms):**

| measurement | p50 | p95 | max | budget | verdict |
| --- | --- | --- | --- | --- | --- |
| search, filters only | 12 | 20 | 20 | 150 | PASS |
| search, date window | 640 | 702 | 739 | 1000 | PASS |
| availability, 7 days | 10 | 11 | 13 | 400 | PASS |
| availability, 30 days | 18 | 20 | 31 | 1500 | PASS |
| booking hold under contention (12 parallel, 6 free) | 270 | 307 | 316 | 1500 | PASS |
| booking confirm under contention (6 conversions) | 134 | 217 | 217 | 2000 | PASS |
| provider dashboard, 30 days | 161 | 188 | 228 | 1500 | PASS |
| admin dashboard | 29 | 36 | 46 | 600 | PASS |

Contention is asserted, not just timed: 12 simultaneous holds against a definition with 6 free
units produced exactly 6 grants and 6 `no_availability` refusals, all 6 converted, the window then
read as fully committed, and a 13th hold was refused.

The table is one run, so the gate was re-run end to end on the release SHA against the same corpus:
8 of 8 inside budget again, with the p95s moving by −31 ms to +47 ms (the contended hold is the
noisiest, as expected of a measurement whose whole point is serialization). Reproducible verdict,
not a reproduced number — which is the most a single-box p95 can honestly offer.

**Where the time actually goes.** Each measurement prints `EXPLAIN (ANALYZE, BUFFERS)` for the
statement the service compiles. The SQL is not the bottleneck anywhere: the search page costs
3.7 ms, its count 2.3 ms, the candidate page 6.8 ms, a rules read 0.065 ms, the in-lock
`committed_quantity` aggregate 0.117 ms, and every dashboard statement except one under 2 ms. The
latency in the table is the request overhead around them — with one exception worth naming: the
date-window search runs a Python fan-out over up to `CANDIDATE_CAP = 200` candidates
(`backend/app/services/marketplace.py:search`), and each candidate costs about twelve statements:
two identity loads for its definition and resource, three reads inside `build_day_plan` (rules,
overrides, exceptions), and one `committed_quantity` aggregate per expanded window. Two thousand
four hundred round trips at the measured 0.25–0.3 ms each is the whole 640 ms. That is the first
place to optimize if this number ever needs to be smaller — bulk-loading the definitions,
resources and rules for the candidate page instead of per candidate — and it is not optimized now
because the budget it is measured against is met, and the fix would touch the shared availability
engine the booking tests depend on. The provider revenue statement does sequential-read 6,650
booking rows (11.4 ms); acceptable inside a 30-day aggregate, tracked as the second.

**What these numbers are not.** They are a p95 of *this machine* — one client, one process,
embedded PostgreSQL, no Redis, rate limiter raised out of the way so it cannot be the reason a
request is slow. They are not a throughput claim, not a capacity plan, and not comparable to a
hosted deployment. Nothing else in this repository quotes them.

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
- **Desktop page-level rendering in CI** — the 51 tests cover the booking and assistant logic,
  the envelope handling, i18n and the admin RBAC gate. The seven admin screens were rendered against
  a live server in a real browser (see layer 4b), but by a throwaway CDP driver on this box rather
  than a committed harness: no CI run opens a browser, so a regression that only shows up inside a
  page is still caught by hand.

## Gate for a release

A release is cut only when layers 1–4 are green in the same CI run and layer 5 is green on the
release box in the same SHA's run: CI runs the suites that are machine-independent (algorithms,
API, live HTTP gate, client tests), while the performance gate stays a local measurement because
its budgets are this machine's p95s and a hosted runner would compare against the wrong ruler.
The migration head must be single and at head, and every failure above is either fixed or recorded
here as not proven. Layer 4b is the exception that is not a gate: it is a browser pass recorded per
SHA, and the part of it CI can repeat is the layer-3 RBAC suite.
The pipeline must fail honestly: no `|| true`, no `continue-on-error`, and no build problem
solved by deleting a feature, screen or native capability.
