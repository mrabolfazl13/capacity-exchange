"""§5.6 commerce + §5.7 cross-cutting DTOs: orders, payments, fulfillment, reviews,
notifications, conversations, disputes, promotions, audit and dashboard aggregates."""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.schemas.common import ORMModel, WireDateTime, WireUUID

OrderStatus = Literal["draft", "placed", "paid", "fulfilled", "cancelled", "refunded"]
OrderPaymentStatus = Literal["not_required", "unpaid", "paid", "refunded", "partially_refunded"]
PaymentStatus = Literal["created", "pending", "succeeded", "failed", "canceled", "refunded"]
FulfillmentStatus = Literal["pending", "in_progress", "completed", "no_show", "failed"]
ReviewStatus = Literal["published", "pending_moderation", "removed"]
NotificationChannel = Literal["in_app", "email", "sms", "push"]
ConversationStatus = Literal["open", "closed", "archived"]
DisputeKind = Literal["quality", "no_show", "payment", "damage", "other"]
DisputeStatus = Literal["open", "under_review", "resolved_refund", "resolved_partial",
                        "resolved_no_fault", "closed"]


class OrderLineItem(ORMModel):
    description: str
    qty: int
    unit_cents: int
    total_cents: int


class OrderOut(ORMModel):
    id: WireUUID
    number: str
    buyer_id: WireUUID
    provider_org_id: WireUUID
    booking_id: WireUUID | None
    promotion_id: WireUUID | None
    subtotal_cents: int
    discount_cents: int
    commission_cents: int
    total_cents: int
    refunded_cents: int
    currency: str
    line_items: list[OrderLineItem] = []
    payment_status: OrderPaymentStatus
    status: OrderStatus
    placed_at: WireDateTime | None
    created_at: WireDateTime
    provider_org_name: str | None = None


class PaymentOut(ORMModel):
    id: WireUUID
    order_id: WireUUID
    provider_key: str
    amount_cents: int
    currency: str
    status: PaymentStatus
    client_secret: str | None
    provider_payment_id: str | None
    failure_reason: str | None
    confirmed_at: WireDateTime | None
    created_at: WireDateTime


class OrderCreateInput(ORMModel):
    """§5.7 idempotent `POST /orders`: open the order that carries a live booking."""

    booking_id: UUID
    coupon_code: str | None = Field(default=None, min_length=3, max_length=64)


class PaymentIntentInput(ORMModel):
    order_id: UUID
    provider_key: str = Field(default="mock", min_length=1, max_length=32)


class FulfillmentNoteInput(ORMModel):
    body: str = Field(min_length=1, max_length=4000)


class FulfillmentNote(ORMModel):
    body: str
    author_id: WireUUID | None = None
    created_at: WireDateTime | None = None


class FulfillmentOut(ORMModel):
    id: WireUUID
    booking_id: WireUUID
    org_id: WireUUID
    status: FulfillmentStatus
    started_at: WireDateTime
    completed_at: WireDateTime | None
    notes: list[FulfillmentNote] = []
    completed_by: WireUUID | None


class ReviewInput(ORMModel):
    booking_id: UUID
    # The fulfilment is derived from the booking; clients that already hold the id may pin it.
    fulfillment_id: UUID | None = None
    rating: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=4000)


class ReviewOut(ORMModel):
    id: WireUUID
    booking_id: WireUUID
    fulfillment_id: WireUUID
    offer_id: WireUUID
    org_id: WireUUID
    reviewer_id: WireUUID
    rating: int
    comment: str | None
    provider_reply: str | None
    replied_at: WireDateTime | None
    status: ReviewStatus
    created_at: WireDateTime
    reviewer_name: str | None = None
    offer_title: str | None = None


class ReviewReplyInput(ORMModel):
    provider_reply: str = Field(min_length=2, max_length=4000)


class NotificationOut(ORMModel):
    id: WireUUID
    user_id: WireUUID
    org_id: WireUUID | None
    kind: str
    title: str
    body: str
    data: dict
    channel: NotificationChannel
    read_at: WireDateTime | None
    sent_at: WireDateTime | None
    created_at: WireDateTime


class ConversationOut(ORMModel):
    id: WireUUID
    kind: str
    ref_id: WireUUID | None
    org_id: WireUUID | None
    customer_id: WireUUID
    provider_org_id: WireUUID
    status: ConversationStatus
    last_message_at: WireDateTime
    created_at: WireDateTime
    peer_name: str | None = None
    last_message_body: str | None = None
    unread_count: int = 0


class ConversationCreateInput(ORMModel):
    kind: str = "booking"
    ref_id: UUID | None = None
    org_id: UUID | None = None
    initial_body: str | None = Field(default=None, max_length=4000)


class ConversationStatusInput(ORMModel):
    status: ConversationStatus


class MessageOut(ORMModel):
    id: WireUUID
    conversation_id: WireUUID
    sender_id: WireUUID
    body: str
    is_system: bool
    read_receipts: dict
    created_at: WireDateTime
    sender_name: str | None = None


class MessageInput(ORMModel):
    body: str = Field(min_length=1, max_length=4000)


class DisputeCreateInput(ORMModel):
    booking_id: UUID
    kind: DisputeKind = "other"
    description: str = Field(min_length=10, max_length=4000)


class DisputeResolveInput(ORMModel):
    status: DisputeStatus
    resolution_note: str = Field(min_length=3, max_length=4000)
    # Only meaningful for resolved_partial; a full refund is whatever the order still holds.
    refund_cents: int | None = Field(default=None, ge=0)


