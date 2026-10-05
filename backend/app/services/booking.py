"""Booking engine (CONTRACTS §5.4/§5.5) — the only writer of overlap state.

Concurrency protocol, in order, inside one transaction:
  1. `pg_advisory_xact_lock(hashtext(definition_id::text))` — every writer of a definition
     serialises on the same key, so two racing holds cannot both see free capacity.
  2. Re-read committed quantity in-lock (never a client-observed value).
  3. Compare against `definition.max_quantity`; exceed => 409.
  4. Insert the booking row in that same transaction; the lock releases at commit.

Advisory locks are used rather than a GiST exclusion constraint because `btree_gist` is a
contrib extension and the local/test Postgres build ships core only (§5.5). Redis is never
consulted for correctness.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Request
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.errors import (
    CapacityExceeded,
    Conflict,
    HoldExpired,
    InvalidStateTransition,
    NoAvailability,
    NotFound,
    OwnershipRequired,
    ValidationFailed,
)
from app.models.booking import (
    ACTIVE_BOOKING_STATUSES,
    BOOKING_TRANSITIONS,
    Booking,
    BookingStatusEvent,
)
from app.models.capacity import CapacityDefinition, CapacityResource
from app.models.marketplace import Offer
from app.services import capacity as cap
from app.services.notifications import notify

MIN_HOLD_MINUTES = 5


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _wire(value: datetime) -> str:
    """§1 timestamp shape for error payloads too: a `+00:00` is a second format clients must not parse."""
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


async def lock_definition(session: AsyncSession, definition_id: uuid.UUID) -> None:
    """Transaction-scoped advisory lock; released automatically at commit/rollback."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(CAST(:d AS text)))"),
        {"d": str(definition_id)},
    )


async def load_bookable_offer(session: AsyncSession, offer_id: uuid.UUID) -> tuple[Offer, CapacityDefinition, CapacityResource]:
    offer = await session.get(Offer, offer_id)
    if offer is None:
        raise NotFound("Offer not found")
    definition = await session.get(CapacityDefinition, offer.definition_id)
    if definition is None or not definition.is_active:
        raise NotFound("Capacity definition is not available")
    resource = await session.get(CapacityResource, definition.resource_id)
    if resource is None:
        raise NotFound("Capacity resource not found")
    return offer, definition, resource


def price_cents(offer: Offer, definition: CapacityDefinition, quantity: int,
                start: datetime, end: datetime) -> tuple[int, int]:
    """Return (unit_amount_cents, total_cents) with unit*quantity == total (§5.4 invariant).

    `per_unit_time` folds the duration into the unit price so the stored row still
    satisfies the check constraint that ties total to unit times quantity.
    """
    if offer.pricing_mode == "flat":
        return offer.unit_amount_cents, offer.unit_amount_cents
    if offer.pricing_mode == "per_unit_time":
        hours = max(1, round((end - start).total_seconds() / 3600))
        unit = offer.unit_amount_cents * hours
    else:
        unit = offer.unit_amount_cents
    return unit, unit * quantity


def _validate_request_window(offer: Offer, definition: CapacityDefinition,
                             start: datetime, end: datetime, quantity: int) -> None:
    now = utcnow()
    if end <= start:
        raise ValidationFailed("window_end must be after window_start",
                               details={"field_errors": [{"field": "window_end",
                                                          "message": "must be after window_start"}]})
    if start < now:
        raise ValidationFailed("Bookings cannot start in the past")
    if quantity < (offer.min_quantity or definition.min_quantity):
        raise ValidationFailed("quantity is below the offer minimum",
                               details={"min_quantity": offer.min_quantity or definition.min_quantity})
    if quantity > (offer.max_quantity or definition.max_quantity):
        raise ValidationFailed("quantity exceeds the offer maximum",
                               details={"max_quantity": offer.max_quantity or definition.max_quantity})
    lead = (start - now).total_seconds() / 60
    if lead < offer.min_lead_time_minutes:
        raise ValidationFailed(
            "Booking is inside the offer's minimum lead time",
            details={"required_lead_minutes": offer.min_lead_time_minutes,
                     "provided_lead_minutes": int(lead)})
    if offer.max_lead_time_days and lead > offer.max_lead_time_days * 1440:
        raise ValidationFailed(
            "Booking is beyond the offer's maximum booking horizon",
            details={"max_lead_time_days": offer.max_lead_time_days})
    duration = int((end - start).total_seconds() / 60)
    if offer.min_duration_minutes and duration < offer.min_duration_minutes:
        raise ValidationFailed("Duration is below the offer minimum",
                               details={"min_duration_minutes": offer.min_duration_minutes})
    if offer.max_duration_minutes and duration > offer.max_duration_minutes:
        raise ValidationFailed("Duration exceeds the offer maximum",
                               details={"max_duration_minutes": offer.max_duration_minutes})


