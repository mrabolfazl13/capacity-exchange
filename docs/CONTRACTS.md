# Shared Contracts (Authoritative)

Source of truth for cross-agent work: DB schema, API conventions, auth/RBAC, booking
concurrency, idempotency, DTO rules. Sync before changing anything here. Code and
Alembic migration `0001_core_schema` must match this file exactly; if they diverge,
fix the code and update this file in the same change.

Machine reality (this dev box): no Docker, no Redis binary, Python 3.12.10, Node 24,
Flutter 3.47, cargo 1.96, Java 17, pgserver (embedded real PostgreSQL **16.2**) for
dev/test DBs, pip installs need `-i https://pypi.org/simple`.

VERIFIED CONSTRAINTS of the pgserver build (validated live on 2026-10-04): contrib
extensions (`btree_gist`, `pgcrypto`, `citext`, `unaccent`) are **NOT available**;
core `gen_random_uuid()`, `tstzrange`, enums, triggers, advisory locks work.
Docker `postgres:16` DOES have contrib. Consequence: correctness must never depend
on a contrib extension (see §5.5 — advisory-lock protocol is authoritative, GiST
exclusion is compose-only hardening). Never use `citext` columns — store normalized
lowercase + unique index.

---

## 1. Naming & DTO conventions

- JSON: snake_case everywhere. IDs: UUIDv4 strings. Timestamps: ISO-8601 UTC with `Z`.
- Enums: lowercase_with_underscore string values.
- Money: integer minor units (`unit_amount_cents`) + ISO `currency`. Never floats in APIs.
- Dates for availability day-of-week: 0 = Monday … 6 = Sunday (ISO order).
- API base prefix: `/api/v1`. Health endpoints are unauthenticated: `GET /health`,
  `GET /health/live`, `GET /health/ready` (ready = DB reachable; returns
  `{status:"ok", db:true, redis:false|true, version}`).
- Realtime: `GET /api/v1/notifications/stream?access_token=<jwt>` (SSE). Clients that do
  not consume it must fall back to polling `GET /notifications?unread=true`.

## 2. Response envelope

- Detail/create/update/delete: bare object, status 200 (or 201 for create, 204 for delete).
- Lists: `{ "items": [...], "total": 123, "limit": 20, "offset": 0 }`.
  Query params: `limit` (default 20, max 100), `offset` (default 0). `total` is the
  full filtered count.
- Errors: `{ "error": { "code": "<machine_code>", "message": "<human>", "details": {...}, "request_id": "<uuid>" } }`
- Validation (422): code `validation_error`, `details.field_errors = [{field, message}]`.

### Error code taxonomy

| HTTP | codes |
|---|---|
| 400 | `validation_error`, `invalid_state_transition`, `unprocessable` |
| 401 | `unauthorized`, `token_expired`, `token_invalid`, `session_revoked` |
| 403 | `forbidden`, `not_member`, `ownership_required`, `role_required`, `tenant_mismatch` |
| 404 | `not_found` |
| 409 | `conflict`, `no_availability`, `capacity_exceeded`, `hold_expired`, `already_rated`, `duplicate_request`, `invalid_credentials` |
| 410 | `gone` |
| 422 | `validation_error`, `range_mismatch` |
| 429 | `rate_limited` (`details.retry_after_seconds`) |
| 500 | `internal_error` (never leak internals; log with request_id) |
| 503 | `dependency_unavailable` |

## 3. Authentication

- Password hashing: Argon2id (`argon2-cffi`, already installed).
- Access token: JWT HS256 via PyJWT. Payload: `sub` (user UUID), `org` (active org UUID or
  null), `roles` ([role keys]), `typ:"access"`, `iat`, `exp` (30 min), `jti`.
- Refresh token: opaque 256-bit random, **SHA-256 hash stored**, plaintext returned once.
  Rotation: `POST /auth/refresh` invalidates the old token and issues a new pair.
  Reuse of a rotated token revokes the whole session family (`session_revoked`).
  TTL 14 days. Logout revokes the session row.
