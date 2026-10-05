"""Background job registry shared by both execution modes (CONTRACTS §10).

Each job is an async handler receiving an AsyncSession. `CELERY_MODE=local` runs them
from `jobs/local_runner.py` (asyncio loop); `CELERY_MODE=celery` wraps the same handlers
as Celery tasks in `app/workers/celery_app.py`. Handlers for tables that arrive with the
domain migrations (bookings/offers) check table existence via `to_regclass` and no-op
with a warning until those migrations land — so the runner is safe to start today.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("capacityexchange.jobs")

JobHandler = Callable[[AsyncSession], Awaitable[dict]]


async def _table_exists(session: AsyncSession, name: str) -> bool:
    row = (await session.execute(text("SELECT to_regclass(:t)"), {"t": name})).scalar()
    return row is not None


async def expire_holds(session: AsyncSession) -> dict:
    """CAS-expire holds past hold_expires_at (§5.5), with event + notification per row."""
    if not await _table_exists(session, "bookings"):
        logger.info("expire_holds: bookings table not present yet (domain migration pending); skipping")
        return {"skipped": "no_table"}
    from app.services.booking import expire_due_holds

    expired = await expire_due_holds(session)
    return {"expired": len(expired)}


async def send_notifications(session: AsyncSession) -> dict:
    """Flush queued outbound rows; email/SMS are no-op records when SMTP is unset (§11)."""
    if not await _table_exists(session, "notifications"):
        return {"skipped": "no_table"}
    from app.services.notifications import send_pending

    return await send_pending(session)


async def rebuild_offer_search(session: AsyncSession) -> dict:
    """`offers.search_vector` is a GENERATED column, so Postgres keeps it current per write.

    The job remains as the operational hook (and a cheap health probe): it re-checks the
    GIN index exists and counts indexed rows, so an operator can see staleness without
    inventing a redundant rebuild.
    """
    if not await _table_exists(session, "offers"):
        logger.info("rebuild_offer_search: offers table not present yet; skipping")
        return {"skipped": "no_table"}
    indexed = (await session.execute(text("SELECT count(*) FROM offers"))).scalar()
    has_index = (await session.execute(text(
        "SELECT to_regclass('ix_offers_search_vector')"))).scalar() is not None
    return {"indexed_offers": indexed, "gin_index": has_index}


async def aggregate_analytics(session: AsyncSession) -> dict:
    """Placeholder-safe daily rollup: counts bookings/orders when tables exist."""
    out: dict = {}
    for table in ("bookings", "orders", "users"):
        if await _table_exists(session, table):
            row = (await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar()
            out[table] = row
    logger.info("aggregate_analytics: %s", out)
    return out


async def cleanup_idempotency(session: AsyncSession) -> dict:
    """Delete idempotency keys older than 24h (§5.7)."""
    if not await _table_exists(session, "idempotency_keys"):
        return {"skipped": "no_table"}
    res = await session.execute(
        text("DELETE FROM idempotency_keys WHERE created_at < now() - interval '24 hours'")
    )
    return {"deleted": res.rowcount or 0}


JOBS: dict[str, JobHandler] = {
    "expire_holds": expire_holds,
    "send_notifications": send_notifications,
    "rebuild_offer_search": rebuild_offer_search,
    "aggregate_analytics": aggregate_analytics,
    "cleanup_idempotency": cleanup_idempotency,
}


async def run_job(session: AsyncSession, name: str) -> dict:
    handler = JOBS[name]
    result = await handler(session)
    await session.commit()
    return result