async def _ensure_window_open(session: AsyncSession, offer: Offer,
                              definition: CapacityDefinition, resource: CapacityResource,
                              start: datetime, end: datetime, *,
                              exclude_booking_id: uuid.UUID | None = None) -> None:
    """The requested range must sit inside a single published availability window."""
    windows = await cap.expand_free_windows(session, definition, resource, start, end,
                                             exclude_booking_id=exclude_booking_id)
    for w in windows:
        if w.start <= start and end <= w.end:
            return
    raise NoAvailability(
        "The requested time is outside the published availability for this capacity",
        details={"window_start": _wire(start), "window_end": _wire(end),
                 "available": [{"start": _wire(w.start), "end": _wire(w.end),
                               "free_quantity": w.quantity} for w in windows[:5]]})


async def _reserve(session: AsyncSession, *, offer: Offer, definition: CapacityDefinition,
                   resource: CapacityResource, start: datetime, end: datetime,
                   quantity: int, status: str, customer_id: uuid.UUID,
                   created_by: uuid.UUID, org_id: uuid.UUID,
                   request_fingerprint: uuid.UUID | None,
                   hold_minutes: int, meta: dict[str, Any],
                   request: Request | None,
                   exclude_booking_id: uuid.UUID | None = None) -> Booking:
    """Lock -> revalidate -> insert, all in the caller's transaction (§5.5 steps 1-4)."""
    await lock_definition(session, definition.id)

    await _ensure_window_open(session, offer, definition, resource, start, end,
                              exclude_booking_id=exclude_booking_id)

    booked = await cap.committed_quantity(session, definition.id, start, end,
                                          exclude_booking_id=exclude_booking_id)
    ceiling = definition.max_quantity
    requested_total = booked + quantity
    if requested_total > ceiling:
        # §5.5 step 3: scheduled capacity reports "no slot left"; fungible quantity reports
        # "would exceed the ceiling". Same arithmetic, different client-facing meaning.
        if resource.capacity_mode == "scheduled":
            raise NoAvailability(
                "No capacity remains for this time range",
                details={"max_quantity": ceiling, "committed": booked,
                         "requested": quantity})
        raise CapacityExceeded(
            "Requested quantity exceeds the capacity available for this time range",
            details={"max_quantity": ceiling, "committed": booked, "requested": quantity})

    unit_cents, total_cents = price_cents(offer, definition, quantity, start, end)
    now = utcnow()
    booking = Booking(
        offer_id=offer.id,
        definition_id=definition.id,
        org_id=org_id,
        customer_id=customer_id,
        created_by_user_id=created_by,
        status=status,
        payment_status="unpaid" if offer.requires_payment else "not_required",
        window_start=start,
        window_end=end,
        quantity=quantity,
        unit_amount_cents=unit_cents,
        currency=offer.currency,
        total_cents=total_cents,
        hold_expires_at=now + timedelta(minutes=max(MIN_HOLD_MINUTES, hold_minutes))
        if status == "hold" else None,
        request_fingerprint=request_fingerprint,
        confirmed_at=now if status == "confirmed" else None,
        meta=meta,
    )
    session.add(booking)
    await session.flush()
    session.add(BookingStatusEvent(
        booking_id=booking.id, from_status=None, to_status=status,
        actor_user_id=created_by, reason="hold created" if status == "hold" else "booking created",
        meta={"quantity": quantity}))
    await record_audit(session, request, action=f"booking.{status}", entity_type="booking",
                       entity_id=booking.id,
                       after={"offer_id": str(offer.id), "quantity": quantity,
                              "total_cents": total_cents})
    return booking


