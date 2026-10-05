"""`/api/v1/bookings` — hold, book, fulfil, cancel (CONTRACTS §5.4/§5.5/§8).

The router is the orchestrator between the two writers of money state: `booking` owns the
status machine and the capacity lock, `commerce` owns the order that carries the booking.
Keeping that composition here (rather than inside either service) avoids a circular import
between them.
"""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select

from app.api.v1.deps import ActorDep, PageDep, envelope, idempotent_write, parse_bound
from app.core.deps import SessionDep
from app.core.errors import OwnershipRequired, ValidationFailed
from app.models.booking import BOOKING_STATUSES, Booking, BookingStatusEvent
from app.models.capacity import CapacityDefinition, CapacityResource
from app.models.crosscut import Review
from app.models.identity import Organization, User
from app.models.marketplace import Offer
from app.schemas.booking import (
    BookingCreate,
    BookingOut,
    BookingStatusEventOut,
    CancelRequest,
    HoldRequest,
)
from app.schemas.commerce import FulfillmentOut, OrderOut
from app.schemas.marketplace import OfferOut
from app.services import booking as booking_svc
from app.services import commerce
from app.services.notifications import notify, notify_org

router = APIRouter(prefix="/bookings", tags=["bookings"])


async def _payload(session, booking: Booking) -> dict:
    """Booking plus the joined context the UI needs in one round trip (§8)."""
    offer = await session.get(Offer, booking.offer_id)
    order = await commerce.order_for_booking(session, booking.id)
    fulfillment = await commerce.fulfillment_for_booking(session, booking.id)
    definition = await session.get(CapacityDefinition, booking.definition_id)
    resource = await session.get(CapacityResource, definition.resource_id) if definition else None
    org = await session.get(Organization, booking.org_id)
    customer = await session.get(User, booking.customer_id)
    reviewed = (await session.execute(
        select(func.count()).select_from(Review).where(Review.booking_id == booking.id)
    )).scalar_one()

    data = BookingOut.model_validate(booking).model_dump(mode="json")
    data["order_id"] = str(order.id) if order else None
    data["offer"] = (OfferOut.model_validate(offer).model_dump(mode="json") if offer else None)
    data["order"] = (OrderOut.model_validate(order).model_dump(mode="json") if order else None)
    data["fulfillment"] = (FulfillmentOut.model_validate(fulfillment).model_dump(mode="json")
                           if fulfillment else None)
    data["offer_title"] = offer.title if offer else None
    data["org_name"] = org.name if org else None
    data["resource_name"] = resource.name if resource else None
    data["customer_name"] = customer.full_name if customer else None
    data["review_submitted"] = reviewed > 0
    return data


async def _load(session, actor, booking_id: uuid.UUID) -> Booking:
    return await booking_svc.get_for_actor(
        session, booking_id, user_id=actor.user_id, org_ids=actor.org_ids,
        is_platform_admin=actor.is_platform_admin)


def _require_manager(actor, booking: Booking) -> None:
    if not actor.can_manage_org(booking.org_id):
        raise OwnershipRequired("Only the providing organization can do this")


async def _commit_money(session, request: Request, booking: Booking) -> None:
    """Every live booking carries its order; a confirmed one also carries the fulfilment record."""
    await commerce.create_order(session, booking=booking, buyer_id=booking.customer_id,
                                request=request)
    if booking.status == "confirmed":
        await commerce.ensure_fulfillment(session, booking)


# --------------------------------------------------------------------------- creation


@router.post("/hold", status_code=201)
async def create_hold(body: HoldRequest, request: Request, session: SessionDep,
                      actor: ActorDep) -> dict:
    """Temporarily reserve capacity (§5.5); a repeat with the same fingerprint reuses the hold."""
    payload = body.model_dump(mode="json")

    async def build() -> dict:
        hold = await booking_svc.create_hold(
            session, offer_id=body.offer_id, start=body.window_start, end=body.window_end,
            quantity=body.quantity, customer_id=actor.user_id, request=request,
            request_fingerprint=body.request_fingerprint)
        return await _payload(session, hold)

    return await idempotent_write(session, request, route="POST /bookings/hold",
                                  actor_id=actor.user_id, payload=payload, status_code=201,
                                  build=build)


@router.post("", status_code=201)
async def create_booking(body: BookingCreate, request: Request, session: SessionDep,
                        actor: ActorDep) -> dict:
    """Convert a hold, or book directly from an offer (§5.5 instant vs request_confirm)."""
    payload = body.model_dump(mode="json")

    async def build() -> dict:
        booking = await booking_svc.create_booking(
            session, customer_id=actor.user_id, request=request, hold_id=body.hold_id,
            offer_id=body.offer_id, start=body.window_start, end=body.window_end,
            quantity=body.quantity)
        await _commit_money(session, request, booking)
        if booking.status == "draft":
            await notify_org(session, booking.org_id, kind="booking.requested",
                             title="New booking request",
                             body="A customer is waiting for confirmation on one of your offers.",
                             data={"booking_id": str(booking.id)})
        return await _payload(session, booking)

    return await idempotent_write(session, request, route="POST /bookings",
                                  actor_id=actor.user_id, payload=payload, status_code=201,
                                  build=build)


# --------------------------------------------------------------------------- reads