class DisputeOut(ORMModel):
    id: WireUUID
    booking_id: WireUUID
    org_id: WireUUID
    complainant_id: WireUUID
    kind: DisputeKind
    description: str
    status: DisputeStatus
    resolution_note: str | None
    resolved_by: WireUUID | None
    resolved_at: WireDateTime | None
    created_at: WireDateTime
    refund_cents: int = 0
    complainant_name: str | None = None
    org_name: str | None = None


class AuditLogOut(ORMModel):
    id: WireUUID
    actor_user_id: WireUUID | None
    actor_org_id: WireUUID | None
    action: str
    entity_type: str
    entity_id: WireUUID | None
    before: dict | None
    after: dict | None
    #: Stored as a native INET, which the driver hands back as an address object (§1: text).
    ip: str | None
    user_agent: str | None
    request_id: str | None
    created_at: WireDateTime

    @field_validator("ip", mode="before")
    @classmethod
    def _ip_as_text(cls, value: object) -> object:
        return value if value is None or isinstance(value, str) else str(value)


def check_discount_config(config: dict) -> dict:
    """The two shapes a discount may take; `commerce` re-checks this when the money moves."""
    kind = config.get("type")
    if kind == "pct" and isinstance(config.get("bp"), int):
        if not 0 <= config["bp"] <= 10000:
            raise ValueError("bp must be within 0..10000")
        return config
    if kind == "fixed" and isinstance(config.get("cents"), int):
        if config["cents"] < 0:
            raise ValueError("cents must be >= 0")
        return config
    raise ValueError('discount_config must be {"type":"pct","bp":N} or '
                     '{"type":"fixed","cents":N,"currency":"X"}')


class PromotionInput(ORMModel):
    name: str = Field(min_length=2, max_length=120)
    kind: Literal["coupon", "campaign"] = "coupon"
    code: str | None = Field(default=None, min_length=3, max_length=64)
    discount_config: dict
    min_order_cents: int = Field(default=0, ge=0)
    applies_to: dict = {}
    usage_limit: int | None = Field(default=None, ge=1)
    per_user_limit: int | None = Field(default=None, ge=1)
    starts_at: WireDateTime
    ends_at: WireDateTime
    status: Literal["draft", "active", "expired", "disabled"] = "draft"

    @field_validator("code")
    @classmethod
    def _upper_code(cls, v: str | None) -> str | None:
        return v.upper() if isinstance(v, str) else v

    @field_validator("discount_config")
    @classmethod
    def _shape(cls, v: dict) -> dict:
        return check_discount_config(v)

    @model_validator(mode="after")
    def _coherent_offer(self) -> "PromotionInput":
        # The database has the same window rule as a CHECK; catching it here turns a
        # rejected write into a 422 that names the field.
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        if self.kind == "coupon" and not self.code:
            raise ValueError("a coupon needs a code for the buyer to enter")
        if self.kind == "campaign" and self.code:
            raise ValueError("a campaign is not redeemable, so it has no code")
        return self


class PromotionPatch(ORMModel):
    """Only what an operator changes after launch; `used_count` is redemptions, never typed."""

    name: str | None = Field(default=None, min_length=2, max_length=120)
    discount_config: dict | None = None
    min_order_cents: int | None = Field(default=None, ge=0)
    applies_to: dict | None = None
    usage_limit: int | None = Field(default=None, ge=1)
    per_user_limit: int | None = Field(default=None, ge=1)
    starts_at: WireDateTime | None = None
    ends_at: WireDateTime | None = None
    status: Literal["draft", "active", "disabled"] | None = None

    @field_validator("discount_config")
    @classmethod
    def _shape(cls, v: dict | None) -> dict | None:
        return v if v is None else check_discount_config(v)

    @model_validator(mode="after")
    def _not_empty(self) -> "PromotionPatch":
        if not self.model_dump(exclude_none=True):
            raise ValueError("send at least one field to change")
        return self


class PromotionOut(ORMModel):
    id: WireUUID
    name: str
    kind: Literal["coupon", "campaign"]
    code: str | None
    discount_config: dict
    min_order_cents: int
    applies_to: dict
    usage_limit: int | None
    used_count: int
    per_user_limit: int | None
    starts_at: WireDateTime
    ends_at: WireDateTime
    status: Literal["draft", "active", "expired", "disabled"]
    created_by: WireUUID | None
    created_at: WireDateTime
    created_by_name: str | None = None


class CouponRedemptionOut(ORMModel):
    id: WireUUID
    promotion_id: WireUUID
    user_id: WireUUID
    order_id: WireUUID | None
    discount_cents: int
    redeemed_at: WireDateTime
    user_name: str | None = None
    user_email: str | None = None
    order_number: str | None = None
    order_total_cents: int | None = None


class CategoryPatch(ORMModel):
    label: str | None = Field(default=None, min_length=1, max_length=200)
    is_active: bool | None = None


class AdminProviderOut(ORMModel):
    id: WireUUID
    name: str
    slug: str
    status: Literal["active", "suspended"]
    created_at: WireDateTime
    org_admin_email: str | None = None
    offer_count: int = 0
    booking_count: int = 0
    country: str | None = None
    currency: str = "USD"
    timezone: str = "UTC"
    #: Net of refunds, over the organization's whole history — the operator's triage column.
    gmv_cents: int = 0
    rating_avg: float | None = None
    rating_count: int = 0
    open_disputes: int = 0


class AdminDisputeOut(DisputeOut):
    """The queue row plus the context a decision needs, so support opens one list (§8)."""

    booking_status: str | None = None
    window_start: WireDateTime | None = None
    window_end: WireDateTime | None = None
    offer_title: str | None = None
    customer_name: str | None = None
    customer_email: str | None = None
    order_total_cents: int | None = None
    order_refunded_cents: int | None = None
    order_payment_status: str | None = None
    age_hours: float | None = None