- Header: `Authorization: Bearer <access>`.
- Endpoints: register (public user + org), login, refresh, logout, `GET /auth/me`,
  `PATCH /auth/me`.

## 4. RBAC & tenancy

Roles (global `user_roles.role`): `platform_admin`, `support`, `org_admin`, `provider`,
`customer`. A user may hold several; every user has `customer` implicitly for reads.

| Role | Can |
|---|---|
| customer | browse/list offers (public read), create demands, holds, bookings, orders, payments for own, reviews for own fulfillments, own notifications/conversations |
| provider | all customer, plus: own-org resource/availability/offer CRUD + publish, accept/decline demands matched to own offers, confirm/start/complete own bookings, own dashboard/analytics |
| org_admin | manage own-org staff (`org_staff`), see/act on all own-org provider resources and bookings |
| support | moderate disputes, conversations, view bookings/users for operations; no payment operations |
| platform_admin | everything incl. `/admin/*`, promotions, category registry, audit log |

Tenancy: `organizations` is the tenant boundary for provider-side data. Every
provider-owned row carries `org_id`; services must filter by it. Marketplace reads
of published offers are cross-org by design. `users.phone` partial-unique (per tenant).

Permission dependencies (backend/core/deps.py): `current_user`,
`require_roles(*keys)`, `require_org_membership(org_id)`, plus explicit ownership
checks in services (`resource.org_id == user.active_org_id` or platform_admin).

## 5. Database schema (PostgreSQL 16+)

All tables: `created_at timestamptz not null default now()`, `updated_at timestamptz
not null default now()` (BEFORE UPDATE trigger `set_updated_at()`). PKs `uuid`
(`gen_random_uuid()` default). FKs listed inline. Enums are **plain varchar + CHECK**
for simplicity, except the two booking/payment status enums which are real Postgres
enums (used in exclusion constraints and indices).

### 5.1 Identity & orgs

```
tenants(id, key varchar unique, name, status tenants_status_enum active|suspended, settings jsonb '{}')
organizations(id, tenant_id FK tenants, name, slug varchar unique, legal_name null, country char(2) null,
              timezone varchar default 'UTC', currency char(3) default 'USD', contact_email null,
              phone null, address jsonb null, status org_status_enum active|suspended, created_by FK users null)
users(id, tenant_id FK, email varchar unique (ALWAYS stored lower()-normalized by app),
  phone varchar null (partial unique when not null),
      password_hash, full_name, preferred_locale default 'en', is_active bool default true,
      last_login_at null, created_by null)
user_roles(user_id FK users, role role_key, granted_by FK null, pk (user_id, role))
org_staff(id, org_id FK, user_id FK, role org_staff_role_enum org_admin|manager|staff,
          status org_staff_status_enum active|invited|revoked, uq (org_id, user_id))
sessions(id, user_id FK, refresh_hash char(64), family uuid, ip inet null, user_agent text null,
         expires_at, revoked_at null, created_at/updated_at, idx refresh_hash)
```

### 5.2 Capacity

