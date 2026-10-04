"""CONTRACTS §5.6 commerce models: orders, payments, refunds, commissions, fulfillment."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPkMixin, ck_enum
from app.models.identity import uuid_fk

ORDER_STATUSES = ("draft", "placed", "paid", "fulfilled", "cancelled", "refunded")
ORDER_PAYMENT_STATUSES = ("not_required", "unpaid", "paid", "partially_refunded", "refunded")
PAYMENT_STATUSES = ("created", "pending", "succeeded", "failed", "canceled", "refunded")
REFUND_STATUSES = ("pending", "succeeded", "failed")
FULFILLMENT_STATUSES = ("pending", "in_progress", "completed", "no_show", "failed")


class Order(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "orders"

    number: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    buyer_id: Mapped[uuid.UUID] = uuid_fk("users.id")
    provider_org_id: Mapped[uuid.UUID] = uuid_fk("organizations.id")
    booking_id: Mapped[uuid.UUID | None] = uuid_fk("bookings.id", nullable=True)
    promotion_id: Mapped[uuid.UUID | None] = uuid_fk("promotions.id", nullable=True)

    subtotal_cents: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    discount_cents: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    commission_cents: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    total_cents: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default=text("'USD'"))
    line_items: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    payment_status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default=text("'unpaid'"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'draft'"))
    placed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refunded_cents: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    __table_args__ = (
        CheckConstraint("subtotal_cents >= 0", name="subtotal_non_negative"),
        CheckConstraint("discount_cents >= 0", name="discount_non_negative"),
        CheckConstraint("commission_cents >= 0", name="commission_non_negative"),
        CheckConstraint("total_cents >= 0", name="total_non_negative"),
        CheckConstraint("refunded_cents >= 0", name="refunded_non_negative"),
        CheckConstraint("length(currency) = 3", name="currency_len"),
        CheckConstraint(ck_enum("orders_status", "status", ORDER_STATUSES), name="status"),
        CheckConstraint(
            ck_enum("orders_payment_status", "payment_status", ORDER_PAYMENT_STATUSES), name="payment_status"),
        Index("ix_orders_buyer_id", "buyer_id"),
        Index("ix_orders_provider_org_id", "provider_org_id"),
        Index("ix_orders_booking_id", "booking_id"),
    )


class Payment(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "payments"

    order_id: Mapped[uuid.UUID] = uuid_fk("orders.id")
    provider_key: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'mock'"))
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default=text("'USD'"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'created'"))
    client_secret: Mapped[str | None] = mapped_column(String(120))
    provider_payment_id: Mapped[str | None] = mapped_column(String(120))
    failure_reason: Mapped[str | None] = mapped_column(Text)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    events: Mapped[list["PaymentEvent"]] = relationship(
        back_populates="payment", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("amount_cents > 0", name="amount_positive"),
        CheckConstraint("length(currency) = 3", name="currency_len"),
        CheckConstraint(ck_enum("payments_status", "status", PAYMENT_STATUSES), name="status"),
        Index(
            "uq_payments_provider_payment_id",
            "provider_key",
            "provider_payment_id",
            unique=True,
            postgresql_where=text("provider_payment_id IS NOT NULL"),
        ),
        Index("ix_payments_order_id", "order_id"),
    )


class PaymentEvent(UUIDPkMixin, Base):
    __tablename__ = "payment_events"

    payment_id: Mapped[uuid.UUID] = uuid_fk("payments.id")
    event_key: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())

    payment: Mapped[Payment] = relationship(back_populates="events")

    __table_args__ = (Index("ix_payment_events_payment_id", "payment_id"),)


class Refund(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "refunds"

    payment_id: Mapped[uuid.UUID] = uuid_fk("payments.id")
    order_id: Mapped[uuid.UUID] = uuid_fk("orders.id")
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default=text("'USD'"))
    reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'pending'"))
    processed_by: Mapped[uuid.UUID | None] = uuid_fk("users.id", nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("amount_cents > 0", name="amount_positive"),
        CheckConstraint("length(currency) = 3", name="currency_len"),
        CheckConstraint(ck_enum("refunds_status", "status", REFUND_STATUSES), name="status"),
        Index("ix_refunds_order_id", "order_id"),
        Index("ix_refunds_payment_id", "payment_id"),
    )


class Commission(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "commissions"

    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), unique=True, nullable=False)
    org_id: Mapped[uuid.UUID] = uuid_fk("organizations.id")
    basis_cents: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    rate_bp: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1000"))
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default=text("'USD'"))
    booked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("basis_cents >= 0", name="basis_non_negative"),
        CheckConstraint("amount_cents >= 0", name="amount_non_negative"),
        CheckConstraint("rate_bp BETWEEN 0 AND 10000", name="rate_bp_range"),
        CheckConstraint("length(currency) = 3", name="currency_len"),
        Index("ix_commissions_org_id", "org_id"),
    )


class Fulfillment(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "fulfillments"

    booking_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bookings.id", ondelete="CASCADE"), unique=True, nullable=False)
    org_id: Mapped[uuid.UUID] = uuid_fk("organizations.id")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'pending'"))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    completed_by: Mapped[uuid.UUID | None] = uuid_fk("users.id", nullable=True)

    __table_args__ = (
        CheckConstraint(ck_enum("fulfillments_status", "status", FULFILLMENT_STATUSES), name="status"),
        Index("ix_fulfillments_org_id_status", "org_id", "status"),
    )
