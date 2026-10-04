"""CONTRACTS §5.7 cross-cutting models: idempotency, audit, notifications, chat, promotions, disputes, reviews."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPkMixin, ck_enum
from app.models.identity import uuid_fk

NOTIFICATION_CHANNELS = ("in_app", "email", "sms", "push")
CONVERSATION_STATUSES = ("open", "closed", "archived")
PROMOTION_KINDS = ("coupon", "campaign")
PROMO_STATUSES = ("draft", "active", "expired", "disabled")
DISPUTE_KINDS = ("quality", "no_show", "payment", "damage", "other")
DISPUTE_STATUSES = (
    "open", "under_review", "resolved_refund", "resolved_partial", "resolved_no_fault", "closed")
REVIEW_STATUSES = ("published", "pending_moderation", "removed")


class IdempotencyKey(UUIDPkMixin, TimestampMixin, Base):
    """§5.7: same key + same request hash replays the stored response; different hash => 409."""

    __tablename__ = "idempotency_keys"

    user_id: Mapped[uuid.UUID] = uuid_fk("users.id")
    route: Mapped[str] = mapped_column(String(160), nullable=False)
    idem_key: Mapped[str] = mapped_column(String(160), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict | None] = mapped_column(JSONB)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "route", "idem_key", name="uq_idempotency_keys_scope"),
        Index("ix_idempotency_keys_expires_at", "expires_at"),
    )


class AuditLog(UUIDPkMixin, Base):
    __tablename__ = "audit_logs"

    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(96), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("ix_audit_logs_entity", "entity_type", "entity_id"),
        Index("ix_audit_logs_created_at", "created_at"),
        Index("ix_audit_logs_actor_user_id", "actor_user_id"),
    )


class Notification(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "notifications"

    user_id: Mapped[uuid.UUID] = uuid_fk("users.id")
    org_id: Mapped[uuid.UUID | None] = uuid_fk("organizations.id", nullable=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    channel: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'in_app'"))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(ck_enum("notifications_channel", "channel", NOTIFICATION_CHANNELS), name="channel"),
        Index("ix_notifications_user_read", "user_id", "read_at"),
        Index("ix_notifications_user_created", "user_id", "created_at"),
    )


class Conversation(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "conversations"

    kind: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'booking'"))
    ref_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    org_id: Mapped[uuid.UUID | None] = uuid_fk("organizations.id", nullable=True)
    customer_id: Mapped[uuid.UUID] = uuid_fk("users.id")
    provider_org_id: Mapped[uuid.UUID] = uuid_fk("organizations.id")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'open'"))
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(ck_enum("conversations_status", "status", CONVERSATION_STATUSES), name="status"),
        UniqueConstraint("kind", "ref_id", "customer_id", name="uq_conversations_scope"),
        Index("ix_conversations_provider_org_status", "provider_org_id", "status"),
        Index("ix_conversations_customer_id", "customer_id"),
    )


class Message(UUIDPkMixin, Base):
    __tablename__ = "messages"

    conversation_id: Mapped[uuid.UUID] = uuid_fk("conversations.id")
    sender_id: Mapped[uuid.UUID] = uuid_fk("users.id")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    read_receipts: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("length(body) BETWEEN 1 AND 4000", name="body_len"),
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
    )


class Promotion(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "promotions"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'coupon'"))
    code: Mapped[str | None] = mapped_column(String(64), unique=True)
    discount_config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    min_order_cents: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    applies_to: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    usage_limit: Mapped[int | None] = mapped_column(Integer)
    used_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    per_user_limit: Mapped[int | None] = mapped_column(Integer)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'draft'"))
    created_by: Mapped[uuid.UUID | None] = uuid_fk("users.id", nullable=True)

    __table_args__ = (
        CheckConstraint(ck_enum("promotions_kind", "kind", PROMOTION_KINDS), name="kind"),
        CheckConstraint(ck_enum("promotions_status", "status", PROMO_STATUSES), name="status"),
        CheckConstraint("ends_at > starts_at", name="window_order"),
        CheckConstraint("min_order_cents >= 0", name="min_order_non_negative"),
        CheckConstraint("used_count >= 0", name="used_count_non_negative"),
    )


class CouponRedemption(UUIDPkMixin, Base):
    __tablename__ = "coupon_redemptions"

    promotion_id: Mapped[uuid.UUID] = uuid_fk("promotions.id")
    user_id: Mapped[uuid.UUID] = uuid_fk("users.id")
    order_id: Mapped[uuid.UUID | None] = uuid_fk("orders.id", nullable=True)
    discount_cents: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    redeemed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("promotion_id", "user_id", "order_id", name="uq_coupon_redemptions_scope"),
        CheckConstraint("discount_cents >= 0", name="discount_non_negative"),
    )


class Dispute(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "disputes"

    booking_id: Mapped[uuid.UUID] = uuid_fk("bookings.id")
    org_id: Mapped[uuid.UUID] = uuid_fk("organizations.id")
    complainant_id: Mapped[uuid.UUID] = uuid_fk("users.id")
    kind: Mapped[str] = mapped_column(String(24), nullable=False, server_default=text("'other'"))
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'open'"))
    resolution_note: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[uuid.UUID | None] = uuid_fk("users.id", nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refund_cents: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    __table_args__ = (
        CheckConstraint(ck_enum("disputes_kind", "kind", DISPUTE_KINDS), name="kind"),
        CheckConstraint(ck_enum("disputes_status", "status", DISPUTE_STATUSES), name="status"),
        CheckConstraint("refund_cents >= 0", name="refund_non_negative"),
        Index("ix_disputes_status", "status"),
        Index("ix_disputes_org_id", "org_id"),
    )


class Review(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "reviews"

    booking_id: Mapped[uuid.UUID] = uuid_fk("bookings.id")
    fulfillment_id: Mapped[uuid.UUID] = uuid_fk("fulfillments.id")
    offer_id: Mapped[uuid.UUID] = uuid_fk("offers.id")
    org_id: Mapped[uuid.UUID] = uuid_fk("organizations.id")
    reviewer_id: Mapped[uuid.UUID] = uuid_fk("users.id")
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    provider_reply: Mapped[str | None] = mapped_column(Text)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default=text("'published'"))

    __table_args__ = (
        CheckConstraint("rating BETWEEN 1 AND 5", name="rating_range"),
        CheckConstraint(ck_enum("reviews_status", "status", REVIEW_STATUSES), name="status"),
        UniqueConstraint("fulfillment_id", name="uq_reviews_fulfillment_id"),
        Index("ix_reviews_offer_id_status", "offer_id", "status"),
        Index("ix_reviews_org_id", "org_id"),
    )
