"""Disputes (CONTRACTS §5.7/§8): the remediation path for a booking that went wrong.

Support owns the resolution; the money it decides goes through `commerce` so the order, the
payment and the booking refund state stay written by one writer. A booking only moves while the
state machine allows it: `confirmed`/`in_progress` become `disputed` when a complaint is opened,
and a `completed` booking keeps its status because the service was already delivered — the
dispute is about compensating for it, not un-completing it.
"""
from __future__ import annotations

import uuid
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
from app.models.crosscut import DISPUTE_KINDS, DISPUTE_STATUSES, Dispute
from app.models.identity import Organization, User
from app.services import booking as booking_svc
from app.services import commerce
from app.services.notifications import notify, notify_org

OPEN_STATUSES = ("open", "under_review")
RESOLVED_STATUSES = ("resolved_refund", "resolved_partial", "resolved_no_fault", "closed")
#: A draft/hold/expired/cancelled booking has nothing to complain about yet (§5.5 machine).
DISPUTABLE_BOOKING_STATUSES = ("confirmed", "in_progress", "completed", "disputed")


def _base_select() -> Select:
    return (
        select(Dispute, User.full_name.label("complainant_name"),
               Organization.name.label("org_name"))
        .join(User, User.id == Dispute.complainant_id)
        .join(Organization, Organization.id == Dispute.org_id)
    )


def row_to_dict(row: Any) -> dict:
    dispute: Dispute = row.Dispute
    return {
        "id": dispute.id, "booking_id": dispute.booking_id, "org_id": dispute.org_id,
        "complainant_id": dispute.complainant_id, "kind": dispute.kind,
        "description": dispute.description, "status": dispute.status,
        "resolution_note": dispute.resolution_note, "resolved_by": dispute.resolved_by,
        "resolved_at": dispute.resolved_at, "created_at": dispute.created_at,
        "refund_cents": dispute.refund_cents, "complainant_name": row.complainant_name,
        "org_name": row.org_name,
    }


async def load_row(session: AsyncSession, dispute_id: uuid.UUID) -> Any:
    row = (await session.execute(_base_select().where(Dispute.id == dispute_id))).first()
    if row is None:
        raise NotFound("Dispute not found")
    return row


async def open_dispute(session: AsyncSession, *, booking: Booking, kind: str,
                       description: str, complainant_id: uuid.UUID,
                       request: Request | None = None) -> Dispute:
    if kind not in DISPUTE_KINDS:
        raise ValidationFailed("Unknown dispute kind", details={"allowed": list(DISPUTE_KINDS)})
    if booking.status not in DISPUTABLE_BOOKING_STATUSES:
        raise InvalidStateTransition(
            f"A booking in status '{booking.status}' cannot be disputed")
    existing = (await session.execute(
        select(Dispute.id).where(Dispute.booking_id == booking.id,
                                 Dispute.status.in_(OPEN_STATUSES)).limit(1)
    )).scalar_one_or_none()
    if existing is not None:
        raise Conflict("This booking already has an open dispute",
                       details={"dispute_id": str(existing)})

    dispute = Dispute(booking_id=booking.id, org_id=booking.org_id,
                      complainant_id=complainant_id, kind=kind,
                      description=description.strip()[:4000], status="open")
    session.add(dispute)
    await session.flush()

    if "disputed" in booking.can_transition_to:
        await booking_svc.transition(session, booking, "disputed",
                                     actor_user_id=complainant_id, reason="dispute opened",
                                     request=request)
    await record_audit(session, request, action="dispute.opened", entity_type="dispute",
                       entity_id=dispute.id, after={"kind": kind,
                                                     "booking_id": str(booking.id)})

    if complainant_id == booking.customer_id:
        await notify_org(session, booking.org_id, kind="dispute.opened",
                         title="A customer opened a dispute",
                         body=dispute.description[:200],
                         data={"dispute_id": str(dispute.id),
                               "booking_id": str(booking.id)},
                         exclude_user_id=complainant_id)
    else:
        await notify(session, user_id=booking.customer_id, kind="dispute.opened",
                     title="The provider opened a dispute on your booking",
                     body=dispute.description[:200],
                     data={"dispute_id": str(dispute.id), "booking_id": str(booking.id)})
    return dispute


