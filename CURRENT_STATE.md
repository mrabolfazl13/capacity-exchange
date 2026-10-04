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
- [x] Contract validation on the real engine (pgserver PostgreSQL 16.2): no contrib
      extensions → advisory-lock protocol is the authoritative concurrency guard
      (docs/CONTRACTS.md §5.5, §14)
- [x] Docker topology + docker-free dev scripts (docker/, infrastructure/scripts/)
- [x] Live e2e harness (tests/e2e/journey.py, tests/e2e/concurrency_race.py)

## In Progress
- [ ] Backend core (identity/auth/RBAC/migrations/tests) — agent, backend/
- [ ] Desktop app (Tauri+React+TS, vitest, vite build) — agent, desktop/
- [ ] Mobile app completion (Flutter) — agent, mobile/
- [ ] AI/data package (deterministic-first, LLM-optional) — agent, ai/

## Blocked
None.

## Agent incidents (operational note)
The session that carried the first agent batch was restarted; the backend and AI
agents were killed by an idle-stream timeout and the mobile agent mid-run, leaving
partial output (backend/ and ai/ empty, mobile/ had api+core only). Mitigation now
in force for every delegated agent: write files to disk incrementally (one per tool
call), run one heavy process at a time (RAM ~1GB free with 4 agents), and keep the
build/bundle steps for the main-agent build phase. Infra output survived and is
committed.

## Active Agents
- backend-core → backend/
- desktop → desktop/
- mobile → mobile/
- ai-data → ai/
- main agent → contracts, e2e harness, CI, integration gate, docs

## Next Sync Point
Backend core landing → main agent launches the backend domains agent
(capacity/marketplace/booking/orders/seed, mounting the ai router), then the QA pass,
then the build/release phase per the attached CI/CD protocol.

## Known Risks
- Booking concurrency
- Flexible capacity modeling
- Cross-platform build dependencies
- Payment-provider differences
- Geospatial search scalability
