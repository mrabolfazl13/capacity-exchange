"""CONTRACTS §5.3 marketplace models: offers, demands, matches."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    literal_column,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPkMixin, ck_enum
from app.models.identity import uuid_fk

OFFER_PRICING_MODES = ("per_unit_time", "per_quantity", "flat")
BOOKING_MODES = ("request_confirm", "instant")
OFFER_STATUSES = ("draft", "published", "paused", "closed")
DEMAND_STATUSES = ("open", "matched", "closed", "cancelled")
MATCH_STATUSES = ("suggested", "accepted", "declined", "expired")

DEFAULT_CANCELLATION_POLICY = (
    '[{"hours_before":24,"refund_pct":100},{"hours_before":0,"refund_pct":0}]'
)


class Offer(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "offers"

    definition_id: Mapped[uuid.UUID] = uuid_fk("capacity_definitions.id")
    org_id: Mapped[uuid.UUID] = uuid_fk("organizations.id")
    resource_id: Mapped[uuid.UUID] = uuid_fk("capacity_resources.id")
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    pricing_mode: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default=text("'per_quantity'"))
    unit_amount_cents: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default=text("'USD'"))
    min_lead_time_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_lead_time_days: Mapped[int | None] = mapped_column(Integer)
    min_duration_minutes: Mapped[int | None] = mapped_column(Integer)
    max_duration_minutes: Mapped[int | None] = mapped_column(Integer)
    min_quantity: Mapped[int | None] = mapped_column(Integer)
    max_quantity: Mapped[int | None] = mapped_column(Integer)
    booking_mode: Mapped[str] = mapped_column(String(24), nullable=False, server_default=text("'instant'"))
    hold_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("15"))
    cancellation_policy: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=literal_column(
            f"'{DEFAULT_CANCELLATION_POLICY}'::jsonb"))
    requires_payment: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    commission_rate_bp: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'draft'"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    # search_vector (GENERATED tsvector) + its GIN index live in the migration: a generated
    # column must never appear in SQLAlchemy INSERTs.

    __table_args__ = (
        CheckConstraint("char_length(title) BETWEEN 3 AND 120", name="title_len"),
        CheckConstraint(ck_enum("offers_pricing_mode", "pricing_mode", OFFER_PRICING_MODES), name="pricing_mode"),
        CheckConstraint(ck_enum("offers_booking_mode", "booking_mode", BOOKING_MODES), name="booking_mode"),
        CheckConstraint(ck_enum("offers_status", "status", OFFER_STATUSES), name="status"),
        CheckConstraint("unit_amount_cents >= 0", name="unit_amount_non_negative"),
        CheckConstraint("hold_minutes BETWEEN 5 AND 120", name="hold_minutes_range"),
        CheckConstraint("min_lead_time_minutes >= 0", name="lead_time_non_negative"),
        CheckConstraint(
            "max_duration_minutes IS NULL OR min_duration_minutes IS NULL "
            "OR max_duration_minutes >= min_duration_minutes", name="duration_order"),
        CheckConstraint(
            "max_quantity IS NULL OR min_quantity IS NULL OR max_quantity >= min_quantity",
            name="quantity_order"),
        Index("ix_offers_status_published", "status", postgresql_where=text("status = 'published'")),
        Index("ix_offers_org_id", "org_id"),
        Index("ix_offers_resource_id", "resource_id"),
        Index("ix_offers_definition_id", "definition_id"),
    )


class Demand(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "demands"

    customer_id: Mapped[uuid.UUID] = uuid_fk("users.id")
    org_id: Mapped[uuid.UUID | None] = uuid_fk("organizations.id", nullable=True)
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("capacity_categories.id", ondelete="SET NULL"), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    address: Mapped[dict | None] = mapped_column(JSONB)
    lat: Mapped[float | None] = mapped_column(Numeric(8, 5))
    lon: Mapped[float | None] = mapped_column(Numeric(8, 5))
    desired_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    desired_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    budget_min_cents: Mapped[int | None] = mapped_column(Integer)
    budget_max_cents: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'open'"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(ck_enum("demands_status", "status", DEMAND_STATUSES), name="status"),
        CheckConstraint("quantity >= 1", name="quantity_positive"),
        CheckConstraint(
            "budget_min_cents IS NULL OR budget_max_cents IS NULL OR budget_max_cents >= budget_min_cents",
            name="budget_order"),
        Index("ix_demands_status", "status"),
        Index("ix_demands_customer_id", "customer_id"),
    )


class Match(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "matches"

    demand_id: Mapped[uuid.UUID] = uuid_fk("demands.id")
    offer_id: Mapped[uuid.UUID] = uuid_fk("offers.id")
    score: Mapped[float] = mapped_column(Numeric(6, 3), nullable=False, server_default=text("0"))
    reasons: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'suggested'"))
    provider_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(ck_enum("matches_status", "status", MATCH_STATUSES), name="status"),
        CheckConstraint("score >= 0 AND score <= 1000", name="score_range"),
        UniqueConstraint("demand_id", "offer_id", name="uq_matches_demand_id_offer_id"),
        Index("ix_matches_demand_id_status", "demand_id", "status"),
        Index("ix_matches_offer_id", "offer_id"),
    )
