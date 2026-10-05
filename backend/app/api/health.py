"""Unauthenticated health surface (§1): liveness, readiness and a combined summary."""
from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import text

from app.core.config import Settings

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request) -> dict:
    settings: Settings = request.app.state.settings
    db_ok = await _db_reachable(request)
    return {"status": "ok" if db_ok else "degraded", "db": db_ok,
            "redis": _redis_reachable(request), "version": settings.app_version}


@router.get("/health/live")
async def live() -> dict:
    """Process is up; deliberately performs no I/O so it can never flap on a DB blip."""
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(request: Request) -> dict:
    """Ready means the database answered: Redis is optional and never gates readiness (§10)."""
    db_ok = await _db_reachable(request)
    settings: Settings = request.app.state.settings
    return {"status": "ok" if db_ok else "degraded", "db": db_ok,
            "redis": _redis_reachable(request), "version": settings.app_version}


async def _db_reachable(request: Request) -> bool:
    from app.core.db import get_sessionmaker

    try:
        async with get_sessionmaker(request)() as session:
            await session.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 — health must report, never raise
        return False
    return True


def _redis_reachable(request: Request) -> bool:
    limiter = getattr(request.app.state, "ratelimiter", None)
    return bool(limiter is not None and limiter.using_redis)
