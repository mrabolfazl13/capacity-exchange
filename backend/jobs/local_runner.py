"""In-process job runner for `CELERY_MODE=local` (CONTRACTS §10).

Same handlers as the Celery tasks, same semantics — only the scheduler differs. One task
per job so a slow or failing job cannot delay the others, and every failure is logged and
swallowed: a broken sweeper must not take the API down with it.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import jobs as job_registry
from app.core.config import Settings

logger = logging.getLogger("capacityexchange.jobs.local")

#: Seconds between runs. Hold expiry needs to be snappy; rollups do not.
DEFAULT_INTERVALS: dict[str, int] = {
    "expire_holds": 30,
    "send_notifications": 60,
    "rebuild_offer_search": 300,
    "cleanup_idempotency": 3600,
    "aggregate_analytics": 3600,
}


async def run_once(session: AsyncSession, name: str) -> dict:
    """Single-shot entry point used by tests, scripts and the Celery task wrapper."""
    return await job_registry.run_job(session, name)


class LocalJobRunner:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession],
                 settings: Settings, *,
                 intervals: dict[str, int] | None = None,
                 enabled: tuple[str, ...] | None = None) -> None:
        self.sessionmaker = sessionmaker
        self.settings = settings
        self.intervals = {**DEFAULT_INTERVALS, **(intervals or {})}
        names = enabled if enabled is not None else tuple(job_registry.JOBS)
        self.names = [n for n in names if n in job_registry.JOBS]
        self._tasks: list[asyncio.Task] = []
        self._stopping = asyncio.Event()

    def start(self) -> None:
        if self.settings.celery_mode != "local":
            logger.info("local runner idle: CELERY_MODE=%s uses Celery workers",
                        self.settings.celery_mode)
            return
        for name in self.names:
            self._tasks.append(asyncio.create_task(self._loop(name), name=f"job:{name}"))
        logger.info("local job runner started: %s", ", ".join(self.names))

    async def stop(self) -> None:
        self._stopping.set()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _loop(self, name: str) -> None:
        interval = max(5, int(self.intervals.get(name, 60)))
        while not self._stopping.is_set():
            try:
                async with self.sessionmaker() as session:
                    result = await run_once(session, name)
                if result and not result.get("skipped"):
                    logger.debug("job %s -> %s", name, result)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — a job failure must never exit the loop
                logger.warning("job %s failed: %s", name, exc)
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=interval)
            except asyncio.TimeoutError:
                continue
