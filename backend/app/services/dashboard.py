"""§8 dashboard read models: one aggregate per audience, each scoped by the caller.

Only the provider view needs a denominator. `utilization_pct` compares booked unit-hours
against the supply the provider published for the window, expanded through the same
recurring-rule engine the availability endpoint uses — so the two screens can never
disagree about what "open" meant.

The provider window is read as *service time*: a booking, its share of capacity and the
revenue it produced all answer to `bookings.window_start`, never to the day the money moved.
Paying today for next week's session therefore shows up in next week's revenue, which is
what the provider means by "how did this week go". `/orders?provider=true` stays the
cash-dated view.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.booking import ACTIVE_BOOKING_STATUSES, BOOKING_STATUSES, Booking
from app.models.capacity import CapacityDefinition, CapacityResource
from app.models.commerce import Order
from app.models.crosscut import Dispute
from app.models.identity import Organization, User
from app.models.marketplace import Offer
from app.services import capacity as cap
from app.services import notifications as notify_svc

#: Money the provider actually kept: refunds are subtracted, never dropped.
PAID_PAYMENT_STATUSES = ("paid", "partially_refunded")
#: GMV adds the settled-then-reversed orders back at zero net, so the gross stays honest.
SETTLED_PAYMENT_STATUSES = ("paid", "partially_refunded", "refunded")
#: Bookings that consumed supply: everything except the states where the service never ran.
CONSUMED_BOOKING_STATUSES = ("hold", "confirmed", "in_progress", "completed", "disputed")
OPEN_DISPUTE_STATUSES = ("open", "under_review")

DEFAULT_WINDOW_DAYS = 30
MAX_WINDOW_DAYS = 92
TOP_OFFER_LIMIT = 10
LIST_LIMIT = 10
RECENT_ORDER_LIMIT = 5


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _hours(start: datetime, end: datetime) -> float:
    return max(0.0, (end - start).total_seconds() / 3600.0)


def _clip(window_start: datetime, window_end: datetime,
          start: datetime, end: datetime) -> float:
    return _hours(max(window_start, start), min(window_end, end))


# --------------------------------------------------------------------------- provider


async def _supply_unit_hours(session: AsyncSession, *, org_id: uuid.UUID,
                             start: datetime, end: datetime) -> float:
    """Published capacity for the window, in quantity × hours, overrides included.

    `archived` is the only resource status that stops supplying: a draft resource with a
    live offer behind it still gets booked, so ignoring it would report full utilization
    for rooms the provider is actively selling.
    """
    rows = (
        await session.execute(
            select(CapacityDefinition, CapacityResource)
            .join(CapacityResource, CapacityResource.id == CapacityDefinition.resource_id)
            .where(CapacityResource.org_id == org_id,
                   CapacityResource.status != "archived",
                   CapacityDefinition.is_active.is_(True))
            .order_by(CapacityDefinition.id)
        )
    ).all()
    from_day, to_day = start.date(), (end - timedelta(microseconds=1)).date()

    total = 0.0
    for definition, resource in rows:
        for plan in await cap.build_day_plan(session, definition, resource, from_day, to_day):
            for window in plan.windows:
                total += window.quantity * _clip(window.start, window.end, start, end)
    return total


async def _booked_unit_hours(session: AsyncSession, *, org_id: uuid.UUID,
                             start: datetime, end: datetime) -> float:
    rows = (
        await session.execute(
            select(Booking.window_start, Booking.window_end, Booking.quantity)
            .where(Booking.org_id == org_id,
                   Booking.status.in_(CONSUMED_BOOKING_STATUSES),
                   Booking.window_end > start,
                   Booking.window_start < end)
        )
    ).all()
    return sum(quantity * _clip(w_start, w_end, start, end)
               for w_start, w_end, quantity in rows)


async def _bookings_by_status(session: AsyncSession, *, org_id: uuid.UUID,
                              start: datetime, end: datetime) -> dict[str, int]:
    """Zero-filled, so the UI renders every status card instead of guessing the gaps."""
    rows = (
        await session.execute(
            select(Booking.status, func.count())
            .where(Booking.org_id == org_id,
                   Booking.window_start >= start,
                   Booking.window_start < end)
            .group_by(Booking.status)
        )
    ).all()
    counts = {status: 0 for status in BOOKING_STATUSES}
    for status, n in rows:
        counts[str(status)] = int(n)
    return counts


async def _top_offers(session: AsyncSession, *, org_id: uuid.UUID,
                      start: datetime, end: datetime) -> list[dict[str, Any]]:
    """The window's money and volume, broken down per offer."""
    booked = dict(
        (
            await session.execute(
                select(Booking.offer_id, func.count())
                .where(Booking.org_id == org_id,
                       Booking.status.in_(CONSUMED_BOOKING_STATUSES),
                       Booking.window_start >= start,
                       Booking.window_start < end)
                .group_by(Booking.offer_id)
            )
        ).all()
    )
    earned = dict(
        (
            await session.execute(
                select(Booking.offer_id,
                       func.coalesce(func.sum(Order.total_cents - Order.refunded_cents), 0))
                .select_from(Order)
                .join(Booking, Booking.id == Order.booking_id)
                .where(Order.provider_org_id == org_id,
                       Order.payment_status.in_(PAID_PAYMENT_STATUSES),
                       Booking.window_start >= start,
                       Booking.window_start < end)
                .group_by(Booking.offer_id)
            )
        ).all()
    )
    offer_ids = set(booked) | set(earned)
    if not offer_ids:
        return []
    titles = dict(
        (
            await session.execute(
                select(Offer.id, Offer.title).where(Offer.id.in_(offer_ids))
            )
        ).all()
    )
    rows = [
        {"offer_id": offer_id, "title": titles.get(offer_id, ""),
         "bookings": int(booked.get(offer_id, 0)), "revenue_cents": int(earned.get(offer_id, 0))}
        for offer_id in offer_ids
    ]
    rows.sort(key=lambda r: (-r["revenue_cents"], -r["bookings"], r["title"], str(r["offer_id"])))
    return rows[:TOP_OFFER_LIMIT]


