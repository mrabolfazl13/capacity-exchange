"""§5.2 capacity DTOs: categories, resources, definitions, availability, expansion results."""
from __future__ import annotations

from datetime import time
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.common import Address, DocumentRef, ORMModel, Photo, WireDate, WireDateTime, WireTime, WireUUID

CapacityMode = Literal["scheduled", "quantity", "open_ended"]
ResourceStatus = Literal["draft", "active", "archived"]
OverrideKind = Literal["closed", "extra", "reduced"]


class CapacityCategoryOut(ORMModel):
    id: WireUUID
    key: str
    label: str
    parent_id: WireUUID | None = None
    is_active: bool
    attributes_schema: dict | None = None


class CapacityDefinitionOut(ORMModel):
    id: WireUUID
    resource_id: WireUUID
    name: str
    unit_label: str
    min_quantity: int
    max_quantity: int
    slot_duration_minutes: int | None
    buffer_before_minutes: int
    buffer_after_minutes: int
    attributes: dict
    is_active: bool


class CapacityResourceOut(ORMModel):
    id: WireUUID
    org_id: WireUUID
    category_id: WireUUID
    creator_id: WireUUID
    name: str
    description: str | None = None
    capacity_mode: CapacityMode
    address: Address
    lat: float | None = None
    lon: float | None = None
    timezone: str
    status: ResourceStatus
    attributes: dict
    photos: list[Photo] = []
    documents: list[DocumentRef] = []
    created_at: WireDateTime
    updated_at: WireDateTime
    definitions: list[CapacityDefinitionOut] = []


class RecurringAvailabilityOut(ORMModel):
    id: WireUUID
    definition_id: WireUUID
    dow: int
    start_time: WireTime
    end_time: WireTime
    quantity: int
    valid_from: WireDate | None
    valid_until: WireDate | None
    is_active: bool


class AvailabilityOverrideOut(ORMModel):
    id: WireUUID
    definition_id: WireUUID
    override_date: WireDate
    kind: OverrideKind
    start_time: WireTime | None
    end_time: WireTime | None
    quantity: int | None
    reason: str | None


class DefinitionInput(ORMModel):
    name: str = Field(min_length=2, max_length=200)
    unit_label: str = Field(min_length=1, max_length=32)
    min_quantity: int = Field(default=1, ge=1)
    max_quantity: int = Field(default=1, ge=1)
    slot_duration_minutes: int | None = Field(default=None, ge=5)
    buffer_before_minutes: int = Field(default=0, ge=0, le=1440)
    buffer_after_minutes: int = Field(default=0, ge=0, le=1440)
    attributes: dict = {}

    @field_validator("max_quantity")
    @classmethod
    def _max_ge_min(cls, v: int, info) -> int:
        min_q = info.data.get("min_quantity")
        if min_q is not None and v < min_q:
            raise ValueError("max_quantity must be >= min_quantity")
        return v


class CapacityResourceInput(ORMModel):
    category_id: UUID
    org_id: UUID | None = None
    name: str = Field(min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=8000)
    capacity_mode: CapacityMode = "quantity"
    address: Address = Address()
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    timezone: str = "UTC"
    attributes: dict = {}
    photos: list[Photo] = []
    documents: list[DocumentRef] = []
    definitions: list[DefinitionInput] = []


class DefinitionPatch(ORMModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    unit_label: str | None = Field(default=None, min_length=1, max_length=32)
    min_quantity: int | None = Field(default=None, ge=1)
    max_quantity: int | None = Field(default=None, ge=1)
    slot_duration_minutes: int | None = Field(default=None, ge=5)
    buffer_before_minutes: int | None = Field(default=None, ge=0, le=1440)
    buffer_after_minutes: int | None = Field(default=None, ge=0, le=1440)
    attributes: dict | None = None
    is_active: bool | None = None


class CapacityResourcePatch(ORMModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=8000)
    address: Address | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    timezone: str | None = None
    status: ResourceStatus | None = None
    attributes: dict | None = None
    photos: list[Photo] | None = None
    documents: list[DocumentRef] | None = None


class RecurringAvailabilityInput(ORMModel):
    definition_id: UUID | None = None
    dow: int = Field(ge=0, le=6)
    start_time: WireTime
    end_time: WireTime
    quantity: int = Field(default=1, ge=1)
    valid_from: WireDate | None = None
    valid_until: WireDate | None = None

    @field_validator("end_time")
    @classmethod
    def _order(cls, v: time, info) -> time:
        start = info.data.get("start_time")
        if start is not None and v <= start:
            raise ValueError("end_time must be after start_time")
        return v


class OverrideInput(ORMModel):
    definition_id: UUID
    override_date: WireDate
    kind: OverrideKind
    start_time: WireTime | None = None
    end_time: WireTime | None = None
    quantity: int | None = Field(default=None, ge=0)
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("end_time")
    @classmethod
    def _pair(cls, v: time | None, info) -> time | None:
        start = info.data.get("start_time")
        if (start is None) != (v is None):
            raise ValueError("start_time and end_time must be provided together")
        if start is not None and v is not None and v <= start:
            raise ValueError("end_time must be after start_time")
        return v


class AvailabilityItemPatch(ORMModel):
    quantity: int | None = Field(default=None, ge=0)
    is_active: bool | None = None
    start_time: WireTime | None = None
    end_time: WireTime | None = None
    valid_from: WireDate | None = None
    valid_until: WireDate | None = None
    reason: str | None = None


class FreeWindow(ORMModel):
    window_start: WireDateTime
    window_end: WireDateTime
    free_quantity: int


class AvailabilityDay(ORMModel):
    date: WireDate
    total_quantity: int
    booked_quantity: int
    free_quantity: int
    closed: bool


class AvailabilityResponse(ORMModel):
    days: list[AvailabilityDay] = []
    recurring: list[RecurringAvailabilityOut] = []
    overrides: list[AvailabilityOverrideOut] = []
