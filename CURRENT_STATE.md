# Current State

Last sync: 2026-10-04 (bootstrap pass).

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

## In Progress
- [ ] Backend core (identity/auth/RBAC/migrations/tests) — owner: backend agent
- [ ] Backend domains (capacity/marketplace/booking/orders/seed) — task #3, starts
      after backend core lands; also mounts ai/capacity_ai router
- [ ] Desktop foundation + full app (Tauri+React+TS) — owner: desktop agent
- [ ] Flutter foundation + full app (Android) — owner: mobile agent
- [ ] AI/data package (deterministic-first, LLM-optional) — owner: AI agent
- [ ] Docker/infra + local dev scripts + root README — owner: infra agent

## Blocked
None.

## Active Agents
- backend-core (FastAPI foundation/auth/migrations) — backend/
- infra (docker/infrastructure/scripts/README) — docker/, infrastructure/, README.md
- desktop (Tauri+React) — desktop/
- mobile (Flutter) — mobile/
- ai-data (matching/search/pricing/insights) — ai/

## Next Sync Point
When backend core lands: backend domain agent implements capacity engine,
marketplace, booking engine, orders/payments, seed (tasks #3), mounts ai router;
then QA/security pass and the integration gate (backend boot + migrations + seed +
E2E journey + desktop/flutter builds).

## Known Risks
- Booking concurrency
- Flexible capacity modeling
- Cross-platform build dependencies
- Payment-provider differences
- Geospatial search scalability
