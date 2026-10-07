# Security Specification

Security controls must be tested, not merely documented. Every line below is a check in
`tests/security/matrix.py`, run against a live backend (`python tests/security/matrix.py
--base http://127.0.0.1:8000/api/v1`), and the last measured run was **33 of 33 passed**.
A control that has no check is listed under "Not yet proven" instead of claimed.

## Authentication
- Argon2id password hashing (`argon2-cffi`, time_cost 3, memory_cost 64 MiB, parallelism 2).
- Registration requires 8–256 characters. Login accepts any stored-length string, so a
  legacy hash is never unreachable.
- Access token: JWT HS256, 30-minute TTL, `sub`/`org`/`roles`/`typ`/`iat`/`exp`/`jti`.
- Refresh token: opaque 256-bit random, only its SHA-256 hash stored, 14-day TTL, rotated
  on every use; replaying a rotated token revokes the whole session family.
- Proven: garbage bearer → 401; refresh rotates; replayed refresh rejected; the rotated
  access token works; no identity payload carries a hash or a token field.
- Roles are re-read from the database per request, never trusted from the token, so
  revoking a role bites on the next call rather than when the access token expires.

## Authorization
RBAC plus resource ownership and tenant isolation. Proven, in both directions:
- another org cannot read a draft resource, mutate it, or raise its capacity (403/404);
- a customer cannot read an arbitrary organization, or complete their own booking
  (provider-only transition → `ownership_required`);
- a stranger who is **signed in** cannot read another user's booking (403). A check that
  gets 401 proves nothing about the tenant boundary, so the harness authenticates first;
- a customer and a provider are both blocked from `/admin/users` (403 `role_required`),
  and a self-granted `platform_admin` in the register body is rejected (422).

## API
- Validation: pydantic at the boundary; malformed UUIDs, absurd `limit`, missing required
  fields and oversized bodies all answer with the §2 envelope (422), not a 500 or an
  unbounded query.
- Rate limiting: one bucket per **socket address** at `RATE_LIMIT_PER_MIN` (120), Redis
  fixed-window when reachable, in-memory token bucket otherwise. `X-Forwarded-For` is not
  consulted — a caller that could name its own bucket could evade the limit. Proven by
  bursting past the configured limit and asserting `rate_limited` with
  `details.retry_after_seconds` and a `Retry-After` header.
- Errors carry a `request_id` and never leak a traceback, SQL fragment or file path.
- Injection: SQL-ish and `<script>` payloads are stored verbatim and returned as raw text;
  nothing executes and nothing 500s.
- CORS is origin-listed from configuration. Payload size is bounded by field-level limits
  (`max_length` on the request fields), which is what answers the matrix's oversized request
  with the §2 envelope instead of an unbounded query.
- Audit: every mutation in auth, capacity, marketplace, booking, commerce, matching, reviews,
  disputes, conversations and admin writes an `audit_logs` row with actor, org, request id and
  before/after, in the same transaction as the change it describes.

## Data
- Secrets come from environment/configuration only; no credential is committed; the seed
  uses a demo password that is documented as non-production.
- Tenant isolation extends to the assistant surface: `/ai/price-suggest` returns a price
  distribution with no identifiers, so a competitor cannot read another org's listings out
  of a suggestion, and organization-scoped reads are gated on `can_manage_org`.
- Refresh tokens and payment webhooks are stored as hashes; refunds and cancellations
  follow the offer's published policy bands, not a client-supplied figure.

## Business security
Idempotency is enforced per route and actor: the same key with the same body replays the
same booking, the same key with a different body is refused rather than silently applied.
Hold expiry is swept server-side (`expire_due_holds`), and a hold that expires before
confirmation is refused with `hold_expired`. Booking concurrency is proven by the race
harness: capacity is never exceeded, and the provider's own workspace view agrees with the
grant count.

## Not yet proven
- No brute-force **lockout** on an existing account — the general bucket is the only brake
  on repeated wrong passwords.
- No transport-level body-size guard: a 2 MB payload is read into memory before field
  validation rejects it, so the current bound is a correctness limit, not a DoS limit.
- No dependency-scanned secrets inventory in CI yet (planned with the release gate).
- No OWASP-style crawl of the desktop/mobile clients; the matrix is API-surface only.
- Multi-tenant rows created by the seed data are not covered by a data-at-rest migration
  test, because production has no data to migrate.