async def resolve(session: AsyncSession, dispute: Dispute, *, status: str,
                  resolution_note: str, actor_user_id: uuid.UUID,
                  refund_cents: int | None = None,
                  request: Request | None = None) -> tuple[Dispute, int]:
    """Support decision. Returns the dispute and the cents actually refunded."""
    if status not in DISPUTE_STATUSES:
        raise ValidationFailed("Unknown dispute status",
                               details={"allowed": list(DISPUTE_STATUSES)})
    if dispute.status not in OPEN_STATUSES:
        raise Conflict(f"Dispute is already {dispute.status}")

    booking = await session.get(Booking, dispute.booking_id)
    if booking is None:
        raise NotFound("Booking not found for dispute")

    if status == "under_review":
        if dispute.status != "open":
            raise InvalidStateTransition("Only an open dispute can be taken for review")
        before = dispute.status
        dispute.status = status
        dispute.resolution_note = resolution_note.strip()[:4000]
        await session.flush()
        await record_audit(session, request, action="dispute.under_review",
                           entity_type="dispute", entity_id=dispute.id,
                           before={"status": before}, after={"status": status})
        await notify(session, user_id=dispute.complainant_id, kind="dispute.under_review",
                     title="Your dispute is under review",
                     body=resolution_note[:200], data={"dispute_id": str(dispute.id)})
        return dispute, 0

    if status not in RESOLVED_STATUSES:
        raise ValidationFailed(f"Cannot move a dispute to {status}")

    outstanding = await commerce.outstanding_cents(session, booking)
    if status == "resolved_refund":
        wanted = outstanding
    elif status == "resolved_partial":
        if refund_cents is None:
            raise ValidationFailed("resolved_partial needs refund_cents")
        wanted = refund_cents
    else:
        if refund_cents:
            raise ValidationFailed(f"A {status} resolution cannot carry a refund")
        wanted = 0

    refunded = await commerce.refund_for_dispute(
        session, booking, amount_cents=wanted, actor_user_id=actor_user_id,
        reason=f"dispute {status}: {resolution_note.strip()[:300]}")

    dispute.status = status
    dispute.resolution_note = resolution_note.strip()[:4000]
    dispute.resolved_by = actor_user_id
    dispute.resolved_at = commerce.utcnow()
    dispute.refund_cents = refunded

    # The machine only allows disputed -> cancelled|completed; a full refund means the
    # service is treated as not delivered, any other outcome leaves it delivered.
    if booking.status == "disputed":
        to_status = "cancelled" if status == "resolved_refund" else "completed"
        await booking_svc.transition(session, booking, to_status, actor_user_id=actor_user_id,
                                     reason=f"dispute {status}", request=request)
        fulfillment = await commerce.fulfillment_for_booking(session, booking.id)
        if fulfillment is not None:
            await commerce.set_fulfillment_status(
                session, fulfillment, "failed" if to_status == "cancelled" else "completed",
                actor_user_id=actor_user_id)

    await session.flush()
    await record_audit(session, request, action=f"dispute.{status}", entity_type="dispute",
                       entity_id=dispute.id, after={"refund_cents": refunded,
                                                     "booking_status": booking.status})
    data = {"dispute_id": str(dispute.id), "booking_id": str(booking.id),
            "refund_cents": refunded}
    await notify(session, user_id=dispute.complainant_id, kind=f"dispute.{status}",
                 title=f"Your dispute was resolved: {status.replace('_', ' ')}",
                 body=dispute.resolution_note[:200], data=data)
    await notify_org(session, dispute.org_id, kind=f"dispute.{status}",
                     title=f"A dispute was resolved: {status.replace('_', ' ')}",
                     body=dispute.resolution_note[:200], data=data,
                     exclude_user_id=actor_user_id)
    return dispute, refunded


async def get_row_for_actor(session: AsyncSession, dispute_id: uuid.UUID, *, user_id: uuid.UUID,
                            org_ids: set[uuid.UUID],
                            is_platform_admin: bool) -> Any:
    row = await load_row(session, dispute_id)
    dispute = row.Dispute
    if is_platform_admin or dispute.complainant_id == user_id or dispute.org_id in org_ids:
        return row
    booking = await session.get(Booking, dispute.booking_id)
    if booking is not None and booking.customer_id == user_id:
        return row
    # Anyone else gets the same 403-equivalent answer as everywhere else (§4), without
    # confirming that the dispute exists.
    raise OwnershipRequired("You do not have access to this dispute")


async def list_disputes(session: AsyncSession, *, user_id: uuid.UUID, org_ids: set[uuid.UUID],
                        is_platform_admin: bool, support: bool = False,
                        provider: bool = False, status: str | None = None,
                        kind: str | None = None, org_id: uuid.UUID | None = None,
                        limit: int = 20,
                        offset: int = 0) -> tuple[list[dict], int]:
    """Support queue sees everything; provider sees its own org; customer sees own bookings."""
    stmt = _base_select()
    if not (is_platform_admin or support):
        if provider:
            stmt = stmt.where(Dispute.org_id.in_(org_ids))
        else:
            stmt = (stmt.join(Booking, Booking.id == Dispute.booking_id)
                    .where(Booking.customer_id == user_id))
    if status:
        stmt = stmt.where(Dispute.status == status)
    if kind:
        stmt = stmt.where(Dispute.kind == kind)
    if org_id is not None:
        stmt = stmt.where(Dispute.org_id == org_id)
    total = (await session.execute(
        select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()
    rows = (await session.execute(stmt.order_by(Dispute.created_at.desc(), Dispute.id)
                                  .limit(limit).offset(offset))).all()
    return [row_to_dict(r) for r in rows], int(total)
