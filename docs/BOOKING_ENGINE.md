# Booking Engine

## State Machine

DRAFT
→ HOLD
→ CONFIRMED
→ IN_PROGRESS
→ COMPLETED

Alternative exits:
HOLD → EXPIRED
HOLD → CANCELLED
CONFIRMED → CANCELLED
CONFIRMED → DISPUTED
IN_PROGRESS → DISPUTED

## Temporary Hold
A hold reserves capacity for a short configurable period.

Requirements:
- unique booking identifier
- expiration timestamp
- atomic availability validation
- transactional persistence
- idempotency
- safe expiration
- retry-safe background task

## Concurrency
Prevent double booking using PostgreSQL transactional mechanisms and appropriate constraints/locking. Redis locks may supplement but must not replace authoritative database correctness.

## Cancellation
Implement policy-driven cancellation windows, refund calculation abstraction, audit records and notifications.

## Critical Rule
Never confirm a booking merely because the UI believes capacity is available. Availability must be validated server-side at commit time.
