"""Availability expansion and committed-quantity accounting (CONTRACTS §5.2/§5.5).

Recurring weekday rules are the plan; overrides and definition exceptions edit it per date;
bookings in status hold/confirmed/in_progress are what is actually gone. Every function here
is read-only — the only writer of overlap state is `services.booking`, which re-reads
`committed_quantity()` inside the advisory lock instead of trusting a value computed here.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.booking import ACTIVE_BOOKING_STATUSES, Booking
from app.models.capacity import (
    AvailabilityOverride,
    CapacityDefinition,
    CapacityDefinitionException,
    CapacityResource,
    RecurringAvailability,
)

DAY_MINUTES = 24 * 60


def tz_of(name: str | None) -> ZoneInfo:
    """Unknown timezone strings degrade to UTC rather than failing the request."""
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return ZoneInfo("UTC")


def to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass
class Window:
    start: datetime  # tz-aware UTC
    end: datetime    # tz-aware UTC
    quantity: int
    closed: bool = False


@dataclass
class DayPlan:
    day: date
    windows: list[Window] = field(default_factory=list)
    closed: bool = False

    @property
    def total_quantity(self) -> int:
        if self.closed:
            return 0
        return sum(w.quantity for w in self.windows if w.quantity > 0)


def day_range(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(0, 0), tzinfo=tz)
    return to_utc(start), to_utc(start + timedelta(days=1))


def _apply_time_override(windows: list[Window], start: datetime, end: datetime, *,
                         close: bool = False, delta: int = 0,
                         absolute: int | None = None) -> list[Window]:
    """Clip [start, end) out of each window and rebuild the touched slice at a new quantity."""
    out: list[Window] = []
    for w in windows:
        s, e = max(w.start, start), min(w.end, end)
        if s >= e:
            out.append(w)
            continue
        if w.start < s:
            out.append(Window(w.start, s, w.quantity, w.closed))
        if close:
            quantity = 0
        elif absolute is not None:
            quantity = absolute
        else:
            quantity = max(0, w.quantity + delta)
        if quantity > 0:
            out.append(Window(s, e, quantity, w.closed))
        if e < w.end:
            out.append(Window(e, w.end, w.quantity, w.closed))
    return out


async def build_day_plan(session: AsyncSession, definition: CapacityDefinition,
                         resource: CapacityResource, from_day: date, to_day: date) -> list[DayPlan]:
    """Expand recurring rules into per-day windows, then apply overrides and exceptions."""
    tz = tz_of(resource.timezone)
    rules = (
        await session.execute(
            select(RecurringAvailability).where(
                RecurringAvailability.definition_id == definition.id,
                RecurringAvailability.is_active.is_(True),
            )
        )
    ).scalars().all()
    overrides = (
        await session.execute(
            select(AvailabilityOverride).where(
                AvailabilityOverride.definition_id == definition.id,
                AvailabilityOverride.is_active.is_(True),
                AvailabilityOverride.override_date >= from_day,
                AvailabilityOverride.override_date <= to_day,
            )
        )
    ).scalars().all()
    exceptions = (
        await session.execute(
            select(CapacityDefinitionException).where(
                CapacityDefinitionException.definition_id == definition.id,
            )
        )
    ).scalars().all()

    by_date: dict[date, list[AvailabilityOverride]] = defaultdict(list)
    for o in overrides:
        by_date[o.override_date].append(o)

    plans: list[DayPlan] = []
    day = from_day
    while day <= to_day:
        start_utc, end_utc = day_range(day, tz)
        windows: list[Window] = []
        for rule in rules:
            if rule.dow != day.weekday():
                continue
            if rule.valid_from and day < rule.valid_from:
                continue
            if rule.valid_until and day > rule.valid_until:
                continue
            w_start = to_utc(datetime.combine(day, rule.start_time, tzinfo=tz))
            w_end = to_utc(datetime.combine(day, rule.end_time, tzinfo=tz))
            if w_end <= w_start:
                continue
            windows.append(Window(w_start, w_end, rule.quantity))

        plan = DayPlan(day=day, windows=windows)
        for o in by_date.get(day, []):
            whole_day = o.start_time is None
            if whole_day:
                o_start, o_end = start_utc, end_utc
            else:
                o_start = to_utc(datetime.combine(day, o.start_time, tzinfo=tz))
                o_end = to_utc(datetime.combine(day, o.end_time, tzinfo=tz))

            if o.kind == "closed":
                if whole_day:
                    plan.closed = True
                    plan.windows = []
                else:
                    plan.windows = _apply_time_override(plan.windows, o_start, o_end, close=True)
            elif o.kind == "extra" and o.quantity:
                # Extra capacity is additive: a fresh window, not a rewrite of the plan.
                plan.windows.append(Window(o_start, o_end, o.quantity))
            elif o.kind == "reduced":
                amount = o.quantity or 0
                if whole_day:
                    plan.windows = [Window(w.start, w.end, max(0, w.quantity - amount), w.closed)
                                    for w in plan.windows]
                else:
                    plan.windows = _apply_time_override(plan.windows, o_start, o_end, delta=-amount)

        for exc in exceptions:
            if exc.effective_from and day < exc.effective_from:
                continue
            if exc.effective_to and day > exc.effective_to:
                continue
            if exc.is_closed:
                plan.closed = True
                plan.windows = []
            elif exc.max_quantity_delta:
                plan.windows = [Window(w.start, w.end, max(0, w.quantity + exc.max_quantity_delta), w.closed)
                                for w in plan.windows]

        plan.windows.sort(key=lambda w: (w.start, w.end))
        plans.append(plan)
        day += timedelta(days=1)
    return plans


async def committed_quantity(session: AsyncSession, definition_id: uuid.UUID,
                             window_start: datetime, window_end: datetime, *,
                             exclude_booking_id: uuid.UUID | None = None) -> int:
    """Units already held by active bookings overlapping [start, end) (§5.5 step 2)."""
    stmt = text(
        """
        SELECT coalesce(sum(quantity), 0)
        FROM bookings
        WHERE definition_id = :definition_id
          AND status = ANY(:statuses)
          AND window_start < :window_end
          AND window_end > :window_start
        """
    )
    params = {
        "definition_id": definition_id,
        "statuses": list(ACTIVE_BOOKING_STATUSES),
        "window_start": to_utc(window_start),
        "window_end": to_utc(window_end),
    }
    if exclude_booking_id is not None:
        stmt = text(
            """
            SELECT coalesce(sum(quantity), 0)
            FROM bookings
            WHERE definition_id = :definition_id
              AND status = ANY(:statuses)
              AND window_start < :window_end
              AND window_end > :window_start
              AND id <> :exclude
            """
        )
        params["exclude"] = exclude_booking_id
    return int((await session.execute(stmt, params)).scalar() or 0)


async def active_bookings_in_range(session: AsyncSession, definition_id: uuid.UUID,
                                   window_start: datetime, window_end: datetime) -> list[Booking]:
    return list((
        await session.execute(
            select(Booking).where(
                Booking.definition_id == definition_id,
                Booking.status.in_(ACTIVE_BOOKING_STATUSES),
                Booking.window_start < window_end,
                Booking.window_end > window_start,
            )
        )
    ).scalars().all())


async def free_quantity_for_window(session: AsyncSession, definition: CapacityDefinition,
                                   window: Window, *,
                                   exclude_booking_id: uuid.UUID | None = None) -> int:
    booked = await committed_quantity(session, definition.id, window.start, window.end,
                                      exclude_booking_id=exclude_booking_id)
    return max(0, min(definition.max_quantity, window.quantity) - booked)


async def expand_free_windows(session: AsyncSession, definition: CapacityDefinition,
                              resource: CapacityResource, start: datetime, end: datetime,
                              *, exclude_booking_id: uuid.UUID | None = None) -> list[Window]:
    """Concrete free windows in [start, end), each with remaining quantity after bookings.

    `exclude_booking_id` lets a hold conversion revalidate without counting its own hold as
    somebody else's reservation.
    """
    tz = tz_of(resource.timezone)
    from_day = to_utc(start).astimezone(tz).date()
    to_day = to_utc(end).astimezone(tz).date()
    plans = await build_day_plan(session, definition, resource, from_day, to_day)
    out: list[Window] = []
    for plan in plans:
        for w in plan.windows:
            s, e = max(w.start, to_utc(start)), min(w.end, to_utc(end))
            if s >= e:
                continue
            free = await free_quantity_for_window(session, definition, Window(s, e, w.quantity),
                                                 exclude_booking_id=exclude_booking_id)
            if free > 0:
                out.append(Window(s, e, free))
    out.sort(key=lambda w: (w.start, w.end))
    return out
