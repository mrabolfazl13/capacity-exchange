# Capacity Exchange — Master Prompt

## Mission
Build Capacity Exchange as a production-grade marketplace that converts unused business capacity into discoverable, bookable inventory.

Core lifecycle:

UNUSED CAPACITY → INVENTORY → DISCOVERY → MATCHING → BOOKING → PAYMENT → FULFILLMENT → ANALYTICS

## Mandatory Stack
- Desktop: Tauri + Vite + React + TypeScript
- Mobile: Flutter (Android + iOS)
- Backend: Python + FastAPI + Pydantic + SQLAlchemy + Alembic
- Database: PostgreSQL
- Cache/realtime/locks: Redis
- Background jobs: Celery
- Infrastructure: Docker + Docker Compose
- Testing: pytest, Vitest/React Testing Library, Flutter tests, Playwright where appropriate

## Autonomous Execution
Do not ask unnecessary questions. Inspect the repository, make sound engineering decisions, implement real files, run tests, fix failures, and continue until the first complete working version exists. Do not stop at planning, wireframes, mock APIs, placeholders, or TODOs.

## Agentic Execution
Use specialized parallel agents:
1. Architecture
2. Backend/Core
3. Frontend/Desktop
4. Mobile
5. AI/Data
6. QA/Security
7. Performance/Release

Parallelize independent work. Synchronize before changes to shared contracts, database schema, authentication, booking state machines, or shared types.

## Required Project Control Files
Keep these current:
- AGENTS.md
- CURRENT_STATE.md
- IMPLEMENTATION_PLAN.md

## Core Domain
Tenant, Organization, User, Provider, Customer, CapacityResource, CapacityDefinition, CapacityUnit, Availability, Offer, Demand, Match, Booking, Order, Payment, Commission, Fulfillment, Review, Dispute, Notification, Conversation, Message, Promotion, Coupon, AuditLog.

## Capacity Types
Support extensible categories including:
- space
- time/appointments
- transportation
- equipment
- production
- storage
- hospitality
- workforce/service capacity

Do not hard-code the product around one category.

## First Working Version
Must include:
- authentication and RBAC
- provider/customer onboarding
- capacity creation wizard
- availability
- marketplace search/filter
- capacity detail
- booking
- temporary holds
- concurrency protection
- cancellation
- orders
- payment abstraction
- notifications
- provider dashboard
- customer dashboard
- admin dashboard
- reviews
- audit logs
- seed/demo data
- Docker development environment
- automated tests
- Windows Tauri build
- Flutter Android build/testable project

## Quality Bar
No fake buttons, disconnected screens, silent errors, or fake booking success. Every critical UI action must reach a real API and persist real state. Review UI manually and structurally, not only through automated tests.

## Booking Safety
Implement idempotency, transactional consistency, temporary holds, expiration, race-condition protection, and prevention of double booking.

## Documentation
All implementation decisions must be reflected in the relevant docs. Actual code is the final source of truth when docs become stale; update the docs immediately.

## Completion
Do not declare completion until:
1. backend starts
2. database migrations run
3. seed data loads
4. marketplace works
5. booking works
6. concurrent booking is protected
7. desktop app builds
8. mobile app builds/tests
9. tests pass or documented non-blocking environment limitations exist
10. README contains exact setup/run commands