```
capacity_categories(id, key varchar unique, label, parent_id self-FK null, is_active bool,
  attributes_schema jsonb null)  -- seed: meeting_room, warehouse, parking, equipment,
  -- appointment_slot, truck_return, commercial_kitchen, production_slot, storage_space,
  -- hospitality_room, workstation, transport_vehicle

capacity_resources(id, org_id FK, category_id FK, creator_id FK users,
  name, description null, capacity_mode capacity_mode_enum,  -- scheduled|quantity|open_ended
  address jsonb '{line1,line2,city,state,postal_code,country}',  -- country char(2)
  lat numeric(8,5) null, lon numeric(8,5) null, timezone varchar 'UTC',
  status resource_status_enum draft|active|archived,
  attributes jsonb '{}', photos jsonb '[]',  -- [{url, caption, sort_order}]
  documents jsonb '[]',                      -- [{name, url, kind}]
  idx (org_id), idx (category_id, status), idx (lat, lon)  -- plain btree, bbox via
  -- lat/lon range filters; NO btree_gist/PostGIS dependency)

capacity_definitions(id, resource_id FK, name,
  unit_label varchar ('hour','pallet','seat','vehicle_slot',...),
  min_quantity int default 1 CHECK(>=1), max_quantity int default 1 CHECK(>=min),
  slot_duration_minutes int null (CHECK null or >=5),   -- required for scheduled
  buffer_before_minutes int default 0, buffer_after_minutes int default 0,
  attributes jsonb '{}', is_active bool default true)

recurring_availabilities(id, definition_id FK, dow int CHECK(0..6), start_time time,
  end_time time CHECK(end_time > start_time), quantity int default 1 CHECK(>=1),
  valid_from date null, valid_until date null, is_active bool,
  uq (definition_id, dow, start_time))

availability_overrides(id, definition_id FK, override_date date, kind availability_override_kind_enum
  closed|extra|reduced, start_time time null, end_time time null, quantity null, reason null,
  uq (definition_id, override_date, start_time nulls))
```

### 5.3 Offers & demand

```
offers(id, definition_id FK, org_id FK (denormalized for fast filtering), resource_id FK (via definition),
  title CHECK(char_length between 3 and 120), description,
  pricing_mode offer_pricing_mode_enum per_unit_time|per_quantity|flat,
  unit_amount_cents int CHECK(>=0), currency char(3),
  min_lead_time_minutes int default 0, max_lead_time_days int null,
  min_duration_minutes null / max_duration_minutes null (scheduled only; max>=min),
  min_quantity null / max_quantity null (within definition),
  booking_mode booking_mode_enum request_confirm|instant,
  hold_minutes int default 15 CHECK(5..120),
  cancellation_policy jsonb '[{"hours_before":24,"refund_pct":100},{"hours_before":0,"refund_pct":0}]',
  status offer_status_enum draft|published|paused|closed, published_at null,
  idx partial (status='published'), idx (org_id), fts: to_tsvector on (title, description) GIN)

demands(id, customer_id FK users, org_id FK null, category_id FK null, description,
  address jsonb null, lat null, lon null, desired_start timestamptz null, desired_end null,
  quantity int default 1, budget_min_cents null, budget_max_cents null,
  status demand_status_enum open|matched|closed|cancelled,
  expires_at null, idx (status), idx (customer_id))

matches(id, demand_id FK, offer_id FK, score numeric(6,3), reasons jsonb '[]',
  status match_status_enum suggested|accepted|declined|expired, provider_action_at null,
  uq (demand_id, offer_id), idx (demand_id, status))
```

### 5.4 Booking (the safety core)

Single physical table `bookings` carries the whole lifecycle — a hold is a booking row
in status `hold`. This avoids double-counting across tables.

```
bookings_status_enum: draft | hold | confirmed | in_progress | completed | cancelled |
                      expired | disputed
bookings_payment_enum: not_required | unpaid | paid | refunded | partially_refunded

bookings(id, offer_id FK, definition_id FK, org_id FK, customer_id FK users,
  created_by_user_id FK users, source match_id FK null,
  status bookings_status_enum default 'hold', payment_status bookings_payment_enum,
  window_start timestamptz, window_end timestamptz,
  quantity int default 1 CHECK(>=1),
  unit_amount_cents int, currency char(3),
  total_cents int CHECK(total_cents >= 0 AND = unit*quantity when mode not flat),
  hold_expires_at timestamptz null (idx, only meaningful in status hold),
  request_fingerprint uuid null,  -- client-generated for hold idempotency
  cancel_reason text null, cancelled_at null, cancelled_by null,
  confirmed_at null, started_at null, completed_at null, dispute_opened_at null,
  meta jsonb '{}',
  uq (idempotency_key) via idempotency table instead — see 5.7,
  -- NO EXCLUDE in core schema (uuid GiST needs btree_gist = contrib, absent in
  -- pgserver; see 5.5). Optional hardening migration adds it where contrib exists.
  idx (customer_id, status), idx (org_id, status), idx (offer_id, status),
  idx (definition_id, status, window_start),
  idx (status, hold_expires_at)
)

booking_status_events(id, booking_id FK, from_status null, to_status, actor_user_id null,
  reason null, meta jsonb '{}', created_at as occurred)
```

