"""Backend half of the `/ai/*` surface: the read-only adapters `capacity_ai` asks for.

`ai/` holds the algorithms and refuses to import a database driver, so everything
that knows about SQLAlchemy lives here. Two rules keep the split honest:

- This module *gathers and shapes*; it never scores, ranks or words a suggestion.
  If a number is not here, it is because a query produced it, and the reasoning
  for it is in `capacity_ai`.
- Every read is scoped to data the caller may already see. Provider insights are
  gated on organization tenancy by the router (§4); pricing comparables only ever
  return published offers, and never an identifier, so a suggestion cannot leak a
  competitor's draft listing or their org.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from zoneinfo import ZoneInfo

from app.models.booking import Booking
from app.models.capacity import CapacityCategory, CapacityDefinition, CapacityResource
from app.models.commerce import Order
from app.models.identity import Organization
from app.models.marketplace import Demand, Offer
from app.services import capacity as cap
from app.services.dashboard import CONSUMED_BOOKING_STATUSES, PAID_PAYMENT_STATUSES

from capacity_ai.db import (
    CatalogCategory,
    CategoryDemand,
    ComparableOffer,
    DefinitionUsage,
    SlotBucket,
)

#: Insights expand recurring rules day by day, so the window is capped (§8) even
#: though a provider could ask for a year.
MAX_WINDOW_DAYS = 92
DEFAULT_WINDOW_DAYS = 28
#: A price band is a percentile, and a percentile needs a sample; beyond this the
#: extra rows cannot move the answer.
COMPARABLE_LIMIT = 200
#: Bound on definitions per insights call. Overridable only by narrowing the window.
MAX_DEFINITIONS = 40
#: Floor used when the platform genuinely has no published price anywhere for the
#: category. Deliberately small: it says "start somewhere", not "this is the market".
COLD_START_FLOOR_CENTS = 5000


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=1)


# --------------------------------------------------------------------------- catalog


async def catalog(session: AsyncSession) -> list[CatalogCategory]:
    """Active categories, as the semantic parser's vocabulary."""
    rows = (
        await session.execute(
            select(CapacityCategory.id, CapacityCategory.key, CapacityCategory.label)
            .where(CapacityCategory.is_active.is_(True))
            .order_by(CapacityCategory.label, CapacityCategory.key)
        )
    ).all()
    return [
        CatalogCategory(key=key, label=label, category_id=str(id_)) for id_, key, label in rows
    ]


async def category_exists(session: AsyncSession, key: str) -> bool:
    return bool(
        (
            await session.execute(
                select(func.count()).select_from(CapacityCategory)
                .where(CapacityCategory.key == key, CapacityCategory.is_active.is_(True))
            )
        ).scalar_one()
    )


# --------------------------------------------------------------------------- pricing


async def comparables(
    session: AsyncSession,
    *,
    category_key: Optional[str] = None,
    city: Optional[str] = None,
    country: Optional[str] = None,
    unit_label: Optional[str] = None,
    capacity_mode: Optional[str] = None,
    currency: Optional[str] = None,
    exclude_offer_id: Optional[uuid.UUID] = None,
) -> list[ComparableOffer]:
    """Published unit prices for offers shaped like the one being priced.

    No identifiers leave this query: a price suggestion must not reveal who
    charges what, only how the market is distributed.
    """
    stmt = (
        select(Offer.unit_amount_cents, Offer.currency)
        .join(CapacityResource, CapacityResource.id == Offer.resource_id)
        .join(CapacityCategory, CapacityCategory.id == CapacityResource.category_id)
        .join(CapacityDefinition, CapacityDefinition.id == Offer.definition_id)
        .where(Offer.status == "published", Offer.unit_amount_cents > 0)
    )
    if category_key:
        stmt = stmt.where(CapacityCategory.key == category_key)
    if city:
        stmt = stmt.where(func.lower(CapacityResource.address["city"].astext) == city.lower())
    if country:
        stmt = stmt.where(
            func.upper(CapacityResource.address["country"].astext) == country.upper())
    if unit_label:
        stmt = stmt.where(CapacityDefinition.unit_label == unit_label)
    if capacity_mode:
        stmt = stmt.where(CapacityResource.capacity_mode == capacity_mode)
    if currency:
        stmt = stmt.where(Offer.currency == currency)
    if exclude_offer_id is not None:
        stmt = stmt.where(Offer.id != exclude_offer_id)

    rows = (await session.execute(stmt.order_by(Offer.unit_amount_cents).limit(COMPARABLE_LIMIT))).all()
    return [ComparableOffer(unit_amount_cents=int(cents), currency=cur) for cents, cur in rows]


