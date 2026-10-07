"""Utilization intelligence and provider copilot (docs/AI_SPEC.md).

Pure analysis over numbers the backend already stores: published capacity and
consumed bookings, aggregated into weekday/hour buckets. Every output says how
many weeks it observed, because a "quiet Tuesday afternoon" derived from two
weeks of data is advice, and from ten weeks it is close to fact.

Nothing here writes to the database, so a wrong suggestion cannot corrupt
anything — the provider still decides what to publish.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from capacity_ai.db import CategoryDemand, DefinitionUsage

DOW_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

# A block this long is what a provider can act on: a 1-hour gap is noise in
# almost every capacity business, a 3-hour one is a slot worth repricing.
IDLE_BLOCK_HOURS = 3
# Below this many observed weeks the numbers cannot separate "quiet" from
# "the calendar happens to be short".
MIN_WEEKS_OBSERVED = 2


@dataclass(frozen=True)
class DefinitionUtilization:
    definition_id: str
    name: str
    category_key: Optional[str]
    period_start: str
    period_end: str
    utilization_pct: float
    booked_slots: int
    total_slots: int


@dataclass(frozen=True)
class IdleWindow:
    definition_id: str
    dow: int
    start_time: str
    end_time: str
    occurrences: int
    note: str


@dataclass(frozen=True)
class DemandSignal:
    category_key: str
    open_demands: int
    trend_note: str


@dataclass(frozen=True)
class CopilotSummary:
    summary_text: str
    next_7d_bookings: int
    at_risk_holds: Sequence[dict]
    top_idle_capacity: Sequence[dict]
    revenue_last_30d_cents: int
    currency: str


def utilization_pct(capacity_unit_hours: float, booked_unit_hours: float) -> float:
    """Booked share of published capacity, as a 0-100 percentage rounded to 0.1."""
    if capacity_unit_hours <= 0:
        return 0.0
    return round(min(100.0, booked_unit_hours / capacity_unit_hours * 100.0), 1)


def measure(usage: DefinitionUsage) -> DefinitionUtilization:
    """One definition's utilization, plus the slot counts behind the percentage."""
    return DefinitionUtilization(
        definition_id=usage.definition_id,
        name=usage.name,
        category_key=usage.category_key,
        period_start=usage.period_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        period_end=usage.period_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        utilization_pct=utilization_pct(usage.capacity_unit_hours, usage.booked_unit_hours),
        booked_slots=sum(1 for b in usage.buckets if b.booked_unit_hours > 0),
        total_slots=len(usage.buckets),
    )


def _hh(hour: int) -> str:
    return f"{hour:02d}:00"


def idle_windows(
    usage: Sequence[DefinitionUsage],
    *,
    block_hours: int = IDLE_BLOCK_HOURS,
    min_weeks: int = MIN_WEEKS_OBSERVED,
) -> list[IdleWindow]:
    """Recurring weekday/hour blocks that stayed empty across the whole period.

    A bucket counts as idle only when *zero* unit-hours were booked in it, and
    the definition must have ``min_weeks`` of history, so the result describes
    a repeating gap rather than one unlucky week.
    """
    out: list[IdleWindow] = []
    for item in usage:
        if item.weeks_observed < min_weeks:
            continue
        per_day: dict[int, set[int]] = {}
        for bucket in item.buckets:
            if bucket.capacity_unit_hours <= 0:
                continue
            if bucket.booked_unit_hours <= 0:
                per_day.setdefault(bucket.dow, set()).add(bucket.hour)
        for dow in sorted(per_day):
            hours = sorted(h for h in per_day[dow] if 0 <= h < 24)
            for block_start in range(0, 24, block_hours):
                block = [h for h in hours if block_start <= h < block_start + block_hours]
                if len(block) < block_hours:
                    continue
                touched = [b for b in item.buckets if b.dow == dow and b.hour in block]
                covered = sum(b.capacity_unit_hours for b in touched)
                # The block repeated as often as its least-observed hour: claiming
                # more would turn a three-Friday sample into a four-week "pattern".
                repeated = min(b.occurrences for b in touched)
                out.append(
                    IdleWindow(
                        definition_id=item.definition_id,
                        dow=dow,
                        start_time=_hh(block_start),
                        end_time=_hh(min(block_start + block_hours, 24)),
                        occurrences=repeated,
                        note=(
                            f"{DOW_NAMES[dow]} {block_start:02d}:00-{block_start + block_hours:02d}:00 "
                            f"carried {covered:,.1f} unit-hours of published capacity across "
                            f"{repeated} occurrence(s) and was never booked."
                        ),
                    )
                )
    out.sort(key=lambda w: (-w.occurrences, w.dow, w.start_time, w.definition_id))
    return out


