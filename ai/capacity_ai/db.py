"""Data the algorithms need, expressed as read-only adapter protocols.

Nothing in this package opens a database connection (see ``__init__.py``). The
backend implements these protocols with SQLAlchemy and injects them, which is
what keeps every algorithm here unit-testable with plain literals.

The async signatures are deliberate: the production adapter is the backend's
``AsyncSession``, and a sync protocol would force it to spawn a second event
loop per call.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol, Sequence, runtime_checkable


@dataclass(frozen=True)
class CatalogCategory:
    """One active capacity category, as stored in ``capacity_categories``.

    ``category_id`` is the caller's own identifier and is never read by an
    algorithm — it exists so a response can hand back the id the client needs to
    run the search it was just offered, without the AI layer knowing anything
    about the schema.
    """

    key: str
    label: str
    synonyms: tuple[str, ...] = ()
    category_id: Optional[str] = None


@dataclass(frozen=True)
class ComparableOffer:
    """A published price point used to ground a pricing suggestion."""

    unit_amount_cents: int
    currency: str


@dataclass(frozen=True)
class SlotBucket:
    """One hour of one weekday, aggregated over the observed weeks.

    ``capacity_unit_hours`` is what the provider published (quantity x hours,
    summed across occurrences); ``booked_unit_hours`` is what was actually
    consumed by bookings in the same span. A bucket is idle when ``booked`` is
    zero, and ``occurrences`` says how many times that hour actually came up —
    a rule that ran three times in the window has three, not the four the
    calendar length implies.
    """

    dow: int
    hour: int
    capacity_unit_hours: float
    booked_unit_hours: float
    occurrences: int = 1

    @property
    def utilization(self) -> float:
        if self.capacity_unit_hours <= 0:
            return 0.0
        return min(1.0, self.booked_unit_hours / self.capacity_unit_hours)


@dataclass(frozen=True)
class DefinitionUsage:
    """Published-vs-booked usage for one capacity definition in one period."""

    definition_id: str
    name: str
    category_key: Optional[str]
    period_start: datetime
    period_end: datetime
    weeks_observed: int
    capacity_unit_hours: float
    booked_unit_hours: float
    buckets: tuple[SlotBucket, ...] = ()


@dataclass(frozen=True)
class CategoryDemand:
    """Open demand for a category, now and in the previous equal-length period."""

    category_key: str
    open_demands: int
    previous_open_demands: int


@runtime_checkable
class AssistantData(Protocol):
    """Everything the `/ai/*` endpoints read, gathered by the backend."""

    async def categories(self) -> Sequence[CatalogCategory]:
        ...

    async def comparables(
        self,
        *,
        category_key: Optional[str],
        city: Optional[str],
        country: Optional[str],
        capacity_mode: Optional[str],
        unit_label: Optional[str],
        currency: str,
    ) -> Sequence[ComparableOffer]:
        ...

    async def usage(
        self, *, org_id: str, period_start: datetime, period_end: datetime
    ) -> Sequence[DefinitionUsage]:
        ...

    async def demand_counts(
        self, *, period_start: datetime, period_end: datetime
    ) -> Sequence[CategoryDemand]:
        ...