@router.get("")
async def list_bookings(session: SessionDep, actor: ActorDep, page: PageDep,
                        status: Annotated[str | None, Query(max_length=24)] = None,
                        mine: bool = False, provider: bool = False,
                        date_from: Annotated[str | None, Query(alias="from")] = None,
                        date_to: Annotated[str | None, Query(alias="to")] = None,
                        offer_id: uuid.UUID | None = None) -> dict:
    """`provider=true` is the org workspace view; anything else is the caller's own bookings."""
    if status is not None and status not in BOOKING_STATUSES:
        raise ValidationFailed("Unknown booking status",
                               details={"allowed": list(BOOKING_STATUSES)})
    stmt = select(Booking)
    if provider and (actor.org_ids or actor.is_platform_admin):
        if not actor.is_platform_admin:
            stmt = stmt.where(Booking.org_id.in_(actor.org_ids))
    else:
        stmt = stmt.where(Booking.customer_id == actor.user_id)
    if status:
        stmt = stmt.where(Booking.status == status)
    if offer_id:
        stmt = stmt.where(Booking.offer_id == offer_id)
    if date_from or date_to:
        if not (date_from and date_to):
            raise ValidationFailed("`from` and `to` must be provided together")
        stmt = stmt.where(Booking.window_end > parse_bound(date_from),
                          Booking.window_start < parse_bound(date_to, end=True))

    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await session.execute(stmt.order_by(Booking.window_start.desc(), Booking.id)
                                  .limit(page.limit).offset(page.offset))).scalars().all()
    return envelope([await _payload(session, b) for b in rows], int(total), page)


@router.get("/{booking_id}")
async def get_booking(booking_id: uuid.UUID, session: SessionDep, actor: ActorDep) -> dict:
    return await _payload(session, await _load(session, actor, booking_id))


@router.get("/{booking_id}/timeline")
async def booking_timeline(booking_id: uuid.UUID, session: SessionDep,
                           actor: ActorDep, page: PageDep) -> dict:
    await _load(session, actor, booking_id)
    rows = list((await session.execute(
        select(BookingStatusEvent).where(BookingStatusEvent.booking_id == booking_id)
        .order_by(BookingStatusEvent.created_at, BookingStatusEvent.id))).scalars().all())
    items = [BookingStatusEventOut.model_validate(e).model_dump(mode="json") for e in rows]
    return envelope(items, len(items), page)


# --------------------------------------------------------------------------- transitions


async def _move(session: SessionDep, request: Request, booking: Booking, to_status: str, *,
                actor_user_id: uuid.UUID, reason: str) -> Booking:
    return await booking_svc.transition(session, booking, to_status,
                                        actor_user_id=actor_user_id, reason=reason,
                                        request=request)


@router.post("/{booking_id}/confirm")
async def confirm_booking(booking_id: uuid.UUID, request: Request, session: SessionDep,
                          actor: ActorDep) -> dict:
    """Provider acceptance of a `request_confirm` booking; the customer's pay-path confirms too."""
    booking = await _load(session, actor, booking_id)
    if not (actor.can_manage_org(booking.org_id) or booking.customer_id == actor.user_id):
        raise OwnershipRequired("Only the customer or the providing organization can confirm")
    await booking_svc.confirm_request(session, booking, actor_user_id=actor.user_id,
                                      request=request)
    await _commit_money(session, request, booking)
    if booking.customer_id != actor.user_id:
        await notify(session, user_id=booking.customer_id, kind="booking.confirmed",
                     title="Booking confirmed",
                     body="The provider has confirmed your booking.",
                     data={"booking_id": str(booking.id)})
    return await _payload(session, booking)


@router.post("/{booking_id}/start")
async def start_booking(booking_id: uuid.UUID, request: Request, session: SessionDep,
                        actor: ActorDep) -> dict:
    booking = await _load(session, actor, booking_id)
    _require_manager(actor, booking)
    await _move(session, request, booking, "in_progress", actor_user_id=actor.user_id,
                reason="service started")
    fulfillment = await commerce.ensure_fulfillment(session, booking)
    await commerce.set_fulfillment_status(session, fulfillment, "in_progress",
                                          actor_user_id=actor.user_id, request=request)
    return await _payload(session, booking)


@router.post("/{booking_id}/complete")
async def complete_booking(booking_id: uuid.UUID, request: Request, session: SessionDep,
                           actor: ActorDep) -> dict:
    booking = await _load(session, actor, booking_id)
    _require_manager(actor, booking)
    await _move(session, request, booking, "completed", actor_user_id=actor.user_id,
                reason="service completed")
    fulfillment = await commerce.ensure_fulfillment(session, booking)
    await commerce.set_fulfillment_status(session, fulfillment, "completed",
                                          actor_user_id=actor.user_id, request=request)
    if booking.customer_id != actor.user_id:
        await notify(session, user_id=booking.customer_id, kind="booking.completed",
                     title="Booking completed",
                     body="The provider marked your booking as completed.",
                     data={"booking_id": str(booking.id)})
    return await _payload(session, booking)


@router.post("/{booking_id}/cancel")
async def cancel_booking(booking_id: uuid.UUID, body: CancelRequest, request: Request,
                         session: SessionDep, actor: ActorDep) -> dict:
    """Cancel with the offer's refund policy applied (§5.6); the freed capacity is immediate."""
    booking = await _load(session, actor, booking_id)
    if booking.customer_id != actor.user_id and not actor.can_manage_org(booking.org_id):
        raise OwnershipRequired("Only the customer or the providing organization can cancel")
    cancelled, refunded = await booking_svc.cancel(
        session, booking, actor_user_id=actor.user_id, reason=body.reason, request=request)
    payload = await _payload(session, cancelled)
    payload["refund_cents"] = refunded
    if booking.customer_id != actor.user_id:
        await notify(session, user_id=booking.customer_id, kind="booking.cancelled",
                     title="Booking cancelled by the provider",
                     body=f"Your booking was cancelled. Refund: {refunded} {booking.currency} cents.",
                     data={"booking_id": str(booking.id), "refund_cents": refunded})
    return payload