def demand_signal(demand: CategoryDemand) -> DemandSignal:
    """Trend wording for open demand, comparing the period with the one before it."""
    delta = demand.open_demands - demand.previous_open_demands
    if demand.previous_open_demands == 0:
        if demand.open_demands == 0:
            note = "No open requests in this category, in this period or the one before it."
        else:
            note = (
                f"{demand.open_demands} open request(s) in this category, against none in the "
                f"previous period — new demand rather than a trend."
            )
    elif delta > 0:
        pct = round(delta / demand.previous_open_demands * 100)
        note = f"{demand.open_demands} open request(s), up {pct}% on the previous period."
    elif delta < 0:
        pct = round(abs(delta) / demand.previous_open_demands * 100)
        note = f"{demand.open_demands} open request(s), down {pct}% on the previous period."
    else:
        note = f"{demand.open_demands} open request(s), unchanged from the previous period."
    return DemandSignal(
        category_key=demand.category_key,
        open_demands=demand.open_demands,
        trend_note=note,
    )


def _money(cents: int, currency: str) -> str:
    return f"{currency} {cents / 100:,.2f}"


def summarize_copilot(
    *,
    usage: Sequence[DefinitionUsage],
    demands: Sequence[CategoryDemand] = (),
    next_7d_bookings: int = 0,
    at_risk_holds: Sequence[dict] = (),
    revenue_last_30d_cents: int = 0,
    currency: str = "USD",
    org_name: str = "Your organization",
    idle_limit: int = 3,
) -> CopilotSummary:
    """Deterministic provider briefing: counts first, one ranked suggestion.

    The sentence is assembled from the same numbers the payload carries, so the
    text and the fields cannot disagree — the failure mode of a generated
    summary is that it sounds confident about the wrong figures.
    """
    measures = [measure(u) for u in usage]
    busy = sorted(measures, key=lambda m: (-m.utilization_pct, m.name))
    idle = idle_windows(usage)
    ranked = sorted(usage, key=lambda u: (utilization_pct(u.capacity_unit_hours, u.booked_unit_hours), u.name))

    parts = [
        f"{org_name} has {len(usage)} active capacity definition(s).",
        f"Next 7 days: {next_7d_bookings} booking(s).",
    ]
    if at_risk_holds:
        parts.append(
            f"{len(at_risk_holds)} hold(s) expire within 24 hours — confirm or release them."
        )
    if busy:
        top = busy[0]
        parts.append(f"Busiest resource: {top.name} at {top.utilization_pct}% of published capacity.")
    quiet = [r for r in ranked if r.weeks_observed >= MIN_WEEKS_OBSERVED]
    if quiet:
        worst = quiet[0]
        pct = utilization_pct(worst.capacity_unit_hours, worst.booked_unit_hours)
        parts.append(f"Least used: {worst.name} at {pct}%.")
    if revenue_last_30d_cents or currency:
        parts.append(
            f"Collected in the last 30 days: {_money(revenue_last_30d_cents, currency)}."
        )
    if idle:
        w = idle[0]
        parts.append(
            f"Suggested action: {DOW_NAMES[w.dow]} {w.start_time}-{w.end_time} on "
            f"{next((m.name for m in measures if m.definition_id == w.definition_id), 'a resource')} "
            f"has been empty in {w.occurrences} consecutive weeks — price it lower or open it to "
            f"a different category."
        )
    else:
        parts.append(
            "No recurring idle block stands out yet; suggestions appear after a few weeks of "
            "booked history."
        )
    unanswered = [d for d in demands if d.open_demands > 0]
    if unanswered:
        best = max(unanswered, key=lambda d: d.open_demands)
        parts.append(
            f"Open demand beside you: {best.open_demands} request(s) in '{best.category_key}' "
            f"are unmatched — publishing capacity there is the quickest way to be found."
        )

    top_idle = [
        {
            "definition_id": u.definition_id,
            "name": u.name,
            "utilization_pct": utilization_pct(u.capacity_unit_hours, u.booked_unit_hours),
            "capacity_unit_hours": round(u.capacity_unit_hours, 1),
            "weeks_observed": u.weeks_observed,
        }
        for u in ranked[:idle_limit]
    ]
    return CopilotSummary(
        summary_text=" ".join(parts),
        next_7d_bookings=next_7d_bookings,
        at_risk_holds=list(at_risk_holds),
        top_idle_capacity=top_idle,
        revenue_last_30d_cents=revenue_last_30d_cents,
        currency=currency,
    )