async def offer_shape(
    session: AsyncSession, offer_id: uuid.UUID
) -> Optional[dict[str, Any]]:
    """What an offer is, so its own listing can be priced against lookalikes."""
    row = (
        await session.execute(
            select(
                CapacityCategory.key.label("category_key"),
                CapacityResource.address["city"].astext.label("city"),
                CapacityResource.address["country"].astext.label("country"),
                CapacityResource.capacity_mode.label("capacity_mode"),
                CapacityDefinition.unit_label.label("unit_label"),
                Offer.currency.label("currency"),
            )
            .select_from(Offer)
            .join(CapacityResource, CapacityResource.id == Offer.resource_id)
            .join(CapacityCategory, CapacityCategory.id == CapacityResource.category_id)
            .join(CapacityDefinition, CapacityDefinition.id == Offer.definition_id)
            .where(Offer.id == offer_id)
        )
    ).first()
    return dict(row._mapping) if row is not None else None


# --------------------------------------------------------------------------- usage


def _spread(counts: dict[tuple[int, int], list[float]], start: datetime, end: datetime,
            quantity: int, tz: ZoneInfo) -> None:
    """Add `quantity` x hours to each local (weekday, hour) bucket the span touches.

    The second number in each bucket counts occurrences: how many times that hour
    actually came up inside the window. A rule that only ran three Fridays must not
    be reported as an idle block that repeated four times.
    """
    if quantity <= 0 or end <= start:
        return
    cursor = start.astimezone(tz).replace(minute=0, second=0, microsecond=0)
    if cursor < start:
        cursor += timedelta(hours=1)
    while cursor < end:
        next_hour = cursor + timedelta(hours=1)
        overlap = (min(next_hour, end.astimezone(tz)) - cursor).total_seconds() / 3600.0
        if overlap > 0:
            bucket = counts.setdefault((cursor.weekday(), cursor.hour), [0.0, 0.0])
            bucket[0] += quantity * overlap
            bucket[1] += 1
        cursor = next_hour


async def _definition_usage(
    session: AsyncSession, definition: CapacityDefinition, resource: CapacityResource,
    category_key: Optional[str], start: datetime, end: datetime,
) -> DefinitionUsage:
    tz = cap.tz_of(resource.timezone)
    from_day, to_day = start.date(), (end - timedelta(microseconds=1)).date()
    supply: dict[tuple[int, int], list[float]] = {}
    for plan in await cap.build_day_plan(session, definition, resource, from_day, to_day):
        for window in plan.windows:
            clip_start = max(window.start, start)
            clip_end = min(window.end, end)
            if window.quantity > 0 and clip_end > clip_start:
                _spread(supply, clip_start, clip_end, window.quantity, tz)

    booked: dict[tuple[int, int], list[float]] = {}
    rows = (
        await session.execute(
            select(Booking.window_start, Booking.window_end, Booking.quantity).where(
                Booking.definition_id == definition.id,
                Booking.status.in_(CONSUMED_BOOKING_STATUSES),
                Booking.window_end > start,
                Booking.window_start < end,
            )
        )
    ).all()
    for window_start, window_end, quantity in rows:
        _spread(booked, max(window_start, start), min(window_end, end), int(quantity), tz)

    buckets = tuple(
        SlotBucket(
            dow=dow,
            hour=hour,
            capacity_unit_hours=round(capacity, 4),
            booked_unit_hours=round(booked.get((dow, hour), [0.0, 0.0])[0], 4),
            occurrences=int(capacity_count),
        )
        for (dow, hour), (capacity, capacity_count) in sorted(supply.items())
    )
    # History depth = how many times the busiest published hour actually came up.
    # For a weekly rule that is the number of weeks with real data, which beats
    # dividing the window length by seven: a rule that only runs three Fridays has
    # three weeks of evidence, not four.
    weeks = int(max((count for _, count in supply.values()), default=0))
    return DefinitionUsage(
        definition_id=str(definition.id),
        name=definition.name,
        category_key=category_key,
        period_start=start,
        period_end=end,
        weeks_observed=weeks,
        capacity_unit_hours=round(sum(v[0] for v in supply.values()), 4),
        booked_unit_hours=round(sum(v[0] for v in booked.values()), 4),
        buckets=buckets,
    )


async def _org_definitions(
    session: AsyncSession, org_id: uuid.UUID
) -> Sequence[tuple[CapacityDefinition, CapacityResource, Optional[str]]]:
    """The org's live definitions, with archived resources excluded like the dashboard does."""
    rows = (
        await session.execute(
            select(CapacityDefinition, CapacityResource, CapacityCategory.key)
            .join(CapacityResource, CapacityResource.id == CapacityDefinition.resource_id)
            .join(CapacityCategory, CapacityCategory.id == CapacityResource.category_id)
            .where(CapacityResource.org_id == org_id,
                   CapacityResource.status != "archived",
                   CapacityDefinition.is_active.is_(True))
            .order_by(CapacityDefinition.id)
        )
    ).all()
    return [(defn, res, key) for defn, res, key in rows]


