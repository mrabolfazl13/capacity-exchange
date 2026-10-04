"""CONTRACTS §5.2 capacity models: categories, resources, definitions, availability, exceptions."""
from __future__ import annotations

import uuid
from datetime import date, time

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPkMixin, ck_enum
from app.models.identity import uuid_fk

CAPACITY_MODES = ("scheduled", "quantity", "open_ended")
RESOURCE_STATUSES = ("draft", "active", "archived")
OVERRIDE_KINDS = ("closed", "extra", "reduced")


class CapacityCategory(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "capacity_categories"

    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("capacity_categories.id", ondelete="SET NULL"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    attributes_schema: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (CheckConstraint("parent_id IS NULL OR parent_id <> id", name="no_self_parent"),)


class CapacityResource(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "capacity_resources"

    org_id: Mapped[uuid.UUID] = uuid_fk("organizations.id")
    category_id: Mapped[uuid.UUID] = uuid_fk("capacity_categories.id")
    creator_id: Mapped[uuid.UUID] = uuid_fk("users.id")
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    capacity_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'quantity'"))
    address: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{\"line1\":\"\",\"city\":\"\",\"country\":\"\"}'::jsonb"))
    lat: Mapped[float | None] = mapped_column(Numeric(8, 5))
    lon: Mapped[float | None] = mapped_column(Numeric(8, 5))
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("'UTC'"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'draft'"))
    attributes: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    photos: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    documents: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))

    definitions: Mapped[list["CapacityDefinition"]] = relationship(
        back_populates="resource", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint(ck_enum("capacity_resources_capacity_mode", "capacity_mode", CAPACITY_MODES), name="capacity_mode"),
        CheckConstraint(ck_enum("capacity_resources_status", "status", RESOURCE_STATUSES), name="status"),
        CheckConstraint("lat IS NULL OR (lat BETWEEN -90 AND 90)", name="lat_range"),
        CheckConstraint("lon IS NULL OR (lon BETWEEN -180 AND 180)", name="lon_range"),
        Index("ix_capacity_resources_org_id", "org_id"),
        Index("ix_capacity_resources_category_id_status", "category_id", "status"),
        Index("ix_capacity_resources_lat_lon", "lat", "lon"),
    )


class CapacityDefinition(UUIDPkMixin, TimestampMixin, Base):
    """The bookable unit inside a resource; `max_quantity` is the concurrency ceiling (§5.5)."""

    __tablename__ = "capacity_definitions"

    resource_id: Mapped[uuid.UUID] = uuid_fk("capacity_resources.id")
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    unit_label: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'unit'"))
    min_quantity: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    max_quantity: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    slot_duration_minutes: Mapped[int | None] = mapped_column(Integer)
    buffer_before_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    buffer_after_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    attributes: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    resource: Mapped[CapacityResource] = relationship(back_populates="definitions")
    recurring: Mapped[list["RecurringAvailability"]] = relationship(
        back_populates="definition", cascade="all, delete-orphan")
    overrides: Mapped[list["AvailabilityOverride"]] = relationship(
        back_populates="definition", cascade="all, delete-orphan")
    exceptions: Mapped[list["CapacityDefinitionException"]] = relationship(
        back_populates="definition", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("min_quantity >= 1", name="min_quantity_positive"),
        CheckConstraint("max_quantity >= min_quantity", name="max_quantity_ge_min"),
        CheckConstraint("slot_duration_minutes IS NULL OR slot_duration_minutes >= 5", name="slot_duration"),
        CheckConstraint("buffer_before_minutes >= 0", name="buffer_before"),
        CheckConstraint("buffer_after_minutes >= 0", name="buffer_after"),
        Index("ix_capacity_definitions_resource_id", "resource_id"),
    )


class RecurringAvailability(UUIDPkMixin, TimestampMixin, Base):
    """dow follows ISO order: 0 = Monday … 6 = Sunday (§1)."""

    __tablename__ = "recurring_availabilities"

    definition_id: Mapped[uuid.UUID] = uuid_fk("capacity_definitions.id")
    dow: Mapped[int] = mapped_column(Integer, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_until: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    definition: Mapped[CapacityDefinition] = relationship(back_populates="recurring")

    __table_args__ = (
        CheckConstraint("dow BETWEEN 0 AND 6", name="dow_range"),
        CheckConstraint("end_time > start_time", name="time_order"),
        CheckConstraint("quantity >= 1", name="quantity_positive"),
        CheckConstraint("valid_from IS NULL OR valid_until IS NULL OR valid_until >= valid_from", name="validity_order"),
        UniqueConstraint("definition_id", "dow", "start_time", name="uq_recurring_availabilities_definition_id"),
        Index("ix_recurring_availabilities_definition_id", "definition_id"),
    )


class AvailabilityOverride(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "availability_overrides"

    definition_id: Mapped[uuid.UUID] = uuid_fk("capacity_definitions.id")
    override_date: Mapped[date] = mapped_column(Date, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    start_time: Mapped[time | None] = mapped_column(Time)
    end_time: Mapped[time | None] = mapped_column(Time)
    quantity: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    definition: Mapped[CapacityDefinition] = relationship(back_populates="overrides")

    __table_args__ = (
        CheckConstraint(ck_enum("availability_overrides_kind", "kind", OVERRIDE_KINDS), name="kind"),
        CheckConstraint(
            "(start_time IS NULL) = (end_time IS NULL)", name="pair_times"),
        CheckConstraint(
            "start_time IS NULL OR end_time > start_time", name="time_order"),
        CheckConstraint("quantity IS NULL OR quantity >= 0", name="quantity_non_negative"),
        Index(
            "uq_availability_overrides_slot",
            "definition_id",
            "override_date",
            "start_time",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )


class CapacityDefinitionException(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "capacity_definition_exceptions"

    definition_id: Mapped[uuid.UUID] = uuid_fk("capacity_definitions.id")
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    max_quantity_delta: Mapped[int | None] = mapped_column(Integer)
    is_closed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    reason: Mapped[str | None] = mapped_column(Text)

    definition: Mapped[CapacityDefinition] = relationship(back_populates="exceptions")

    __table_args__ = (
        CheckConstraint(
            "effective_from IS NULL OR effective_to IS NULL OR effective_to >= effective_from",
            name="validity_order"),
        Index("ix_capacity_definition_exceptions_definition_id", "definition_id"),
    )
