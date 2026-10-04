# API Specification

Base prefix: /api/v1

> Binding cross-agent contract: see **docs/CONTRACTS.md** (response envelope, error
> taxonomy, RBAC, idempotency, booking concurrency). This file lists the endpoint
> surface; CONTRACTS.md §8 extends it. On conflict, CONTRACTS.md wins.

## Authentication
POST /auth/register
POST /auth/login
POST /auth/refresh
POST /auth/logout
GET /auth/me

## Capacity
GET /capacities
POST /capacities
GET /capacities/{id}
PATCH /capacities/{id}
DELETE /capacities/{id}

## Availability
GET /capacities/{id}/availability
POST /capacities/{id}/availability
PATCH /availability/{id}
DELETE /availability/{id}

## Marketplace
GET /offers
POST /offers
GET /offers/{id}
PATCH /offers/{id}
POST /offers/{id}/publish

## Demand
GET /demands
POST /demands
GET /demands/{id}
PATCH /demands/{id}

## Matching
GET /matches
POST /matches/{id}/accept
POST /matches/{id}/reject

## Booking
POST /bookings/hold
POST /bookings
GET /bookings
GET /bookings/{id}
POST /bookings/{id}/cancel

## Orders
GET /orders
GET /orders/{id}

## Payments
POST /payments/intents
POST /payments/{id}/confirm
POST /payments/webhook

## Reviews
POST /reviews
GET /offers/{id}/reviews

## Admin
GET /admin/users
GET /admin/providers
GET /admin/disputes
GET /admin/audit-logs

All write endpoints must validate authorization, ownership, input, state transitions, and idempotency where appropriate.
