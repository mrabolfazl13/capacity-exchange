"""Background job registry shared by both execution modes (CONTRACTS §10).

Each job is an async handler receiving an AsyncSession. `CELERY_MODE=local` runs them
from `jobs/local_runner.py` (asyncio loop); `CELERY_MODE=celery` wraps the same handlers
as Celery tasks in `app/workers/celery_app.py`. Handlers for tables that arrive with the
domain migrations (bookings/offers) check table existence via `to_regclass` and no-op
with a warning until those migrations land — so the runner is safe to start today.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("capacityexchange.jobs")

JobHandler = Callable[[AsyncSession], Awaitable[dict]]


async def _table_exists(session: AsyncSession, name: str) -> bool:
    row = (await session.execute(text("SELECT to_regclass(:t)"), {"t": name})).scalar()
    return row is not None


async def expire_holds(session: AsyncSession) -> dict:
    """CAS-expire holds past hold_expires_at (§5.5). Idempotent: only status='hold' rows flip."""
    if not await _table_exists(session, "bookings"):
        logger.info("expire_holds: bookings table not present yet (domain migration pending); skipping")
        return {"skipped": "no_table"}
    now = datetime.now(timezone.utc)
    res = await session.execute(
        text("""
            UPDATE bookings SET status='expired', updated_at=now()
            WHERE status='hold' AND hold_expires_at < :now
        """),
        {"now": now},
    )
    count = res.rowcount or 0
    if count:
        await session.execute(
            text("""
                INSERT INTO booking_status_events (booking_id, from_status, to_status, reason)
                SELECT b.id, 'hold', 'expired', 'hold sweeper' FROM bookings b
                WHERE b.status='expired' AND b.updated_at >= :now
                  AND NOT EXISTS (SELECT 1 FROM booking_status_events e
                                  WHERE e.booking_id=b.id AND e.to_status='expired')
            """),
            {"now": now},
        )
    logger.info("expire_holds: expired %d hold(s)", count)
    return {"expired": count}


async def send_notifications(session: AsyncSession) -> dict:
    """Flush queued in-app notifications (set sent_at). Email/SMS are no-op records when SMTP unset (§11)."""
    if not await _table_exists(session, "notifications"):
        return {"skipped": "no_table"}
    res = await session.execute(
        text("UPDATE notifications SET sent_at=now() WHERE sent_at IS NULL AND channel='in_app'")
    )
    count = res.rowcount or 0
    return {"sent": count}


async def rebuild_offer_search(session: AsyncSession) -> dict:
    """Refresh the offers FTS index (GIN tsvector is maintained by trigger once offers land)."""
    if not await _table_exists(session, "offers"):
        logger.info("rebuild_offer_search: offers table not present yet; skipping")
        return {"skipped": "no_table"}
    await session.execute(text("REFRESH MATERIALIZED VIEW IF EXISTS offer_search_mv"))
    return {"refreshed": True}


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