async def create_hold(session: AsyncSession, *, offer_id: uuid.UUID, start: datetime,
                      end: datetime, quantity: int, customer_id: uuid.UUID,
                      request: Request | None,
                      request_fingerprint: uuid.UUID | None = None,
                      fingerprint_window_minutes: int = 240) -> Booking:
    """Reserve temporarily. A retried hold with the same fingerprint reuses the live hold."""
    offer, definition, resource = await load_bookable_offer(session, offer_id)
    if offer.status != "published":
        raise Conflict("Offer is not published", details={"status": offer.status})
    _validate_request_window(offer, definition, start, end, quantity)

    if request_fingerprint is not None:
        existing = (
            await session.execute(
                select(Booking).where(
                    Booking.definition_id == definition.id,
                    Booking.request_fingerprint == request_fingerprint,
                    Booking.status == "hold",
                ).limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

    return await _reserve(
        session, offer=offer, definition=definition, resource=resource,
        start=start, end=end, quantity=quantity, status="hold",
        customer_id=customer_id, created_by=customer_id, org_id=offer.org_id,
        request_fingerprint=request_fingerprint, hold_minutes=offer.hold_minutes,
        meta={"source": "hold"}, request=request)


async def create_booking(session: AsyncSession, *, customer_id: uuid.UUID,
                         request: Request | None, hold_id: uuid.UUID | None = None,
                         offer_id: uuid.UUID | None = None, start: datetime | None = None,
                         end: datetime | None = None, quantity: int = 1,
                         source_match_id: uuid.UUID | None = None) -> Booking:
    """Confirm a hold, or book directly from an offer."""
    if hold_id is not None:
        hold = await session.get(Booking, hold_id)
        if hold is None:
            raise NotFound("Hold not found")
        if hold.customer_id != customer_id:
            raise OwnershipRequired("Hold belongs to another customer")
        if hold.status != "hold":
            raise InvalidStateTransition(f"Hold is in status '{hold.status}' and cannot be booked")
        if hold.hold_expires_at is not None and hold.hold_expires_at <= utcnow():
            await _mark_expired(session, hold, actor_user_id=customer_id, reason="expiry at booking")
            raise HoldExpired("This hold has expired; request a new one")

        offer, definition, resource = await load_bookable_offer(session, hold.offer_id)
        booked = await _reserve(
            session, offer=offer, definition=definition, resource=resource,
            start=hold.window_start, end=hold.window_end, quantity=hold.quantity,
            # §5.5: instant offers land confirmed; request_confirm offers become a `draft`
            # awaiting the provider — never a second hold (that would double-count capacity).
            status="confirmed" if offer.booking_mode == "instant" else "draft",
            customer_id=customer_id, created_by=customer_id, org_id=hold.org_id,
            request_fingerprint=None, hold_minutes=offer.hold_minutes,
            meta={"source": "hold_conversion", "hold_id": str(hold.id),
                  "match_id": str(source_match_id) if source_match_id else None},
            # The hold's own units are the ones being converted; counting them as foreign
            # demand would refuse the customer their own reservation.
            exclude_booking_id=hold.id,
            request=request)
        booked.source_match_id = source_match_id
        booked.payment_status = hold.payment_status
        await cancel_internal(session, hold, actor_user_id=customer_id,
                              reason="converted to booking", silent=True)
        await session.flush()
        if booked.status == "confirmed":
            await notify(session, user_id=customer_id, kind="booking.confirmed",
                         title="Booking confirmed",
                         body=f"Your booking for {offer.title} is confirmed.",
                         data={"booking_id": str(booked.id)})
        return booked

    if offer_id is None or start is None or end is None:
        raise ValidationFailed("Provide hold_id, or offer_id with window_start and window_end")
    offer, definition, resource = await load_bookable_offer(session, offer_id)
    if offer.status != "published":
        raise Conflict("Offer is not published", details={"status": offer.status})
    _validate_request_window(offer, definition, start, end, quantity)
    booked = await _reserve(
        session, offer=offer, definition=definition, resource=resource,
        start=start, end=end, quantity=quantity,
        status="confirmed" if offer.booking_mode == "instant" else "draft",
        customer_id=customer_id, created_by=customer_id, org_id=offer.org_id,
        request_fingerprint=None, hold_minutes=offer.hold_minutes,
        meta={"source": "direct", "match_id": str(source_match_id) if source_match_id else None},
        request=request)
    booked.source_match_id = source_match_id
    await session.flush()
    return booked


async def transition(session: AsyncSession, booking: Booking, to_status: str, *,
                     actor_user_id: uuid.UUID, reason: str | None = None,
                     request: Request | None = None) -> Booking:
    """Enforce the §5.5 state machine; anything outside the table is a 400."""
    allowed = BOOKING_TRANSITIONS.get(booking.status, frozenset())
    if to_status not in allowed:
        raise InvalidStateTransition(
            f"Cannot move a booking from '{booking.status}' to '{to_status}'",
            details={"from": booking.status, "to": to_status,
                     "allowed": sorted(allowed)})
    if booking.status == "hold" and to_status == "confirmed":
        if booking.hold_expires_at is not None and booking.hold_expires_at <= utcnow():
            await _mark_expired(session, booking, actor_user_id=actor_user_id,
                                reason="expiry before confirmation")
            raise HoldExpired("This hold has expired")

    now = utcnow()
    from_status = booking.status
    booking.status = to_status
    if to_status == "confirmed":
        booking.confirmed_at = booking.confirmed_at or now
        booking.hold_expires_at = None
    elif to_status == "in_progress":
        booking.started_at = now
    elif to_status == "completed":
        booking.completed_at = now
    elif to_status == "disputed":
        booking.dispute_opened_at = now
    session.add(BookingStatusEvent(booking_id=booking.id, from_status=from_status,
                                   to_status=to_status, actor_user_id=actor_user_id,
                                   reason=reason))
    await session.flush()
    await record_audit(session, request, action=f"booking.{to_status}", entity_type="booking",
                       entity_id=booking.id, before={"status": from_status},
                       after={"status": to_status})
    return booking


async def confirm_request(session: AsyncSession, booking: Booking, *,
                          actor_user_id: uuid.UUID,
                          request: Request | None = None) -> Booking:
    """Revalidate before a hold or a draft becomes confirmed (§5.5 step 5).

    A `draft` is not counted as committed capacity, so provider acceptance is exactly where an
    over-subscribed slot must still be refused instead of quietly double-booked.
    """
    if booking.status not in ("hold", "draft"):
        return await transition(session, booking, "confirmed", actor_user_id=actor_user_id,
                                reason="confirmed", request=request)
    offer, definition, resource = await load_bookable_offer(session, booking.offer_id)
    await lock_definition(session, definition.id)
    await _ensure_window_open(session, offer, definition, resource, booking.window_start,
                              booking.window_end, exclude_booking_id=booking.id)
    booked = await cap.committed_quantity(session, definition.id, booking.window_start,
                                          booking.window_end, exclude_booking_id=booking.id)
    if booked + booking.quantity > definition.max_quantity:
        error_type = NoAvailability if resource.capacity_mode == "scheduled" else CapacityExceeded
        raise error_type(
            "The capacity requested is no longer available",
            details={"max_quantity": definition.max_quantity, "committed": booked,
                     "requested": booking.quantity})
    return await transition(session, booking, "confirmed", actor_user_id=actor_user_id,
                            reason="confirmed after capacity revalidation", request=request)


async def _mark_expired(session: AsyncSession, booking: Booking, *,
                        actor_user_id: uuid.UUID | None, reason: str) -> None:
    """CAS flip: only a row still in 'hold' is expired, so concurrent sweeps are harmless."""
    result = await session.execute(
        text("UPDATE bookings SET status='expired', updated_at=now() "
             "WHERE id=:id AND status='hold'"),
        {"id": booking.id},
    )
    if (result.rowcount or 0) == 0:
        return
    session.add(BookingStatusEvent(booking_id=booking.id, from_status="hold",
                                   to_status="expired", actor_user_id=actor_user_id,
                                   reason=reason))
    booking.status = "expired"
    await session.flush()


async def cancel_internal(session: AsyncSession, booking: Booking, *,
                          actor_user_id: uuid.UUID, reason: str, silent: bool = False) -> Booking:
    now = utcnow()
    from_status = booking.status
    booking.status = "cancelled"
    booking.cancelled_at = now
    booking.cancelled_by = actor_user_id
    booking.cancel_reason = None if silent else reason
    booking.hold_expires_at = None
    session.add(BookingStatusEvent(booking_id=booking.id, from_status=from_status,
                                   to_status="cancelled", actor_user_id=actor_user_id,
                                   reason=reason))
    await session.flush()
    return booking


async def cancel(session: AsyncSession, booking: Booking, *, actor_user_id: uuid.UUID,
                 reason: str, request: Request | None = None) -> tuple[Booking, int]:
    """Cancel with the offer's refund policy; returns (booking, refund_cents).

    Import of the payment side is deferred because settlement calls back into
    `transition()` — a top-level import would be circular.
    """
    from app.services.commerce import refund_for_cancellation

    if "cancelled" not in BOOKING_TRANSITIONS.get(booking.status, frozenset()):
        raise InvalidStateTransition(f"A '{booking.status}' booking cannot be cancelled")
    offer = await session.get(Offer, booking.offer_id)
    hours_before = (booking.window_start - utcnow()).total_seconds() / 3600
    refunded = await refund_for_cancellation(session, booking, offer=offer,
                                             actor_user_id=actor_user_id)
    now = utcnow()
    from_status = booking.status
    booking.status = "cancelled"
    booking.cancelled_at = now
    booking.cancelled_by = actor_user_id
    booking.cancel_reason = reason
    booking.hold_expires_at = None
    session.add(BookingStatusEvent(booking_id=booking.id, from_status=from_status,
                                   to_status="cancelled", actor_user_id=actor_user_id,
                                   reason=reason,
                                   meta={"hours_before_start": round(hours_before, 2),
                                         "refund_cents": refunded,
                                         "policy_applied": _policy_label(offer, hours_before)}))
    await session.flush()
    await record_audit(session, request, action="booking.cancelled", entity_type="booking",
                       entity_id=booking.id, before={"status": from_status},
                       after={"status": "cancelled", "refund_cents": refunded})
    await notify(session, user_id=booking.customer_id, kind="booking.cancelled",
                 title="Booking cancelled",
                 body=f"Booking cancelled. Refund: {refunded} {booking.currency} cents.",
                 data={"booking_id": str(booking.id), "refund_cents": refunded})
    return booking, refunded


def _policy_label(offer: Offer | None, hours_before: float) -> int:
    if offer is None:
        return 0
    return refund_pct_for(offer.cancellation_policy, hours_before)


def refund_pct_for(policy: list[dict[str, Any]] | Any, hours_before: float) -> int:
    """Highest refund_pct whose hours_before threshold the cancellation still clears."""
    bands: list[dict[str, Any]] = []
    if isinstance(policy, list):
        bands = [b for b in policy if isinstance(b, dict)]
    if not bands:
        return 0
    eligible = [b for b in bands if float(b.get("hours_before", 0)) <= hours_before]
    chosen = max(eligible, key=lambda b: float(b.get("hours_before", 0))) if eligible else \
        min(bands, key=lambda b: float(b.get("hours_before", 0)))
    return int(chosen.get("refund_pct", 0))


async def expire_due_holds(session: AsyncSession) -> list[uuid.UUID]:
    """Sweeper entry point for both Celery and the in-process runner (§5.5, §10)."""
    now = utcnow()
    due = (
        await session.execute(
            select(Booking).where(Booking.status == "hold",
                                  Booking.hold_expires_at.isnot(None),
                                  Booking.hold_expires_at < now)
        )
    ).scalars().all()
    expired: list[uuid.UUID] = []
    for booking in due:
        await _mark_expired(session, booking, actor_user_id=None, reason="hold sweeper")
        expired.append(booking.id)
        await notify(session, user_id=booking.customer_id, kind="booking.hold_expired",
                     title="Hold expired",
                     body="Your temporary hold expired before the booking was completed.",
                     data={"booking_id": str(booking.id)})
    if expired:
        await session.flush()
    return expired


async def get_for_actor(session: AsyncSession, booking_id: uuid.UUID, *, user_id: uuid.UUID,
                        org_ids: set[uuid.UUID], is_platform_admin: bool) -> Booking:
    booking = await session.get(Booking, booking_id)
    if booking is None:
        raise NotFound("Booking not found")
    if is_platform_admin or booking.customer_id == user_id or booking.org_id in org_ids:
        return booking
    # §4: a cross-tenant read answers 404-equivalent 403 without confirming existence.
    raise OwnershipRequired("You do not have access to this booking")
