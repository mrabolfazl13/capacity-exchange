"""Notifications (CONTRACTS §5.7) — the only writer of the `notifications` table.

Delivery model: every event creates an `in_app` row immediately (that is the readable
record clients poll or stream), plus an `email`/`sms` row with `sent_at IS NULL` when the
caller asks for an outbound channel. The §10 `send_notifications` job (`send_pending`)
dispatches those pending rows; with `SMTP_HOST` unset it records them as sent no-ops, so
no user-facing path can fail because mail infrastructure is missing (§11).
"""
from __future__ import annotations

import logging
import smtplib
import ssl
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage
from functools import partial
from typing import Any, Iterable, Sequence

import anyio
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings_cached
from app.models.crosscut import Notification
from app.models.identity import OrgStaff, User

logger = logging.getLogger("capacityexchange.notifications")


async def notify(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    kind: str,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
    org_id: uuid.UUID | None = None,
    channels: Sequence[str] = ("in_app",),
) -> list[Notification]:
    """Fan one event out to one user over the requested channels."""
    rows: list[Notification] = []
    for channel in channels:
        row = Notification(
            user_id=user_id,
            org_id=org_id,
            kind=kind,
            title=title[:200],
            body=body,
            data=data or {},
            channel=channel,
        )
        session.add(row)
        rows.append(row)
    await session.flush()
    return rows


async def notify_users(
    session: AsyncSession,
    user_ids: Iterable[uuid.UUID],
    *,
    kind: str,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
    org_id: uuid.UUID | None = None,
    channels: Sequence[str] = ("in_app",),
) -> int:
    seen: set[uuid.UUID] = set()
    count = 0
    for user_id in user_ids:
        if user_id in seen:
            continue
        seen.add(user_id)
        count += len(await notify(session, user_id=user_id, kind=kind, title=title,
                                  body=body, data=data, org_id=org_id, channels=channels))
    return count


async def org_member_ids(session: AsyncSession, org_id: uuid.UUID) -> list[uuid.UUID]:
    """Active staff of an org — the provider-side notification audience (§4 tenancy)."""
    return list((
        await session.execute(
            select(OrgStaff.user_id).where(OrgStaff.org_id == org_id,
                                           OrgStaff.status == "active")
        )
    ).scalars().all())


async def notify_org(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    kind: str,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
    exclude_user_id: uuid.UUID | None = None,
    channels: Sequence[str] = ("in_app",),
) -> int:
    """Notify a provider org's staff, skipping the actor who caused the event."""
    ids = await org_member_ids(session, org_id)
    if exclude_user_id is not None:
        ids = [i for i in ids if i != exclude_user_id]
    return await notify_users(session, ids, kind=kind, title=title, body=body,
                              data=data, org_id=org_id, channels=channels)


async def list_for_user(session: AsyncSession, user_id: uuid.UUID, *,
                        unread_only: bool = False, limit: int = 20,
                        offset: int = 0) -> tuple[list[Notification], int]:
    stmt = select(Notification).where(Notification.user_id == user_id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    total = (
        await session.execute(
            select(func.count()).select_from(stmt.subquery())
        )
    ).scalar_one()
    items = list((
        await session.execute(
            stmt.order_by(Notification.created_at.desc()).limit(limit).offset(offset)
        )
    ).scalars().all())
    return items, total


async def unread_count(session: AsyncSession, user_id: uuid.UUID) -> int:
    return (
        await session.execute(
            select(func.count()).select_from(Notification).where(
                Notification.user_id == user_id, Notification.read_at.is_(None))
        )
    ).scalar_one()


async def mark_read(session: AsyncSession, notification_id: uuid.UUID,
                    user_id: uuid.UUID) -> int:
    now = datetime.now(timezone.utc)
    result = await session.execute(
        update(Notification)
        .where(Notification.id == notification_id, Notification.user_id == user_id,
               Notification.read_at.is_(None))
        .values(read_at=now, updated_at=now)
    )
    await session.flush()
    return result.rowcount or 0


async def mark_all_read(session: AsyncSession, user_id: uuid.UUID) -> int:
    now = datetime.now(timezone.utc)
    result = await session.execute(
        update(Notification)
        .where(Notification.user_id == user_id, Notification.read_at.is_(None))
        .values(read_at=now, updated_at=now)
    )
    await session.flush()
    return result.rowcount or 0


def _send_email(settings: Settings, to_addr: str, title: str, body: str) -> None:
    """Blocking SMTP call — only ever run from `send_pending`, inside a worker thread."""
    msg = EmailMessage()
    msg["Subject"] = title
    msg["From"] = settings.smtp_from
    msg["To"] = to_addr
    msg.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as client:
        client.starttls(context=ssl.create_default_context())
        if settings.smtp_username:
            client.login(settings.smtp_username, settings.smtp_password)
        client.send_message(msg)


async def send_pending(session: AsyncSession, settings: Settings | None = None,
                       *, limit: int = 100) -> dict[str, int]:
    """§10 `send_notifications`: dispatch outbound rows, mark every attempt handled."""
    settings = settings or get_settings_cached()
    now = datetime.now(timezone.utc)
    pending = list((
        await session.execute(
            select(Notification)
            .where(Notification.sent_at.is_(None),
                   Notification.channel.in_(("email", "sms", "push")))
            .order_by(Notification.created_at)
            .limit(limit)
        )
    ).scalars().all())

    stats = {"sent": 0, "no_op": 0}
    for row in pending:
        addr = None
        if row.channel == "email" and settings.smtp_host:
            addr = (
                await session.execute(select(User.email).where(User.id == row.user_id))
            ).scalar_one_or_none()
        if addr:
            try:
                await anyio.to_thread.run_sync(
                    partial(_send_email, settings, addr, row.title, row.body))
                row.sent_at = now
                stats["sent"] += 1
                continue
            except (OSError, smtplib.SMTPException) as exc:
                logger.warning("email delivery failed notification=%s: %s", row.id, exc)
        # No mail infrastructure, or the send failed: the row is recorded rather than retried
        # forever, because the in_app copy is what clients actually read.
        row.sent_at = now
        stats["no_op"] += 1
    if pending:
        await session.flush()
    return stats
