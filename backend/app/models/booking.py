"""CONTRACTS §5.4 booking models — the safety core.

One physical `bookings` table carries the whole lifecycle: a hold is simply a booking row
in status `hold`, which removes any cross-table double-counting.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPkMixin
from app.models.identity import uuid_fk

BOOKING_STATUSES = (
    "draft", "hold", "confirmed", "in_progress", "completed", "cancelled", "expired", "disputed")
BOOKING_PAYMENT_STATUSES = ("not_required", "unpaid", "paid", "refunded", "partially_refunded")

# Real Postgres enums (§5): the concurrency path filters on these columns.
bookings_status_enum = ENUM(*BOOKING_STATUSES, name="bookings_status_enum", create_type=False)
bookings_payment_enum = ENUM(
    *BOOKING_PAYMENT_STATUSES, name="bookings_payment_enum", create_type=False)

ACTIVE_BOOKING_STATUSES = ("hold", "confirmed", "in_progress")
TERMINAL_BOOKING_STATUSES = ("completed", "expired", "cancelled")

#: Server-enforced state machine (§5.5). Anything else => 400 invalid_state_transition.
BOOKING_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"confirmed", "cancelled"}),
    "hold": frozenset({"confirmed", "expired", "cancelled"}),
    "confirmed": frozenset({"in_progress", "cancelled", "disputed"}),
    "in_progress": frozenset({"completed", "disputed"}),
    "disputed": frozenset({"cancelled", "completed"}),
    "completed": frozenset(),
    "expired": frozenset(),
    "cancelled": frozenset(),
}


class Booking(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "bookings"

    offer_id: Mapped[uuid.UUID] = uuid_fk("offers.id")
    definition_id: Mapped[uuid.UUID] = uuid_fk("capacity_definitions.id")
    org_id: Mapped[uuid.UUID] = uuid_fk("organizations.id")
    customer_id: Mapped[uuid.UUID] = uuid_fk("users.id")
    created_by_user_id: Mapped[uuid.UUID] = uuid_fk("users.id")
    source_match_id: Mapped[uuid.UUID | None] = uuid_fk("matches.id", nullable=True)

    status: Mapped[str] = mapped_column(
        bookings_status_enum, nullable=False, server_default=text("'hold'"))
    payment_status: Mapped[str] = mapped_column(
        bookings_payment_enum, nullable=False, server_default=text("'unpaid'"))

    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    unit_amount_cents: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default=text("'USD'"))
    total_cents: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    hold_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    request_fingerprint: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    cancel_reason: Mapped[str | None] = mapped_column(Text)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispute_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    meta: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    events: Mapped[list["BookingStatusEvent"]] = relationship(
        back_populates="booking", cascade="all, delete-orphan",
        order_by="BookingStatusEvent.created_at")

    __table_args__ = (
        CheckConstraint("quantity >= 1", name="quantity_positive"),
        CheckConstraint("total_cents >= 0", name="total_non_negative"),
        CheckConstraint("unit_amount_cents >= 0", name="unit_amount_non_negative"),
        CheckConstraint("window_end > window_start", name="window_order"),
        CheckConstraint("length(currency) = 3", name="currency_len"),
        Index("ix_bookings_customer_id_status", "customer_id", "status"),
        Index("ix_bookings_org_id_status", "org_id", "status"),
        Index("ix_bookings_offer_id_status", "offer_id", "status"),
        Index("ix_bookings_definition_status_window", "definition_id", "status", "window_start"),
        Index("ix_bookings_hold_expiry", "status", "hold_expires_at"),
        Index(
            "uq_bookings_request_fingerprint",
            "definition_id",
            "request_fingerprint",
            unique=True,
            postgresql_where=text("request_fingerprint IS NOT NULL"),
        ),
    )

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_BOOKING_STATUSES

    @property
    def can_transition_to(self) -> frozenset[str]:
        return BOOKING_TRANSITIONS.get(self.status, frozenset())


class BookingStatusEvent(UUIDPkMixin, Base):
    __tablename__ = "booking_status_events"

    booking_id: Mapped[uuid.UUID] = uuid_fk("bookings.id")
    from_status: Mapped[str | None] = mapped_column(String(24))
    to_status: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reason: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"),
        comment="occurred-at per §5.4")

    booking: Mapped[Booking] = relationship(back_populates="events")

    __table_args__ = (Index("ix_booking_status_events_booking_id", "booking_id"),)