### 5.5 Booking concurrency protocol (MANDATORY)

Authoritative mechanism: PostgreSQL **transaction-scoped advisory lock + in-lock
revalidation** (works on ANY Postgres build — verified on pgserver 16.2 without
contrib). GiST exclusion constraints may be added as optional hardening in
deployments where `btree_gist` exists, but tests on pgserver must not require it.

1. `pg_advisory_xact_lock(hashtext(definition_id::text))` at the start of the
   hold/create/quantity-change transaction (all writers take the same lock ⇒
   serialization per definition).
2. In-lock revalidation: `SELECT coalesce(SUM(quantity),0) FROM bookings WHERE
   definition_id=$1 AND status IN ('hold','confirmed','in_progress') AND
   window_start < $3 AND window_end > $2` (range overlap, plain SQL — no
   tstzrange/GiST needed).
3. If `sum + requested_quantity > definition.max_quantity` → 409 `capacity_exceeded`.
   For `scheduled`/discrete-slot offers the same query with `sum >= requested`
   semantics (max_quantity units overlapping) yields 409 `no_availability`.
4. Insert booking (status `hold` or `confirmed`) in the SAME transaction as the
   lock + revalidation. Commit releases the lock.
5. Never trust client-observed availability; revalidate at commit time.

Redis may add caching/locks but **must not** be load-bearing for correctness.
Availability expansion: recurring rules + overrides are expanded to concrete
windows in SQL/python inside the service layer (`capacity_windows` helper); the
`availability_ranges` VIEW is an optional read convenience, NOT the correctness
path. API `GET /capacities/{resource_id}/availability?definition_id&from&to`
computes free quantity per day server-side.

Hold expiry: Celery/beat **and** a fallback in-process sweeper (when Redis/Celery absent)
sets `status='expired'` for `hold_expires_at < now()` where status='hold', writes a
`booking_status_events` row + notification. Expiry is idempotent (CAS on status).

State transitions (server-enforced; anything else → 400 `invalid_state_transition`):
- `hold → confirmed` (instant offers, or payment captured, or provider accepts)
- `hold → expired` (sweeper) | `hold → cancelled` (customer/provider)
- `draft → confirmed` | `draft → cancelled`
- `confirmed → in_progress` (provider) | `confirmed → cancelled` (policy) | `confirmed → disputed`
- `in_progress → completed` (provider) | `in_progress → disputed`
- `disputed → cancelled | completed` (support/platform_admin resolution)
- `completed`, `expired`, `cancelled` are terminal.

### 5.6 Orders, payments, commissions, fulfillment

```
orders(id, number varchar unique, buyer_id FK users, provider_org_id FK, booking_id FK null,
  subtotal_cents, discount_cents default 0, commission_cents default 0, total_cents CHECK(>=0),
  currency, line_items jsonb '[]',  -- [{description, qty, unit_cents, total_cents}]
  payment_status order_payment_enum, status order_status_enum draft|placed|paid|fulfilled|
  cancelled|refunded, placed_at null, idx (buyer_id), idx (provider_org_id))

payments(id, order_id FK, provider_key varchar (mock|bank_transfer|manual|stripe-like),
  amount_cents>0, currency, status payment_status_enum created|pending|succeeded|failed|
  canceled|refunded, client_secret null, provider_payment_id null, failure_reason null,
  confirmed_at null, idx (order_id), uq (provider_key, provider_payment_id) partial)
payment_events(id, payment_id FK, event_key varchar unique, type, payload jsonb, occurred_at)
refunds(id, payment_id FK, order_id FK, amount_cents>0, currency, reason,
  status refund_status_enum pending|succeeded|failed, processed_by FK null, processed_at null)
commissions(id, order_id FK uq, org_id FK, basis_cents, rate_bp int (1000 = 10%),
  amount_cents, currency, booked_at)
fulfillments(id, booking_id FK uq, org_id FK, status fulfillment_status_enum pending|
  in_progress|completed|no_show|failed, started_at, completed_at null, notes jsonb '[]',
  completed_by null)
```

