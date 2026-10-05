"""Conversations (CONTRACTS §5.7/§8): the buyer <-> provider thread behind a booking or listing.

One thread per (kind, ref, customer), so the desktop can open "the" conversation for a booking
without racing a create call. Read receipts are stamped by whoever lists the thread, which is
how the unread badge on the list view stays honest without a second endpoint.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import Request
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.errors import (
    Conflict,
    InvalidStateTransition,
    NotFound,
    OwnershipRequired,
    ValidationFailed,
)
from app.models.booking import Booking
from app.models.crosscut import CONVERSATION_STATUSES, Conversation, Message
from app.models.identity import Organization, User
from app.models.marketplace import Offer
from app.services import marketplace as market
from app.services.notifications import notify, notify_org

KINDS = ("booking", "offer", "other")


def _select() -> Select:
    return (
        select(Conversation, User.full_name.label("customer_name"),
               Organization.name.label("provider_org_name"))
        .join(User, User.id == Conversation.customer_id)
        .join(Organization, Organization.id == Conversation.provider_org_id)
    )


def row_to_dict(row: Any, *, view: str = "customer") -> dict:
    conv: Conversation = row.Conversation
    return {
        "id": conv.id, "kind": conv.kind, "ref_id": conv.ref_id, "org_id": conv.org_id,
        "customer_id": conv.customer_id, "provider_org_id": conv.provider_org_id,
        "status": conv.status, "last_message_at": conv.last_message_at,
        "created_at": conv.created_at,
        "peer_name": (row.provider_org_name if view == "customer" else row.customer_name),
    }


async def _attach_latest(session: AsyncSession, items: list[dict], *,
                         user_id: uuid.UUID) -> list[dict]:
    """Fill `last_message_body` and `unread_count` for one page of threads in two queries."""
    if not items:
        return items
    ids = [uuid.UUID(str(i["id"])) for i in items]
    latest: dict[uuid.UUID, str] = {}
    rows = (await session.execute(
        select(Message.conversation_id, Message.body).where(
            Message.conversation_id.in_(ids)).order_by(Message.created_at, Message.id)
    )).all()
    for conversation_id, body in rows:
        latest[conversation_id] = body

    key = str(user_id)
    unread = dict((await session.execute(
        select(Message.conversation_id, func.count()).where(
            Message.conversation_id.in_(ids), Message.sender_id != user_id,
            Message.read_receipts.op("->>")(key).is_(None)).group_by(Message.conversation_id)
    )).all())
    for item in items:
        item_id = uuid.UUID(str(item["id"]))
        item["last_message_body"] = latest.get(item_id)
        item["unread_count"] = int(unread.get(item_id, 0))
    return items


def message_to_dict(message: Message, *, sender_name: str | None = None) -> dict:
    return {
        "id": message.id, "conversation_id": message.conversation_id,
        "sender_id": message.sender_id, "body": message.body, "is_system": message.is_system,
        "read_receipts": message.read_receipts, "created_at": message.created_at,
        "sender_name": sender_name,
    }


async def load_row(session: AsyncSession, conversation_id: uuid.UUID) -> Any:
    row = (await session.execute(_select().where(Conversation.id == conversation_id))).first()
    if row is None:
        raise NotFound("Conversation not found")
    return row


def _participant(row: Any, user_id: uuid.UUID, org_ids: set[uuid.UUID]) -> bool:
    conv: Conversation = row.Conversation
    return conv.customer_id == user_id or conv.provider_org_id in org_ids


async def get_for_actor(session: AsyncSession, conversation_id: uuid.UUID, *, user_id: uuid.UUID,
                        org_ids: set[uuid.UUID], is_platform_admin: bool) -> Any:
    row = await load_row(session, conversation_id)
    if is_platform_admin or _participant(row, user_id, org_ids):
        return row
    raise OwnershipRequired("You do not have access to this conversation")


async def _existing(session: AsyncSession, *, kind: str, ref_id: uuid.UUID | None,
                    customer_id: uuid.UUID, provider_org_id: uuid.UUID) -> Conversation | None:
    stmt = select(Conversation).where(Conversation.kind == kind,
                                      Conversation.customer_id == customer_id,
                                      Conversation.provider_org_id == provider_org_id)
    # A thread without a reference is unique per (customer, org); Postgres treats the NULL
    # ref as distinct, so the reuse check has to match on the org instead.
    stmt = stmt.where(Conversation.ref_id.is_(None) if ref_id is None
                      else Conversation.ref_id == ref_id)
    return (await session.execute(stmt.limit(1))).scalar_one_or_none()


async def open_conversation(session: AsyncSession, *, user_id: uuid.UUID,
                            org_ids: set[uuid.UUID], is_platform_admin: bool, kind: str,
                            ref_id: uuid.UUID | None = None,
                            org_id: uuid.UUID | None = None,
                            initial_body: str | None = None) -> tuple[Conversation, bool]:
    """Return the thread for this counterparty, creating it on first use."""
    if kind not in KINDS:
        raise ValidationFailed("Unknown conversation kind", details={"allowed": list(KINDS)})

    if kind == "booking":
        if ref_id is None:
            raise ValidationFailed("A booking conversation needs booking ref_id")
        booking = await session.get(Booking, ref_id)
        if booking is None:
            raise NotFound("Booking not found")
        if not (booking.customer_id == user_id or booking.org_id in org_ids
                or is_platform_admin):
            raise OwnershipRequired("Only the two parties of the booking can talk")
        customer_id, provider_org_id = booking.customer_id, booking.org_id
    elif kind == "offer":
        if ref_id is None:
            raise ValidationFailed("An offer conversation needs offer ref_id")
        offer: Offer = (await market.get_offer_row(session, ref_id)).Offer
        if offer.org_id in org_ids:
            # A provider has no counterparty to ask: their side of a thread starts from a booking.
            raise ValidationFailed("A provider cannot open an enquiry against their own listing")
        customer_id, provider_org_id = user_id, offer.org_id
    else:
        target = org_id or ref_id
        if target is None:
            raise ValidationFailed("An organization conversation needs org_id")
        if await session.get(Organization, target) is None:
            raise NotFound("Organization not found")
        customer_id, provider_org_id = user_id, target

    found = await _existing(session, kind=kind, ref_id=ref_id if kind != "other" else None,
                            customer_id=customer_id, provider_org_id=provider_org_id)
    if found is not None:
        return found, False

    conv = Conversation(kind=kind, ref_id=ref_id, org_id=provider_org_id,
                        customer_id=customer_id, provider_org_id=provider_org_id,
                        status="open")
    session.add(conv)
    await session.flush()
    if initial_body:
        await post_message(session, conv, sender_id=user_id, body=initial_body)
    return conv, True


async def post_message(session: AsyncSession, conversation: Conversation, *, sender_id: uuid.UUID,
                       body: str) -> Message:
    if conversation.status != "open":
        raise InvalidStateTransition(
            f"Cannot write to a {conversation.status} conversation")
    text = body.strip()
    if not text:
        raise ValidationFailed("Message body cannot be empty")
    if len(text) > 4000:
        raise ValidationFailed("Message body is limited to 4000 characters")

    message = Message(conversation_id=conversation.id, sender_id=sender_id, body=text,
                      is_system=False, read_receipts={})
    session.add(message)
    conversation.last_message_at = datetime.now(timezone.utc)
    await session.flush()

    if sender_id == conversation.customer_id:
        await notify_org(session, conversation.provider_org_id, kind="message.created",
                         title="New message about your listing", body=text[:200],
                         data={"conversation_id": str(conversation.id)},
                         exclude_user_id=sender_id)
    else:
        await notify(session, user_id=conversation.customer_id, kind="message.created",
                     title="New message from the provider", body=text[:200],
                     data={"conversation_id": str(conversation.id)})
    return message


async def list_messages(session: AsyncSession, conversation: Conversation, *, user_id: uuid.UUID,
                        limit: int = 50, offset: int = 0,
                        mark_read: bool = True) -> tuple[list[dict], int]:
    """The thread itself, oldest first; reading it stamps the caller's receipt."""
    stmt = (
        select(Message, User.full_name.label("sender_name"))
        .join(User, User.id == Message.sender_id)
        .where(Message.conversation_id == conversation.id)
    )
    total = (await session.execute(
        select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()
    rows = (await session.execute(stmt.order_by(Message.created_at, Message.id)
                                  .limit(limit).offset(offset))).all()

    if mark_read:
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        key = str(user_id)
        for row in rows:
            if row.Message.sender_id != user_id and row.Message.read_receipts.get(key) is None:
                receipts = dict(row.Message.read_receipts)
                receipts[key] = now
                row.Message.read_receipts = receipts
        await session.flush()

    return [message_to_dict(row.Message, sender_name=row.sender_name) for row in rows], int(total)


async def set_status(session: AsyncSession, conversation: Conversation, *, to_status: str,
                     actor_user_id: uuid.UUID,
                     request: Request | None = None) -> Conversation:
    """Participants close or reopen their thread; `archived` is the terminal state."""
    if to_status not in CONVERSATION_STATUSES:
        raise ValidationFailed("Unknown conversation status",
                               details={"allowed": list(CONVERSATION_STATUSES)})
    if to_status == conversation.status:
        raise Conflict(f"Conversation is already {to_status}")
    if conversation.status == "archived":
        raise InvalidStateTransition("An archived conversation cannot be reopened")
    before = conversation.status
    conversation.status = to_status
    await session.flush()
    await record_audit(session, request, action=f"conversation.{to_status}",
                       entity_type="conversation", entity_id=conversation.id,
                       before={"status": before}, after={"status": to_status},
                       actor_user_id=actor_user_id)
    return conversation


async def list_for_actor(session: AsyncSession, *, user_id: uuid.UUID, org_ids: set[uuid.UUID],
                         is_platform_admin: bool, provider: bool = False,
                         status: str | None = None, limit: int = 20,
                         offset: int = 0) -> tuple[list[dict], int]:
    stmt = _select()
    if provider:
        stmt = stmt.where(Conversation.provider_org_id.in_(org_ids))
    elif not is_platform_admin:
        stmt = stmt.where(Conversation.customer_id == user_id)
    if status:
        stmt = stmt.where(Conversation.status == status)
    total = (await session.execute(
        select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()
    rows = (await session.execute(stmt.order_by(Conversation.last_message_at.desc(),
                                                Conversation.id)
                                  .limit(limit).offset(offset))).all()
    view = "provider" if provider else "customer"
    return await _attach_latest(session, [row_to_dict(r, view=view) for r in rows],
                                user_id=user_id), int(total)
