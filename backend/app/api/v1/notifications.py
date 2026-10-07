"""`/api/v1/notifications` — the readable bell plus the SSE stream (§5.7, §1 realtime)."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Annotated, Any, AsyncIterator, Callable

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import ActorDep, PageDep, envelope
from app.core.deps import SessionDep, SettingsDep, current_user
from app.core.errors import NotFound, Unauthorized
from app.core.routing import TransactionalRoute
from app.core.security import decode_access_token
from app.models.crosscut import Notification
from app.models.identity import User
from app.schemas.commerce import NotificationOut
from app.services import notifications as svc

router = APIRouter(route_class=TransactionalRoute, prefix="/notifications", tags=["notifications"])

HEARTBEAT_SECONDS = 15.0


def _dump(row: Notification) -> dict:
    return NotificationOut.model_validate(row).model_dump(mode="json")


def _sse(row: Notification) -> str:
    return f"id: {row.id}\ndata: {json.dumps(_dump(row))}\n\n"


@router.get("")
async def list_notifications(session: SessionDep, actor: ActorDep, page: PageDep,
                             unread: bool = False) -> dict:
    """Newest first; `unread=true` is the polling fallback the contract names (§1)."""
    items, total = await svc.list_for_user(session, actor.user_id, unread_only=unread,
                                           limit=page.limit, offset=page.offset)
    return envelope([_dump(i) for i in items], int(total), page)


@router.post("/read-all")
async def mark_all_read(session: SessionDep, actor: ActorDep) -> dict:
    updated = await svc.mark_all_read(session, actor.user_id)
    return {"ok": True, "updated": int(updated)}


@router.post("/{notification_id}/read")
async def mark_read(notification_id: uuid.UUID, session: SessionDep, actor: ActorDep) -> dict:
    """Idempotent: re-reading a row leaves `read_at` at the first touch."""
    row = await session.get(Notification, notification_id)
    if row is None or row.user_id != actor.user_id:
        # The same answer as a missing row, so another user's id does not confirm existence.
        raise NotFound("Notification not found")
    await svc.mark_read(session, notification_id, actor.user_id)
    await session.refresh(row)  # the bulk update leaves the identity map stale
    return _dump(row)


@router.get("/stream")
async def stream(request: Request, session: SessionDep, settings: SettingsDep,
                 page: PageDep,
                 limit: Annotated[int, Query(ge=1, le=200)] = 50,
                 seconds: Annotated[float, Query(ge=0.2, le=3600)] = 60.0,
                 poll: Annotated[float, Query(ge=0.2, le=5.0)] = 1.0) -> StreamingResponse:
    """Snapshot of the unread rows, then every new one until the client or the window ends.

    A bounded stream is deliberate: with no Redis pub/sub available (§11) the honest realtime
    source is a short poll loop, and the cap means a dropped client cannot pin a session.
    """
    user = await _stream_user(request, session, settings)
    maker: Callable[[], Any] = request.app.state.sessionmaker
    body = _events(maker, user.id, snapshot_limit=min(page.limit, limit),
                   seconds=seconds, poll_seconds=poll)
    return StreamingResponse(body, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


async def _stream_user(request: Request, session: AsyncSession, settings) -> User:
    """EventSource cannot set headers, so the stream also accepts `?access_token=` (§1)."""
    token = request.query_params.get("access_token")
    if not token:
        return await current_user(request, session, settings)
    payload = decode_access_token(settings, token.strip())
    try:
        user_id = uuid.UUID(str(payload.get("sub")))
    except ValueError as exc:
        raise Unauthorized("Access token subject is not a user id") from exc
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise Unauthorized("User is inactive or no longer exists")
    return user


async def _events(maker, user_id: uuid.UUID, *, snapshot_limit: int,
                  seconds: float, poll_seconds: float) -> AsyncIterator[str]:
    deadline = time.monotonic() + seconds
    last_beat = time.monotonic()
    first_pass = True
    seen: set[uuid.UUID] = set()

    async with maker() as session:
        # The cursor starts at the newest row that already exists; `seen` is what keeps a row
        # landing on that boundary from reaching the client twice.
        newest = (await session.execute(
            select(func.max(Notification.created_at))
            .where(Notification.user_id == user_id))).scalar()

        while True:
            rows = (await _unread(session, user_id, snapshot_limit) if first_pass
                    else await _since(session, user_id, newest, snapshot_limit))
            first_pass = False
            for row in rows:
                if row.id in seen:
                    continue
                seen.add(row.id)
                newest = row.created_at if newest is None else max(newest, row.created_at)
                yield _sse(row)
            now = time.monotonic()
            if now >= deadline:
                # A dropped client cancels this task through the ASGI server; the window is
                # the backstop for everyone else.
                yield ": stream window closed\n\n"
                return
            if now - last_beat >= HEARTBEAT_SECONDS:
                last_beat = now
                yield ": keep-alive\n\n"
            await asyncio.sleep(poll_seconds)


async def _unread(session: AsyncSession, user_id: uuid.UUID, limit: int) -> list[Notification]:
    """The bell's current backlog, replayed oldest first so the client can append."""
    rows = list((await session.execute(
        select(Notification).where(Notification.user_id == user_id,
                                   Notification.read_at.is_(None))
        .order_by(Notification.created_at.desc()).limit(limit))).scalars().all())
    return list(reversed(rows))


async def _since(session: AsyncSession, user_id: uuid.UUID, newest,
                 limit: int) -> list[Notification]:
    if newest is None:
        return []
    return list((await session.execute(
        select(Notification).where(Notification.user_id == user_id,
                                   Notification.created_at > newest)
        .order_by(Notification.created_at, Notification.id).limit(limit))).scalars().all())