Flow: hold/booking confirm → order created (`/orders` from booking or automatic on
confirm when offer requires payment) → `POST /payments/intents {order_id}` →
`POST /payments/{id}/confirm` (mock provider = immediate success; idempotent via
`Idempotency-Key`) → webhook `POST /payments/webhook` (HMAC-style shared-secret header
`X-Webhook-Signature: v1=<hex hmac sha256 of raw body with settings.webhook_secret>`).
On succeeded: booking `hold/draft → confirmed` (if not already), order.paid,
fulfillment row created (pending), commission row booked (rate from offer meta or
settings.default_commission_bp = 1000), notifications.

Cancellation refund: match `offer.cancellation_policy` bands on hours-before-start →
`refund_pct`; auto-create refund on a succeeded payment; booking → cancelled.

### 5.7 Cross-cutting

```
idempotency_keys(id, user_id FK, route varchar, idem_key varchar, request_hash char(64),
  response_status int null, response_body jsonb null, completed_at null,
  uq (user_id, route, idem_key))
```
Applies to `POST /bookings/hold`, `POST /bookings`, `POST /payments/intents`,
`POST /payments/{id}/confirm`, `POST /orders`. Header `Idempotency-Key`. Same key +
same request hash → replay stored response; same key + different body → 409
`duplicate_request` (conflict semantics: return 409 `validation_error` with details).
Keys expire after 24h (cleanup job). Holds additionally accept client
`request_fingerprint` so a retried hold without header is deduped within its window.

```
audit_logs(id, actor_user_id null, actor_org_id null, action varchar, entity_type varchar,
  entity_id uuid null, before jsonb null, after jsonb null, ip inet null,
  user_agent text null, request_id uuid null, idx (entity_type, entity_id), idx (created_at))
notifications(id, user_id FK, org_id null, kind varchar, title, body,
  data jsonb '{}', channel notification_channel_enum in_app|email|sms|push, read_at null,
  sent_at null, idx (user_id, read_at nulls))
conversations(id, kind varchar 'booking', ref_id null, org_id null, customer_id FK,
  provider_org_id FK, status conversation_status_enum open|closed|archived,
  last_message_at timestamptz, uq (kind, ref_id, customer_id))
messages(id, conversation_id FK, sender_id FK, body text len 1..4000, is_system bool default false,
  read_receipts jsonb '{}', idx (conversation_id, created_at))
promotions(id, name, kind promotion_kind_enum coupon|campaign, code varchar null unique,
  discount_config jsonb ({"type":"pct","bp":1000} | {"type":"fixed","cents":5000,"currency":"USD"}),
  min_order_cents default 0, applies_to jsonb '{}', usage_limit null, used_count default 0,
  per_user_limit null, starts_at, ends_at, status promo_status_enum draft|active|expired|disabled,
  created_by FK)
coupon_redemptions(id, promotion_id FK, user_id FK, order_id FK null, redeemed_at,
  uq (promotion_id, user_id, order_id))
disputes(id, booking_id FK, org_id FK, complainant_id FK users, kind dispute_kind_enum
  quality|no_show|payment|damage|other, description, status dispute_status_enum
  open|under_review|resolved_refund|resolved_partial|resolved_no_fault|closed,
  resolution_note null, resolved_by null, resolved_at null, idx (status))
reviews(id, booking_id FK, fulfillment_id FK, offer_id FK, org_id FK, reviewer_id FK users,
  rating int CHECK(1..5), comment text null, provider_reply null, replied_at null,
  status review_status_enum published|pending_moderation|removed,
  uq (fulfillment_id), idx (offer_id, status))
capacity_definition_exceptions(id, definition_id FK, effective_from date null,
  effective_to date null, max_quantity_delta int null, is_closed bool default false, reason null)
```

