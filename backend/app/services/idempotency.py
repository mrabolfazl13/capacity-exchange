"""Idempotency-Key handling (CONTRACTS §5.7).

Replay semantics: the row is claimed before the handler runs, so a concurrent retry of the
same key blocks on the unique index instead of executing the mutation twice. A completed
row replays its stored response; the same key with a different body is a client bug and
raises 409 rather than silently re-running.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, Conflict
from app.models.crosscut import IdempotencyKey

TTL = timedelta(hours=24)


def request_hash(payload: Any) -> str:
    """Stable hash of the request body; key order must not change the fingerprint."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class IdempotentResult:
    """Outcome of claiming a key: either replay `response`, or run and `record`."""

    def __init__(self, row: IdempotencyKey, replayed: bool) -> None:
        self.row = row
        self.replayed = replayed

    @property
    def replay_status(self) -> int | None:
        return self.row.response_status

    @property
    def replay_body(self) -> Any:
        return self.row.response_body


async def claim(
    session: AsyncSession,
    *,
    user_id: UUID,
    route: str,
    key: str | None,
    payload: Any,
) -> IdempotentResult | None:
    """Return None when the request carries no Idempotency-Key (no dedup requested)."""
    if not key:
        return None
    digest = request_hash(payload)
    existing = (
        await session.execute(
            select(IdempotencyKey).where(
                IdempotencyKey.user_id == user_id,
                IdempotencyKey.route == route,
                IdempotencyKey.idem_key == key,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        if existing.request_hash != digest:
            raise Conflict(
                "Idempotency-Key was already used with a different request body",
                details={"field_errors": [{"field": "Idempotency-Key", "message": "body mismatch"}]},
            )
        if existing.completed_at is not None:
            return IdempotentResult(existing, replayed=True)
        raise AppError(
            "A request with this Idempotency-Key is still in progress",
            code="duplicate_request",
            http_status=409,
        )

    row = IdempotencyKey(
        user_id=user_id,
        route=route,
        idem_key=key,
        request_hash=digest,
        expires_at=datetime.now(timezone.utc) + TTL,
    )
    # A savepoint, not session.rollback(): a lost race must undo only this INSERT and
    # leave any work the handler already did inside the same request intact.
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError as exc:
        winner = (
            await session.execute(
                select(IdempotencyKey).where(
                    IdempotencyKey.user_id == user_id,
                    IdempotencyKey.route == route,
                    IdempotencyKey.idem_key == key,
                )
            )
        ).scalar_one_or_none()
        if winner is None:
            raise Conflict("Could not claim idempotency key") from exc
        if winner.request_hash != digest:
            raise Conflict("Idempotency-Key was already used with a different request body") from exc
        return IdempotentResult(winner, replayed=winner.completed_at is not None)
    return IdempotentResult(row, replayed=False)


async def record(session: AsyncSession, result: IdempotentResult | None, *,
                 status: int, body: Any) -> None:
    if result is None:
        return
    result.row.response_status = status
    result.row.response_body = body
    result.row.completed_at = datetime.now(timezone.utc)
    await session.flush()


def require_key_or_none(header_value: str | None) -> str | None:
    if not header_value:
        return None
    trimmed = header_value.strip()
    return trimmed[:160] if trimmed else None


__all__ = ["IdempotentResult", "claim", "record", "request_hash", "require_key_or_none"]
