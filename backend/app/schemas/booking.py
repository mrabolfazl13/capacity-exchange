"""§5.4 booking DTOs — hold requests, booking creation, transitions and timeline."""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.commerce import FulfillmentOut, OrderOut
from app.schemas.common import ORMModel, WireDateTime, WireUUID
from app.schemas.marketplace import OfferOut

BookingStatus = Literal["draft", "hold", "confirmed", "in_progress", "completed",
                        "cancelled", "expired", "disputed"]
BookingPaymentStatus = Literal["not_required", "unpaid", "paid", "refunded", "partially_refunded"]


class HoldRequest(ORMModel):
    offer_id: UUID
    window_start: WireDateTime
    window_end: WireDateTime
    quantity: int = Field(default=1, ge=1)
    request_fingerprint: UUID | None = None

    @field_validator("window_end")
    @classmethod
    def _window(cls, v, info):
        start = info.data.get("window_start")
        if start is not None and v <= start:
            raise ValueError("window_end must be after window_start")
        return v


class BookingCreate(HoldRequest):
    """`hold_id` converts an existing hold; otherwise the fields create a booking directly."""

    hold_id: UUID | None = None


class BookingOut(ORMModel):
    id: WireUUID
    offer_id: WireUUID
    definition_id: WireUUID
    org_id: WireUUID
    customer_id: WireUUID
    created_by_user_id: WireUUID
    source_match_id: WireUUID | None
    status: BookingStatus
    payment_status: BookingPaymentStatus
    window_start: WireDateTime
    window_end: WireDateTime
    quantity: int
    unit_amount_cents: int
    currency: str
    total_cents: int
    hold_expires_at: WireDateTime | None
    request_fingerprint: WireUUID | None
    cancel_reason: str | None
    cancelled_at: WireDateTime | None
    cancelled_by: WireUUID | None
    confirmed_at: WireDateTime | None
    started_at: WireDateTime | None
    completed_at: WireDateTime | None
    dispute_opened_at: WireDateTime | None
    meta: dict
    created_at: WireDateTime
    offer: OfferOut | None = None
    fulfillment: FulfillmentOut | None = None
    order: OrderOut | None = None
    review_submitted: bool = False
    # Detail-view convenience so the UI can render context without extra round-trips.
    offer_title: str | None = None
    org_name: str | None = None
    resource_name: str | None = None
    customer_name: str | None = None


class BookingStatusEventOut(ORMModel):
    id: WireUUID
    booking_id: WireUUID
    from_status: BookingStatus | None
    to_status: BookingStatus
    actor_user_id: WireUUID | None
    reason: str | None
    meta: dict
    created_at: WireDateTime


class CancelRequest(ORMModel):
    reason: str = Field(min_length=3, max_length=1000)
