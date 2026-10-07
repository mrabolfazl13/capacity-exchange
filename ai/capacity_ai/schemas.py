"""Pydantic schemas for the capacity_ai package (API-facing DTOs).

Conventions (CONTRACTS.md §1/§2):
- JSON is snake_case; IDs are UUID strings; timestamps are ISO-8601 UTC with ``Z``.
- Money is ALWAYS integer minor units (``*_cents``) plus an ISO ``currency`` code.
- Errors use the envelope ``{"error": {code, message, details, request_id}}``, which
  ``app.core.errors`` owns — the algorithms raise nothing.

Pure algorithm modules use plain dataclasses internally; these models are the transport
layer the `/ai/*` routes in ``backend/app/api/v1/assistant.py`` serialize through.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Optional

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer


def _iso_z(value: datetime) -> str:
    """Serialize datetimes as ISO-8601 UTC with a trailing ``Z`` (§1)."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc)
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


IsoDatetime = Annotated[datetime, PlainSerializer(_iso_z, return_type=str)]
"""Datetime field serialized as ``2026-10-04T12:30:00Z``."""


class _Model(BaseModel):
    """Base with strict-ish config and snake_case JSON (§1)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# Semantic search — POST /ai/parse-search
# ---------------------------------------------------------------------------


class ParseSearchRequest(_Model):
    text: str = Field(min_length=1, max_length=2000, description="Natural-language search text.")


class ParsedQueryOut(BaseModel):
    """Structured filters derived from free text. Field set mirrors §8."""

    model_config = ConfigDict(extra="allow")

    raw_text: str
    category_key: Optional[str] = None
    category_id: Optional[str] = None
    """Identifier to feed ``GET /offers?category=``; resolved from the catalog."""
    category_confidence: float = 0.0
    city: Optional[str] = None
    country: Optional[str] = Field(default=None, pattern=r"^[A-Z]{2}$")
    quantity: Optional[int] = Field(default=None, ge=1)
    unit: Optional[str] = None
    window_start: Optional[IsoDatetime] = None
    window_end: Optional[IsoDatetime] = None
    budget_min_cents: Optional[int] = Field(default=None, ge=0)
    budget_max_cents: Optional[int] = Field(default=None, ge=0)
    currency: Optional[str] = Field(default=None, pattern=r"^[A-Z]{3}$")
    constraints: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    unparsed_fragments: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Listing draft — POST /ai/listing-draft
# ---------------------------------------------------------------------------


class ListingDraftRequest(_Model):
    raw_text: str = Field(min_length=1, max_length=8000)
    category_key: Optional[str] = Field(default=None, max_length=64)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")


class RecurringPatternOut(BaseModel):
    """Suggested recurring availability (§5.2); dow: 0=Monday … 6=Sunday."""

    model_config = ConfigDict(extra="allow")

    dow: int = Field(ge=0, le=6)
    start_time: str
    end_time: str
    quantity: int = Field(ge=1)
    source_phrase: Optional[str] = None


class ListingDraftOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    title: str = Field(max_length=120)
    description: str
    category_key: Optional[str] = None
    category_confidence: float = 0.0
    attributes: dict[str, Any] = Field(default_factory=dict)
    suggested_availabilities: list[RecurringPatternOut] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    unit_label: Optional[str] = None
    suggested_unit_amount_cents: Optional[int] = Field(default=None, ge=0)
    currency: Optional[str] = None


# ---------------------------------------------------------------------------
# Pricing — POST /ai/price-suggest
# ---------------------------------------------------------------------------


class PriceSuggestRequest(_Model):
    offer_id: Optional[str] = None
    category_key: Optional[str] = Field(default=None, max_length=64)
    city: Optional[str] = Field(default=None, max_length=120)
    country: Optional[str] = Field(default=None, pattern=r"^[A-Z]{2}$")
    capacity_mode: Optional[str] = Field(
        default=None, pattern=r"^(scheduled|quantity|open_ended)$"
    )
    unit_label: Optional[str] = Field(default=None, max_length=40)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")


class PriceSuggestionOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    suggested_min_cents: int = Field(ge=0)
    suggested_max_cents: int = Field(ge=0)
    currency: str
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)
    method: str  # "comparables" | "cold_start_floor"
    comparable_count: int = Field(ge=0)


# ---------------------------------------------------------------------------
# Utilization insights — GET /ai/utilization-insights
# ---------------------------------------------------------------------------


class DefinitionUtilizationOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    definition_id: str
    name: str
    category_key: Optional[str] = None
    period_start: IsoDatetime
    period_end: IsoDatetime
    utilization_pct: float
    booked_slots: int
    total_slots: int


class IdleWindowOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    definition_id: str
    dow: int = Field(ge=0, le=6)
    start_time: str
    end_time: str
    occurrences: int
    """How many times this weekday/hour block was published inside the window and
    stayed unbooked every time — the least-observed hour of the block, not the
    calendar length, so a short history cannot read as a pattern."""
    note: str


class DemandCountOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    category_key: str
    open_demands: int
    trend_note: str


class UtilizationInsightsOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    items: list[DefinitionUtilizationOut] = Field(default_factory=list)
    total: int = 0
    idle_windows: list[IdleWindowOut] = Field(default_factory=list)
    demand_counts: list[DemandCountOut] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Provider copilot — GET /ai/copilot
# ---------------------------------------------------------------------------


class CopilotOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    summary_text: str
    next_7d_bookings: int
    at_risk_holds: list[dict[str, Any]] = Field(default_factory=list)
    top_idle_capacity: list[dict[str, Any]] = Field(default_factory=list)
    revenue_last_30d_cents: int
    currency: str
