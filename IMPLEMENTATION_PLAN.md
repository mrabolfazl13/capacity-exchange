# Implementation Plan

Status markers: [~] in progress, [x] done, [ ] not started. See CURRENT_STATE.md
for owners and docs/CONTRACTS.md for the binding cross-agent contract.

## Phase 0 — Foundation  [~]
Repository, documentation, environment, Docker, CI baseline.
Local reality: no Docker on dev box → pgserver-based dev DB scripts + compose files
validated structurally (infrastructure/scripts/, docker/).

## Phase 1 — Architecture  [x]
Domain boundaries (docs/ARCHITECTURE.md), API conventions + error model + full DB
schema pinned in docs/CONTRACTS.md.

## Phase 2 — Backend Core  [~]
FastAPI, PostgreSQL (pgserver locally / postgres:16 in compose), Redis optional,
Celery-or-local job runner, auth, RBAC, migrations.

## Phase 3 — Capacity Engine  [~]
Resources, definitions, units, availability, recurring schedules, exceptions.

## Phase 4 — Marketplace  [~]
Offers, search, filters, geolocation abstraction, capacity detail.

## Phase 5 — Booking Engine  [~]
Holds, transactions, idempotency, concurrency, cancellation, state machine.

## Phase 6 — Orders & Payments  [~]
Order lifecycle, payment abstraction, commissions, refunds.

## Phase 7 — Provider Experience  [~]
Onboarding, capacity wizard, availability, offers, dashboard, analytics.

## Phase 8 — Customer Experience  [~]
Discovery, comparison, booking, orders, notifications, reviews.

## Phase 9 — Desktop  [~]
Tauri + React + TypeScript production integration and Windows build.

## Phase 10 — Mobile  [~]
Flutter Android/iOS implementation and integration.

## Phase 11 — AI/Data  [~]
Matching, recommendations, pricing suggestions, utilization insights.

## Phase 12 — Admin
Moderation, disputes, users, providers, categories, audit, analytics.

## Phase 13 — QA/Security
Unit, integration, E2E, concurrency, authorization, security regression.

## Phase 14 — Performance
Caching, indexing, query optimization, background jobs, load testing.

## Phase 15 — Release
Docker production profile, Windows package, Android/iOS release configuration, documentation.
