"""`/api/v1/capacities` + definitions, availability rules, overrides and free-window reads.

Implements the §5.2 capacity surface (endpoint list: CONTRACTS §8). Every write is scoped to
the resource's own tenant, and every mutation leaves an audit row. Free-window reads are
deliberately not org-scoped: a customer picking a slot has to see what is left (§5.2).
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.v1.deps import (
    Actor,
    ActorDep,
    AttrRow,
    PageDep,
    envelope,
    idempotent_write,
    parse_window,
)
from app.core.audit import record_audit
from app.core.deps import SessionDep
from app.core.errors import Conflict, NotFound, OwnershipRequired, ValidationFailed
from app.models.capacity import (
    AvailabilityOverride,
    CapacityCategory,
    CapacityDefinition,
    CapacityResource,
    RecurringAvailability,
)
from app.models.marketplace import Offer
from app.schemas.capacity import (
    AvailabilityDay,
    AvailabilityItemPatch,
    AvailabilityOverrideOut,
    CapacityCategoryOut,
    CapacityDefinitionOut,
    CapacityResourceInput,
    CapacityResourceOut,
    CapacityResourcePatch,
    DefinitionInput,
    DefinitionPatch,
    FreeWindow,
    OverrideInput,
    RecurringAvailabilityInput,
    RecurringAvailabilityOut,
)
from app.services import capacity as cap

router = APIRouter(tags=["capacity"])
catalog_router = APIRouter(prefix="/catalog", tags=["catalog"])

MAX_PLAN_DAYS = 120
MAX_FREE_QUERY_DAYS = 92


def _dump(model, obj) -> dict:
    return model.model_validate(obj).model_dump(mode="json")


def _require_org(actor: Actor, org_id: uuid.UUID) -> None:
    if not actor.can_manage_org(org_id):
        raise OwnershipRequired("This capacity belongs to another organization")


async def _load_resource(session, resource_id: uuid.UUID) -> CapacityResource:
    row = (
        await session.execute(
            select(CapacityResource)
            .where(CapacityResource.id == resource_id)
            .options(selectinload(CapacityResource.definitions))
        )
    ).unique().scalar_one_or_none()
    if row is None:
        raise NotFound("Capacity resource not found")
    return row


async def _load_definition(session, definition_id: uuid.UUID) -> tuple[CapacityDefinition, CapacityResource]:
    row = (
        await session.execute(
            select(CapacityDefinition)
            .where(CapacityDefinition.id == definition_id)
            .options(selectinload(CapacityDefinition.resource))
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("Capacity definition not found")
    return row, row.resource


async def _default_definition(resource: CapacityResource,
                             definition_id: uuid.UUID | None) -> CapacityDefinition:
    """A resource with a single active definition doesn't need the id repeated (§8)."""
    if definition_id is not None:
        definition = next((d for d in resource.definitions if d.id == definition_id), None)
        if definition is None:
            raise NotFound("Capacity definition not found for this resource")
        return definition
    active = [d for d in resource.definitions if d.is_active]
    if len(active) != 1:
        raise ValidationFailed(
            "`definition_id` is required: the resource has "
            f"{len(active)} active definition(s)")
    return active[0]


# --------------------------------------------------------------------------- catalog