async def usage(
    session: AsyncSession, *, org_id: uuid.UUID, start: datetime, end: datetime
) -> tuple[list[DefinitionUsage], bool]:
    """Published-vs-booked usage per definition, plus whether the batch was truncated."""
    rows = await _org_definitions(session, org_id)
    items = [
        await _definition_usage(session, definition, resource, category_key, start, end)
        for definition, resource, category_key in rows[:MAX_DEFINITIONS]
    ]
    return items, len(rows) > MAX_DEFINITIONS


# --------------------------------------------------------------------------- demand


async def demand_counts(
    session: AsyncSession, *, start: datetime, end: datetime
) -> list[CategoryDemand]:
    """Open requests posted in the window, against the same count one window earlier.

    "Open" is the current status, not the status at posting time: the point of the
    signal is unmet demand that exists right now, and the previous period gives the
    comparison its direction.
    """
    span = end - start
    previous_start = start - span

    async def group(window_start: datetime, window_end: datetime) -> dict[str, int]:
        rows = (
            await session.execute(
                select(CapacityCategory.key, func.count())
                .select_from(Demand)
                .join(CapacityCategory, CapacityCategory.id == Demand.category_id)
                .where(Demand.status == "open",
                       Demand.created_at >= window_start,
                       Demand.created_at < window_end)
                .group_by(CapacityCategory.key)
            )
        ).all()
        return {key: int(count) for key, count in rows}

    current = await group(start, end)
    previous = await group(previous_start, start)
    keys = sorted(set(current) | set(previous))
    return [
        CategoryDemand(category_key=key, open_demands=current.get(key, 0),
                       previous_open_demands=previous.get(key, 0))
        for key in keys
    ]


# --------------------------------------------------------------------------- copilot inputs


async def copilot_context(session: AsyncSession, *, org_id: uuid.UUID) -> dict[str, Any]:
    """The three numbers `/ai/copilot` states in words.

    Bookings and holds are read as *service time* (the window the provider has to
    work), while revenue is read as *cash time* (`orders.placed_at`). The two
    axes are deliberate: the provider dashboard answers "how did that week go"
    over services delivered, whereas a trailing-30-day line in a briefing reads
    as money collected, and on a young account every paid booking is still in
    the future. Dating revenue by service time would tell a busy provider they
    collected nothing. `top_offers` and `revenue_cents` on `/dashboard/provider`
    keep the service-dated rule (§8), so this figure is labelled as collected
    rather than earned wherever it appears.
    """
    now = utcnow()
    next_bookings = int(
        (
            await session.execute(
                select(func.count()).select_from(Booking).where(
                    Booking.org_id == org_id,
                    Booking.status.in_(("hold", "confirmed")),
                    Booking.window_start >= now,
                    Booking.window_start < now + timedelta(days=7),
                )
            )
        ).scalar_one()
    )
    at_risk = (
        await session.execute(
            select(Booking.id, Booking.window_start, Booking.hold_expires_at, Offer.title)
            .join(Offer, Offer.id == Booking.offer_id)
            .where(Booking.org_id == org_id,
                   Booking.status == "hold",
                   Booking.hold_expires_at.is_not(None),
                   Booking.hold_expires_at < now + timedelta(hours=24))
            .order_by(Booking.hold_expires_at)
            .limit(10)
        )
    ).all()
    revenue = int(
        (
            await session.execute(
                select(func.coalesce(func.sum(Order.total_cents - Order.refunded_cents), 0))
                .where(Order.provider_org_id == org_id,
                       Order.payment_status.in_(PAID_PAYMENT_STATUSES),
                       Order.placed_at >= now - timedelta(days=30),
                       Order.placed_at < now)
            )
        ).scalar_one()
    )
    org_name = (
        await session.execute(select(Organization.name).where(Organization.id == org_id))
    ).scalar_one_or_none()
    # One currency per briefing: the org's own dominant listing currency, so the
    # figure never reads as a mixed total.
    currency = (
        await session.execute(
            select(Offer.currency, func.count().label("n"))
            .where(Offer.org_id == org_id)
            .group_by(Offer.currency)
            .order_by(func.count().desc(), Offer.currency)
        )
    ).first()
    return {
        "next_7d_bookings": next_bookings,
        "at_risk_holds": [
            {
                "booking_id": str(booking_id),
                "offer_title": title,
                "hold_expires_at": expires.astimezone(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"),
                "window_start": window_start.astimezone(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"),
            }
            for booking_id, window_start, expires, title in at_risk
        ],
        "revenue_last_30d_cents": revenue,
        "org_name": org_name,
        "currency": currency.currency if currency else "USD",
    }
