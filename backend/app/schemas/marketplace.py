"""§5.3 marketplace DTOs: offers, search, demands, matches."""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.capacity import Address
from app.schemas.common import CancellationPolicyBand, ORMModel, WireDateTime, WireUUID

OfferPricingMode = Literal["per_unit_time", "per_quantity", "flat"]
BookingMode = Literal["request_confirm", "instant"]
OfferStatus = Literal["draft", "published", "paused", "closed"]
DemandStatus = Literal["open", "matched", "closed", "cancelled"]
MatchStatus = Literal["suggested", "accepted", "declined", "expired"]
OfferSort = Literal["relevance", "price_asc", "price_desc", "newest", "rating"]


class OfferOut(ORMModel):
    id: WireUUID
    definition_id: WireUUID
    org_id: WireUUID
    resource_id: WireUUID
    title: str
    description: str
    pricing_mode: OfferPricingMode
    unit_amount_cents: int
    currency: str
    min_lead_time_minutes: int
    max_lead_time_days: int | None
    min_duration_minutes: int | None
    max_duration_minutes: int | None
    min_quantity: int | None
    max_quantity: int | None
    booking_mode: BookingMode
    hold_minutes: int
    cancellation_policy: list[CancellationPolicyBand]
    status: OfferStatus
    published_at: WireDateTime | None
    created_at: WireDateTime
    updated_at: WireDateTime
    # Read-model joins the marketplace list needs without a second request.
    category_id: WireUUID | None = None
    category_label: str | None = None
    org_name: str | None = None
    city: str | None = None
    rating_avg: float | None = None
    rating_count: int | None = None
    resource_name: str | None = None
    definition_name: str | None = None
    unit_label: str | None = None
    max_quantity_definition: int | None = None
    lat: float | None = None
    lon: float | None = None
    free_quantity: int | None = None


class OfferInput(ORMModel):
    definition_id: UUID
    title: str = Field(min_length=3, max_length=120)
    description: str = Field(min_length=10, max_length=8000)
    pricing_mode: OfferPricingMode = "per_quantity"
    unit_amount_cents: int = Field(ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    min_lead_time_minutes: int = Field(default=0, ge=0)
    max_lead_time_days: int | None = Field(default=None, ge=1)
    min_duration_minutes: int | None = Field(default=None, ge=5)
    max_duration_minutes: int | None = Field(default=None, ge=5)
    min_quantity: int | None = Field(default=None, ge=1)
    max_quantity: int | None = Field(default=None, ge=1)
    booking_mode: BookingMode = "instant"
    hold_minutes: int = Field(default=15, ge=5, le=120)
    cancellation_policy: list[CancellationPolicyBand] | None = None

    @field_validator("currency", mode="before")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper() if isinstance(v, str) else v

    @field_validator("max_duration_minutes")
    @classmethod
    def _duration_order(cls, v: int | None, info) -> int | None:
        low = info.data.get("min_duration_minutes")
        if v is not None and low is not None and v < low:
            raise ValueError("max_duration_minutes must be >= min_duration_minutes")
        return v

    @field_validator("max_quantity")
    @classmethod
    def _quantity_order(cls, v: int | None, info) -> int | None:
        low = info.data.get("min_quantity")
        if v is not None and low is not None and v < low:
            raise ValueError("max_quantity must be >= min_quantity")
        return v


class OfferPatch(ORMModel):
    title: str | None = Field(default=None, min_length=3, max_length=120)
    description: str | None = Field(default=None, min_length=10, max_length=8000)
    pricing_mode: OfferPricingMode | None = None
    unit_amount_cents: int | None = Field(default=None, ge=0)
    min_lead_time_minutes: int | None = Field(default=None, ge=0)
    max_lead_time_days: int | None = Field(default=None, ge=1)
    min_duration_minutes: int | None = Field(default=None, ge=5)
    max_duration_minutes: int | None = Field(default=None, ge=5)
    min_quantity: int | None = Field(default=None, ge=1)
    max_quantity: int | None = Field(default=None, ge=1)
    booking_mode: BookingMode | None = None
    hold_minutes: int | None = Field(default=None, ge=5, le=120)
    cancellation_policy: list[CancellationPolicyBand] | None = None


class DemandInput(ORMModel):
    category_id: UUID | None = None
    description: str = Field(min_length=10, max_length=8000)
    address: Address | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    desired_start: WireDateTime | None = None
    desired_end: WireDateTime | None = None
    quantity: int = Field(default=1, ge=1)
    budget_min_cents: int | None = Field(default=None, ge=0)
    budget_max_cents: int | None = Field(default=None, ge=0)
    expires_at: WireDateTime | None = None

    @field_validator("budget_max_cents")
    @classmethod
    def _budget_order(cls, v: int | None, info) -> int | None:
        low = info.data.get("budget_min_cents")
        if v is not None and low is not None and v < low:
            raise ValueError("budget_max_cents must be >= budget_min_cents")
        return v


class DemandPatch(ORMModel):
    """Only the fields a customer may still change while the demand is open (§8)."""

    category_id: UUID | None = None
    description: str | None = Field(default=None, min_length=10, max_length=8000)
    address: Address | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    desired_start: WireDateTime | None = None
    desired_end: WireDateTime | None = None
    quantity: int | None = Field(default=None, ge=1)
    budget_min_cents: int | None = Field(default=None, ge=0)
    budget_max_cents: int | None = Field(default=None, ge=0)
    expires_at: WireDateTime | None = None


class DemandOut(ORMModel):
    id: WireUUID
    customer_id: WireUUID
    org_id: WireUUID | None
    category_id: WireUUID | None
    description: str
    address: Address | None
    lat: float | None
    lon: float | None
    desired_start: WireDateTime | None
    desired_end: WireDateTime | None
    quantity: int
    budget_min_cents: int | None
    budget_max_cents: int | None
    status: DemandStatus
    expires_at: WireDateTime | None
    created_at: WireDateTime
    category_label: str | None = None
    match_count: int | None = None


class MatchOut(ORMModel):
    id: WireUUID
    demand_id: WireUUID
    offer_id: WireUUID
    score: float
    reasons: list[str] = []
    status: MatchStatus
    provider_action_at: WireDateTime | None
    created_at: WireDateTime
    offer: OfferOut | None = None
    demand: DemandOut | None = None
    # The draft booking an accepted match produced, so either side can act on it directly.
    booking_id: WireUUID | None = None