## 8. Resource/entity API surface (all under /api/v1)

Beyond the endpoints in API_SPEC.md, these complete the surface (same conventions):

- `GET/PATCH /auth/me`, `POST /auth/logout`
- `POST /organizations` (register flow helper), `GET /organizations/mine`, `PATCH /organizations/{id}`
- `GET /catalog/categories`
- `GET /capacities/{id}/definitions`, `POST/PATCH/DELETE /definitions/{id}` (nested auth via resource)
- `GET /availability/free?definition_id&from&to` → `{items:[{window_start, window_end, free_quantity}]}`
- `GET/PATCH /offers/{id}/publish|pause|close` (publish = POST `/offers/{id}/publish`)
- `GET /demands/{id}/matches`, `POST /demands/{id}/close|cancel`
- `GET /bookings/{id}/timeline` → status events
- `POST /bookings/{id}/confirm` (provider accept, or customer pay-path), `POST /bookings/{id}/start`,
  `POST /bookings/{id}/complete`, `POST /fulfillments/{id}/notes`
- `GET /orders/{id}` — `/orders` customer+provider views
- `GET /payments/{id}`, `GET /orders/{id}/payments`
- `POST /disputes`, `GET /disputes/{id}`, `POST /disputes/{id}/resolve` (support/admin)
- `GET /notifications?unread`, `POST /notifications/{id}/read`, `POST /notifications/read-all`
- `GET /conversations`, `GET /conversations/{id}/messages`, `POST /conversations`, `POST /conversations/{id}/messages`
- Dashboards (aggregates, org-scoped or customer-scoped):
  `GET /dashboard/provider?from&to` → utilization, bookings_by_status, revenue_cents,
  upcoming_bookings, top_offers; `GET /dashboard/customer` → active bookings, spend,
  unread_notifications, recent_orders; `GET /dashboard/admin` → platform totals.
- AI (task 6 implements; routes may 503 `dependency_unavailable` until then):
  `POST /ai/parse-search {text}` → structured filters; `POST /ai/listing-draft {raw_text, category_key}`;
  `POST /ai/price-suggest {offer_id | category+region+mode}` → `{suggested_min_cents, suggested_max_cents, rationale}`;
  `GET /ai/utilization-insights?org_id`; `GET /ai/copilot?org_id`.
- Admin: users/providers/disputes/audit-logs per API_SPEC + `GET/PATCH /admin/categories`,
  `POST /admin/promotions`, `GET /admin/analytics`.

Query params for `GET /offers`: `q` (full-text), `category_id`, `city`, `country`,
`bbox=min_lon,min_lat,max_lon,max_lat`, `from`, `to`, `min_quantity`, `max_unit_cents`,
`min_rating`, `booking_mode`, `sort` (`relevance|price_asc|price_desc|newest|rating`),
`limit`, `offset`. Published offers only.

## 9. Seed demo data (docs/SEED_DATA.md + deterministic ids)

