"""Capacity category registry (CONTRACTS §5.2 seed list).

`capacity_categories` is reference data, not user data: it is upserted by the seeder and by
the test harness, and admins can rename/deactivate entries through `/admin/categories`.
Lookups are by stable `key`, so ids can differ between environments.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.capacity import CapacityCategory

CATEGORIES: tuple[dict[str, Any], ...] = (
    {"key": "meeting_room", "label": "Meeting room",
     "attributes_schema": {"capacity_persons": "integer", "has_projector": "boolean",
                           "has_whiteboard": "boolean", "floor": "string"}},
    {"key": "warehouse", "label": "Warehouse space",
     "attributes_schema": {"pallet_positions": "integer", "square_meters": "number",
                           "loading_dock": "boolean", "temperature_controlled": "boolean"}},
    {"key": "parking", "label": "Parking",
     "attributes_schema": {"vehicle_height_m": "number", "covered": "boolean",
                           "ev_charging": "boolean"}},
    {"key": "equipment", "label": "Equipment",
     "attributes_schema": {"model": "string", "power_kw": "number", "operator_included": "boolean"}},
    {"key": "appointment_slot", "label": "Appointment slot",
     "attributes_schema": {"practitioner": "string", "service": "string", "room": "string"}},
    {"key": "truck_return", "label": "Truck return capacity",
     "attributes_schema": {"route": "string", "capacity_cbm": "number",
                           "max_weight_kg": "number", "refrigerated": "boolean"}},
    {"key": "commercial_kitchen", "label": "Commercial kitchen",
     "attributes_schema": {"burners": "integer", "oven_count": "integer",
                           "licensed_for_delivery": "boolean", "square_meters": "number"}},
    {"key": "production_slot", "label": "Production slot",
     "attributes_schema": {"machine": "string", "tolerance_mm": "number",
                           "min_batch_units": "integer"}},
    {"key": "storage_space", "label": "Storage space",
     "attributes_schema": {"square_meters": "number", "shelved": "boolean",
                           "access_hours": "string"}},
    {"key": "hospitality_room", "label": "Hospitality room",
     "attributes_schema": {"capacity_persons": "integer", "catering_available": "boolean",
                           "wheelchair_accessible": "boolean"}},
    {"key": "workstation", "label": "Workstation",
     "attributes_schema": {"desk_type": "string", "monitors": "integer",
                           "network_gbps": "number"}},
    {"key": "transport_vehicle", "label": "Transport vehicle",
     "attributes_schema": {"vehicle_type": "string", "payload_kg": "number",
                           "driver_included": "boolean"}},
)


async def ensure_categories(session: AsyncSession) -> int:
    """Insert only the missing keys; existing labels/attribute hints are left alone."""
    existing = set((await session.execute(
        select(CapacityCategory.key))).scalars().all())
    created = 0
    for entry in CATEGORIES:
        if entry["key"] in existing:
            continue
        session.add(CapacityCategory(key=entry["key"], label=entry["label"],
                                     attributes_schema=entry["attributes_schema"]))
        created += 1
    if created:
        await session.flush()
    return created


async def category_by_key(session: AsyncSession, key: str) -> CapacityCategory | None:
    return (await session.execute(
        select(CapacityCategory).where(CapacityCategory.key == key))).scalar_one_or_none()