async def _sum(session: AsyncSession, expr, *where) -> int:
    return int((await session.execute(
        select(func.coalesce(func.sum(expr), 0)).where(*where))).scalar_one())


async def _revenue_cents(session: AsyncSession, *, org_id: uuid.UUID,
                         start: datetime, end: datetime) -> int:
    """Kept money for services delivered in the window; a refunded order nets to zero here."""
    return int((
        await session.execute(
            select(func.coalesce(func.sum(Order.total_cents - Order.refunded_cents), 0))
            .select_from(Order)
            .join(Booking, Booking.id == Order.booking_id)
            .where(Order.provider_org_id == org_id,
                   Order.payment_status.in_(PAID_PAYMENT_STATUSES),
                   Booking.window_start >= start,
                   Booking.window_start < end)
        )
    ).scalar_one())


async def provider_dashboard(session: AsyncSession, *, org_id: uuid.UUID,
                             start: datetime, end: datetime,
                             now: datetime | None = None) -> dict[str, Any]:
    supply = await _supply_unit_hours(session, org_id=org_id, start=start, end=end)
    booked = await _booked_unit_hours(session, org_id=org_id, start=start, end=end)
    org = await session.get(Organization, org_id)
    upcoming = list(
        (
            await session.execute(
                select(Booking)
                .where(Booking.org_id == org_id,
                       Booking.status.in_(ACTIVE_BOOKING_STATUSES),
                       Booking.window_end > (now or utcnow()))
                .order_by(Booking.window_start, Booking.id)
                .limit(LIST_LIMIT)
            )
        ).scalars().all()
    )
    return {
        "utilization_pct": round(100.0 * booked / supply, 2) if supply > 0 else 0.0,
        "bookings_by_status": await _bookings_by_status(session, org_id=org_id,
                                                        start=start, end=end),
        "revenue_cents": await _revenue_cents(session, org_id=org_id, start=start, end=end),
        "revenue_currency": org.currency if org else "USD",
        "upcoming_bookings": upcoming,
        "top_offers": await _top_offers(session, org_id=org_id, start=start, end=end),
    }


# --------------------------------------------------------------------------- customer


async def customer_dashboard(session: AsyncSession, *, user_id: uuid.UUID,
                             now: datetime | None = None) -> dict[str, Any]:
    active = list(
        (
            await session.execute(
                select(Booking)
                .where(Booking.customer_id == user_id,
                       Booking.status.in_(ACTIVE_BOOKING_STATUSES),
                       Booking.window_end > (now or utcnow()))
                .order_by(Booking.window_start, Booking.id)
                .limit(LIST_LIMIT)
            )
        ).scalars().all()
    )
    recent = list(
        (
            await session.execute(
                select(Order)
                .where(Order.buyer_id == user_id)
                .order_by(Order.created_at.desc(), Order.id.desc())
                .limit(RECENT_ORDER_LIMIT)
            )
        ).scalars().all()
    )
    currency = (
        await session.execute(
            select(Order.currency)
            .where(Order.buyer_id == user_id,
                   Order.payment_status.in_(PAID_PAYMENT_STATUSES))
            .order_by(Order.created_at.desc(), Order.id.desc())
            .limit(1)
        )
    ).scalar()
    return {
        "active_bookings": active,
        "spend_cents": await _sum(
            session, Order.total_cents - Order.refunded_cents,
            Order.buyer_id == user_id,
            Order.payment_status.in_(PAID_PAYMENT_STATUSES)),
        "spend_currency": currency or "USD",
        "unread_notifications": await notify_svc.unread_count(session, user_id),
        "recent_orders": recent,
    }


# --------------------------------------------------------------------------- admin


async def _total(session: AsyncSession, model, *where) -> int:
    stmt = select(func.count()).select_from(model)
    if where:
        stmt = stmt.where(*where)
    return int((await session.execute(stmt)).scalar_one())


async def admin_dashboard(session: AsyncSession) -> dict[str, Any]:
    """Platform totals: unscoped, because only a platform admin reaches this view."""
    settled = Order.payment_status.in_(SETTLED_PAYMENT_STATUSES)
    return {
        "users_total": await _total(session, User),
        "orgs_total": await _total(session, Organization),
        "offers_total": await _total(session, Offer),
        "bookings_total": await _total(session, Booking),
        "gmv_cents": await _sum(session, Order.total_cents - Order.refunded_cents, settled),
        "commission_cents": await _sum(session, Order.commission_cents, settled),
        "open_disputes": await _total(
            session, Dispute, Dispute.status.in_(OPEN_DISPUTE_STATUSES)),
    }
