# Architecture

## High Level

Flutter / Tauri React
        |
      REST/WebSocket
        |
     FastAPI
   /    |     \
Postgres Redis Celery
        |
      AI/Data

## Desktop
Tauri hosts a Vite + React + TypeScript application. Native functionality should be isolated behind Tauri commands/plugins.

## Mobile
Flutter communicates with the same backend contracts. Do not duplicate business rules in the client.

## Backend
Use layered architecture:
- API
- application services
- domain
- infrastructure
- persistence

## Redis
Use for caching, distributed locks where justified, temporary booking state, rate limiting, and Celery broker/result infrastructure.

## Celery
Use for notifications, indexing, analytics aggregation, AI jobs, expiration tasks, reports, and other non-request-critical work.

## PostgreSQL
Authoritative transactional datastore.

## AI
Keep AI capabilities modular so deterministic business logic never depends on an LLM being available.