Seeder: `backend/scripts/seed.py --reset`. Demo password for ALL seeded accounts:
`Demo1234!` (dev only). Orgs (5): `northway-logistics`, `cityspace-rooms`,
`precision-fab`, `freshbite-kitchens`, `transitline-fleet` — emails
`owner@<slug>.test` (org_admin+provider), staff `staff@<slug>.test`, 50 customers
`customer01..50@example.test`, platform admin `admin@capacityexchange.test`,
support `support@capacityexchange.test`. ≥50 resources / 100 published offers across
all 8+ categories, recurring + one-off availability incl. closed overrides, 100
demands, matches, bookings in every status (incl. an active hold expiring soon and
conflicting pairs proving exclusion constraint), orders/payments/commissions,
fulfillments + reviews (avg ~4.2), notifications, conversations, 1 active coupon
`WELCOME10` (10% off). Must be idempotent (upsert by stable keys) and finish < 60s.

## 10. Background jobs & graceful degradation

Job kinds (Celery tasks when Redis available; otherwise `backend/jobs/local_runner.py`
in-process asyncio loop — same handlers, same semantics):
`expire_holds`, `send_notifications`, `rebuild_offer_search`, `aggregate_analytics`,
`cleanup_idempotency`, `ai:*`. Redis is optional everywhere: `redis_url` unset or
unreachable → code paths log a warning and use DB-only behavior. Nothing user-facing
may 500 because Redis is down.

## 11. Env & config (backend `app.core.config`, pydantic-settings)

`DATABASE_URL` (required; dev default `postgresql+psycopg://capacity:capacity@localhost:5544/capacity`
pointing at pgserver script), `SECRET_KEY` (dev default `dev-secret-change-me`),
`ACCESS_TOKEN_TTL_MIN=30`, `REFRESH_TOKEN_TTL_DAYS=14`, `REDIS_URL=` (optional),
`CELERY_MODE=local|celery` (default local), `CORS_ORIGINS=http://localhost:5173,tauri://localhost`,
`WEBHOOK_SECRET=dev-webhook-secret`, `DEFAULT_COMMISSION_BP=1000`, `APP_ENV=dev|test|prod`,
`SMTP_*` optional (email notifications become no-op records when unset),
`PAYMENT_PROVIDER=mock`, `RATE_LIMIT_PER_MIN=120` (in-memory token bucket without Redis).

## 12. Client contracts

- Desktop (Tauri+React) and Mobile (Flutter) call the same REST API; no business rules
  in clients beyond input validation hints. Base URL from env:
  desktop `VITE_API_BASE_URL` (default `http://127.0.0.1:8000/api/v1`),
  mobile `--dart-define=API_BASE_URL=...` (default same).
- Token storage: desktop in-memory + localStorage refresh; mobile `flutter_secure_storage`.
- After backend task 2 lands, export OpenAPI to `docs/openapi.json`; clients may
  codegen types but hand-written DTOs following §1 naming are acceptable.
- UI quality bar: no fake buttons — every critical action hits the real API; loading,
  empty and error states are mandatory (see UI_UX_SPEC.md).

## 13. Testing contracts

- Backend tests run against a **real pgserver Postgres** (test database
  `capacity_test`), schema from Alembic `upgrade head` — never SQLite.
- Concurrency tests must run ≥8 parallel hold requests for the same definition/window
  with `max_quantity` lower than attempts and assert exactly `max_quantity` succeed.
- `pytest -q` must pass fully offline. Fast suite marker `-m "not slow"` kept green.

## 14. Contract validation log

- 2026-10-04 — live-validated on the pgserver build (PostgreSQL 16.2): contrib
  extensions (`btree_gist`, `pgcrypto`, `citext`, `unaccent`) unavailable; core
  `gen_random_uuid()`, enums, triggers, `tstzrange`, advisory locks OK;
  `EXCLUDE USING gist` on uuid impossible without btree_gist → §5.5 advisory-lock
  protocol is the authoritative concurrency mechanism (exclusion only as
  compose/postgres-contrib hardening). Email uniqueness via app-normalized
  lowercase varchar.
- pgserver API note: `pgserver.get_server(pgdata)` → `srv.get_uri()` returns a
  `postgresql://` DSN (no `.dsn` attribute; `cleanup` kwarg is `cleanup_mode`).