@catalog_router.get("/categories")
async def list_categories(session: SessionDep, actor: ActorDep, page: PageDep,
                          active_only: Annotated[bool, Query()] = True) -> dict:
    stmt = select(CapacityCategory)
    if active_only:
        stmt = stmt.where(CapacityCategory.is_active.is_(True))
    total = (await session.execute(
        select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = list((await session.execute(
        stmt.order_by(CapacityCategory.key).limit(page.limit).offset(page.offset)
    )).scalars().all())
    return envelope([_dump(CapacityCategoryOut, r) for r in rows], total, page)


# --------------------------------------------------------------------------- resources


@router.get("/capacities")
async def list_capacities(session: SessionDep, actor: ActorDep, page: PageDep,
                          org_id: uuid.UUID | None = None,
                          category_id: uuid.UUID | None = None,
                          status: Annotated[str | None, Query()] = None) -> dict:
    """Provider workspace: the resources of one tenant; `org_id` defaults to the active org."""
    if actor.is_platform_admin:
        scope_ids = None if org_id is None else {org_id}
    elif org_id is not None:
        _require_org(actor, org_id)
        scope_ids = {org_id}
    else:
        scope_ids = actor.org_ids
    stmt = select(CapacityResource)
    if scope_ids is not None:
        if not scope_ids:
            return envelope([], 0, page)
        stmt = stmt.where(CapacityResource.org_id.in_(scope_ids))
    if category_id is not None:
        stmt = stmt.where(CapacityResource.category_id == category_id)
    if status is not None:
        stmt = stmt.where(CapacityResource.status == status)
    total = (await session.execute(
        select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = list((await session.execute(
        stmt.order_by(CapacityResource.created_at.desc()).limit(page.limit).offset(page.offset)
    )).scalars().all())
    return envelope([_dump(CapacityResourceOut, r) for r in rows], total, page)


@router.post("/capacities", status_code=201)
async def create_capacity(body: CapacityResourceInput, request: Request, session: SessionDep,
                          actor: ActorDep) -> dict:
    org_id = body.org_id or actor.require_org(actor.active_org_id)
    _require_org(actor, org_id)
    category = await session.get(CapacityCategory, body.category_id)
    if category is None or not category.is_active:
        raise ValidationFailed("Unknown or inactive capacity category")
    payload = body.model_dump(mode="json")

    async def build() -> dict:
        resource = CapacityResource(
            org_id=org_id, category_id=category.id, creator_id=actor.user_id,
            name=body.name, description=body.description, capacity_mode=body.capacity_mode,
            address=body.address.model_dump(exclude_none=True), lat=body.lat, lon=body.lon,
            timezone=body.timezone, status="draft", attributes=body.attributes,
            photos=[p.model_dump(exclude_none=True) for p in body.photos],
            documents=[d.model_dump(exclude_none=True) for d in body.documents],
        )
        for definition in body.definitions:
            resource.definitions.append(_definition_orm(definition))
        session.add(resource)
        await session.flush()
        await session.refresh(resource, ["definitions"])  # Pydantic cannot lazy-load in async
        await record_audit(session, request, action="capacity.resource_created",
                           entity_type="capacity_resource", entity_id=resource.id,
                           after=payload, actor_org_id=org_id)
        return _dump(CapacityResourceOut, resource)

    return await idempotent_write(session, request, route="POST /capacities",
                                  actor_id=actor.user_id, payload=payload,
                                  status_code=201, build=build)


@router.get("/capacities/{resource_id}")
async def get_capacity(resource_id: uuid.UUID, session: SessionDep, actor: ActorDep) -> dict:
    resource = await _load_resource(session, resource_id)
    _require_org(actor, resource.org_id)
    return _dump(CapacityResourceOut, resource)


@router.patch("/capacities/{resource_id}")
async def patch_capacity(resource_id: uuid.UUID, body: CapacityResourcePatch, request: Request,
                         session: SessionDep, actor: ActorDep) -> dict:
    resource = await _load_resource(session, resource_id)
    _require_org(actor, resource.org_id)
    changes = body.model_dump(exclude_unset=True, mode="json")
    before = {k: getattr(resource, k) for k in changes}
    for field, value in changes.items():
        setattr(resource, field, value)
    await session.flush()
    await record_audit(session, request, action="capacity.resource_updated",
                       entity_type="capacity_resource", entity_id=resource.id,
                       before=_jsonable(before), after=_jsonable(changes),
                       actor_org_id=resource.org_id)
    return _dump(CapacityResourceOut, resource)


@router.delete("/capacities/{resource_id}", status_code=204)
async def delete_capacity(resource_id: uuid.UUID, request: Request, session: SessionDep,
                          actor: ActorDep) -> None:
    """Hard delete only while no offer points at the resource; otherwise archive with PATCH."""
    resource = await _load_resource(session, resource_id)
    _require_org(actor, resource.org_id)
    offer_count = (await session.execute(
        select(func.count()).select_from(Offer).where(Offer.resource_id == resource.id)
    )).scalar_one()
    if offer_count:
        raise Conflict(f"Resource has {offer_count} offer(s); close them before deleting")
    await session.delete(resource)
    await session.flush()
    await record_audit(session, request, action="capacity.resource_deleted",
                       entity_type="capacity_resource", entity_id=resource_id,
                       actor_org_id=resource.org_id)
    return None


# ----------------------------------------------------------------------- definitions


def _definition_orm(body: DefinitionInput) -> CapacityDefinition:
    return CapacityDefinition(
        name=body.name, unit_label=body.unit_label, min_quantity=body.min_quantity,
        max_quantity=body.max_quantity, slot_duration_minutes=body.slot_duration_minutes,
        buffer_before_minutes=body.buffer_before_minutes,
        buffer_after_minutes=body.buffer_after_minutes, attributes=body.attributes)


def _jsonable(value: dict) -> dict:
    """Audit `before` snapshots hold ORM-native values (Decimal, date); JSON column needs strings."""
    return {k: (str(v) if not isinstance(v, (int, float, bool, list, dict, type(None))) else v)
            for k, v in value.items()}


@router.get("/capacities/{resource_id}/definitions")
async def list_definitions(resource_id: uuid.UUID, session: SessionDep,
                           actor: ActorDep) -> dict:
    resource = await _load_resource(session, resource_id)
    _require_org(actor, resource.org_id)
    items = sorted(resource.definitions, key=lambda d: d.created_at)
    dumped = [_dump(CapacityDefinitionOut, d) for d in items]
    return {"items": dumped, "total": len(dumped), "limit": len(dumped), "offset": 0}


@router.post("/capacities/{resource_id}/definitions", status_code=201)
async def create_definition(resource_id: uuid.UUID, body: DefinitionInput, request: Request,
                            session: SessionDep, actor: ActorDep) -> dict:
    resource = await _load_resource(session, resource_id)
    _require_org(actor, resource.org_id)
    payload = body.model_dump(mode="json")

    async def build() -> dict:
        definition = _definition_orm(body)
        definition.resource_id = resource.id
        session.add(definition)
        await session.flush()
        await record_audit(session, request, action="capacity.definition_created",
                           entity_type="capacity_definition", entity_id=definition.id,
                           after=payload, actor_org_id=resource.org_id)
        return _dump(CapacityDefinitionOut, definition)

    return await idempotent_write(session, request,
                                  route=f"POST /capacities/{resource_id}/definitions",
                                  actor_id=actor.user_id, payload=payload,
                                  status_code=201, build=build)


@router.patch("/definitions/{definition_id}")
async def patch_definition(definition_id: uuid.UUID, body: DefinitionPatch, request: Request,
                           session: SessionDep, actor: ActorDep) -> dict:
    definition, resource = await _load_definition(session, definition_id)
    _require_org(actor, resource.org_id)
    changes = body.model_dump(exclude_unset=True, mode="json")
    min_q = changes.get("min_quantity", definition.min_quantity)
    max_q = changes.get("max_quantity", definition.max_quantity)
    if max_q < min_q:
        raise ValidationFailed("max_quantity must be >= min_quantity")
    before = {k: getattr(definition, k) for k in changes}
    for field, value in changes.items():
        setattr(definition, field, value)
    await session.flush()
    await record_audit(session, request, action="capacity.definition_updated",
                       entity_type="capacity_definition", entity_id=definition.id,
                       before=_jsonable(before), after=changes, actor_org_id=resource.org_id)
    return _dump(CapacityDefinitionOut, definition)


@router.delete("/definitions/{definition_id}", status_code=204)
async def delete_definition(definition_id: uuid.UUID, request: Request, session: SessionDep,
                            actor: ActorDep) -> None:
    definition, resource = await _load_definition(session, definition_id)
    _require_org(actor, resource.org_id)
    offer_count = (await session.execute(
        select(func.count()).select_from(Offer).where(Offer.definition_id == definition.id)
    )).scalar_one()
    if offer_count:
        raise Conflict(f"Definition has {offer_count} offer(s); close them before deleting")
    await session.delete(definition)
    await session.flush()
    await record_audit(session, request, action="capacity.definition_deleted",
                       entity_type="capacity_definition", entity_id=definition_id,
                       actor_org_id=resource.org_id)
    return None


# -------------------------------------------------------------------- availability


@router.get("/capacities/{resource_id}/availability")
async def get_availability(resource_id: uuid.UUID, session: SessionDep, actor: ActorDep,
                           date_from: Annotated[str, Query(alias="from")],
                           date_to: Annotated[str, Query(alias="to")],
                           definition_id: uuid.UUID | None = None) -> dict:
    """Day-by-day plan for a definition: planned, booked and free units (§5.2)."""
    resource = await _load_resource(session, resource_id)
    _require_org(actor, resource.org_id)
    definition = await _default_definition(resource, definition_id)
    start, end = parse_window(date_from, date_to, max_days=MAX_PLAN_DAYS)
    tz = cap.tz_of(resource.timezone)
    from_day = start.astimezone(tz).date()
    to_day = (end - timedelta(microseconds=1)).astimezone(tz).date()
    plans = await cap.build_day_plan(session, definition, resource, from_day, to_day)

    days = []
    for plan in plans:
        day_start, day_end = cap.day_range(plan.day, tz)
        booked = await cap.committed_quantity(session, definition.id, day_start, day_end)
        total = plan.total_quantity
        days.append(_dump(AvailabilityDay, AttrRow(
            date=plan.day, total_quantity=total, booked_quantity=booked,
            free_quantity=max(0, total - booked), closed=plan.closed)))

    rules = list((await session.execute(
        select(RecurringAvailability)
        .where(RecurringAvailability.definition_id == definition.id)
        .order_by(RecurringAvailability.dow, RecurringAvailability.start_time)
    )).scalars().all())
    overrides = list((await session.execute(
        select(AvailabilityOverride)
        .where(AvailabilityOverride.definition_id == definition.id,
               AvailabilityOverride.is_active.is_(True),
               AvailabilityOverride.override_date >= from_day,
               AvailabilityOverride.override_date <= to_day)
        .order_by(AvailabilityOverride.override_date)
    )).scalars().all())

    return {"definition_id": str(definition.id), "timezone": resource.timezone,
            "days": days,
            "recurring": [_dump(RecurringAvailabilityOut, r) for r in rules],
            "overrides": [_dump(AvailabilityOverrideOut, o) for o in overrides]}


@router.post("/capacities/{resource_id}/availability", status_code=201)
async def create_availability(resource_id: uuid.UUID, body: RecurringAvailabilityInput,
                              request: Request, session: SessionDep, actor: ActorDep) -> dict:
    resource = await _load_resource(session, resource_id)
    _require_org(actor, resource.org_id)
    definition = await _default_definition(resource, body.definition_id)
    payload = body.model_dump(mode="json")

    async def build() -> dict:
        clash = (await session.execute(
            select(RecurringAvailability).where(
                RecurringAvailability.definition_id == definition.id,
                RecurringAvailability.dow == body.dow,
                RecurringAvailability.start_time == body.start_time,
            )
        )).scalar_one_or_none()
        if clash is not None:
            raise Conflict("A rule for that weekday and start time already exists")
        rule = RecurringAvailability(
            definition_id=definition.id, dow=body.dow, start_time=body.start_time,
            end_time=body.end_time, quantity=body.quantity,
            valid_from=body.valid_from, valid_until=body.valid_until)
        session.add(rule)
        await session.flush()
        await record_audit(session, request, action="capacity.availability_created",
                           entity_type="recurring_availability", entity_id=rule.id,
                           after=payload, actor_org_id=resource.org_id)
        return _dump(RecurringAvailabilityOut, rule)

    return await idempotent_write(session, request,
                                  route=f"POST /capacities/{resource_id}/availability",
                                  actor_id=actor.user_id, payload=payload,
                                  status_code=201, build=build)


async def _load_availability_item(session, item_id: uuid.UUID):
    """`/availability/{id}` addresses a recurring rule or a dated override (§8)."""
    rule = (await session.execute(
        select(RecurringAvailability).where(RecurringAvailability.id == item_id)
    )).scalar_one_or_none()
    if rule is not None:
        definition, resource = await _load_definition(session, rule.definition_id)
        return rule, definition, resource, "recurring"
    override = (await session.execute(
        select(AvailabilityOverride).where(AvailabilityOverride.id == item_id)
    )).scalar_one_or_none()
    if override is not None:
        definition, resource = await _load_definition(session, override.definition_id)
        return override, definition, resource, "override"
    raise NotFound("Availability item not found")


@router.patch("/availability/{item_id}")
async def patch_availability(item_id: uuid.UUID, body: AvailabilityItemPatch, request: Request,
                             session: SessionDep, actor: ActorDep) -> dict:
    row, definition, resource, kind = await _load_availability_item(session, item_id)
    _require_org(actor, resource.org_id)
    changes = body.model_dump(exclude_unset=True, mode="json")
    if kind == "override":
        allowed = {"quantity", "is_active", "start_time", "end_time", "reason"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValidationFailed(f"Override has no field(s): {', '.join(sorted(unknown))}")
        if changes.get("quantity") is not None and changes["quantity"] < 0:
            raise ValidationFailed("override quantity must be >= 0")
        model = AvailabilityOverrideOut
    else:
        allowed = {"quantity", "is_active", "start_time", "end_time", "dow",
                   "valid_from", "valid_until"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValidationFailed(f"Availability rule has no field(s): {', '.join(sorted(unknown))}")
        if changes.get("quantity", row.quantity) < 1:
            raise ValidationFailed("quantity must be at least 1")
        model = RecurringAvailabilityOut

    start = changes.get("start_time", row.start_time)
    end = changes.get("end_time", row.end_time)
    if start is not None and end is not None and end <= start:
        raise ValidationFailed("end_time must be after start_time")

    before = {k: getattr(row, k) for k in changes if hasattr(row, k)}
    for field, value in changes.items():
        setattr(row, field, value)
    await session.flush()
    await record_audit(session, request,
                       action=f"capacity.{kind}_updated",
                       entity_type="availability_override" if kind == "override"
                       else "recurring_availability",
                       entity_id=row.id, before=_jsonable(before), after=changes,
                       actor_org_id=resource.org_id)
    return _dump(model, row)


@router.delete("/availability/{item_id}", status_code=204)
async def delete_availability(item_id: uuid.UUID, request: Request, session: SessionDep,
                              actor: ActorDep) -> None:
    row, definition, resource, kind = await _load_availability_item(session, item_id)
    _require_org(actor, resource.org_id)
    await session.delete(row)
    await session.flush()
    await record_audit(session, request, action=f"capacity.{kind}_deleted",
                       entity_type="availability_override" if kind == "override"
                       else "recurring_availability",
                       entity_id=item_id, actor_org_id=resource.org_id)
    return None


# --------------------------------------------------------------------- free windows


@router.get("/availability/free")
async def free_windows(session: SessionDep, actor: ActorDep, definition_id: uuid.UUID,
                       date_from: Annotated[str, Query(alias="from")],
                       date_to: Annotated[str, Query(alias="to")]) -> dict:
    """Concrete bookable windows with remaining quantity — the slot picker's source (§5.2)."""
    definition, resource = await _load_definition(session, definition_id)
    start, end = parse_window(date_from, date_to, max_days=MAX_FREE_QUERY_DAYS)
    windows = await cap.expand_free_windows(session, definition, resource, start, end)
    items = [_dump(FreeWindow, AttrRow(window_start=w.start, window_end=w.end,
                                    free_quantity=w.quantity)) for w in windows]
    return {"items": items, "total": len(items), "limit": len(items), "offset": 0}


@router.post("/capacities/{resource_id}/availability/overrides", status_code=201)
async def create_override(resource_id: uuid.UUID, body: OverrideInput, request: Request,
                          session: SessionDep, actor: ActorDep) -> dict:
    resource = await _load_resource(session, resource_id)
    _require_org(actor, resource.org_id)
    definition = await _default_definition(resource, body.definition_id)
    if body.kind in ("extra", "reduced") and not body.quantity:
        raise ValidationFailed(f"`quantity` is required for a {body.kind} override")
    payload = body.model_dump(mode="json")

    async def build() -> dict:
        slot = AvailabilityOverride.start_time.is_(None) if body.start_time is None \
            else AvailabilityOverride.start_time == body.start_time
        clash = (await session.execute(
            select(AvailabilityOverride).where(
                AvailabilityOverride.definition_id == definition.id,
                AvailabilityOverride.override_date == body.override_date, slot)
        )).scalar_one_or_none()
        if clash is not None:
            raise Conflict("An override already exists for that date and time slot")
        row = AvailabilityOverride(
            definition_id=definition.id, override_date=body.override_date, kind=body.kind,
            start_time=body.start_time, end_time=body.end_time, quantity=body.quantity,
            reason=body.reason)
        session.add(row)
        await session.flush()
        await record_audit(session, request, action="capacity.override_created",
                           entity_type="availability_override", entity_id=row.id,
                           after=payload, actor_org_id=resource.org_id)
        return _dump(AvailabilityOverrideOut, row)

    return await idempotent_write(session, request,
                                  route=f"POST /capacities/{resource_id}/availability/overrides",
                                  actor_id=actor.user_id, payload=payload,
                                  status_code=201, build=build)
