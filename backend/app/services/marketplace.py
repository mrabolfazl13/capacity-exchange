"""Offer lifecycle + marketplace search (CONTRACTS §5.3).

Search keeps every decision Postgres can make in SQL (full text, category, place, price,
rating, booking mode); only time-window feasibility runs per candidate because it needs the
availability expansion and committed quantities of §5.2/§5.5.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

from fastapi import Request
from sqlalchemy import Select, bindparam, desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.errors import (
    Conflict,
    NotFound,
    RangeMismatch,
    Unprocessable,
    ValidationFailed,
)
from app.models.capacity import (
    CapacityCategory,
    CapacityDefinition,
    CapacityResource,
    RecurringAvailability,
)
from app.models.crosscut import Review
from app.models.identity import Organization
from app.models.marketplace import OFFER_STATUSES, Offer
from app.services import capacity as cap

# Time feasibility is evaluated in Python, so the candidate set that reaches it is bounded.
CANDIDATE_CAP = 200

PUBLISHED_REVIEW = Review.status == "published"


@dataclass
class SearchParams:
    q: str | None = None
    category_id: uuid.UUID | None = None
    city: str | None = None
    country: str | None = None
    bbox: tuple[float, float, float, float] | None = None  # min_lon, min_lat, max_lon, max_lat
    start: datetime | None = None
    end: datetime | None = None
    min_quantity: int | None = None
    max_unit_cents: int | None = None
    min_rating: float | None = None
    booking_mode: str | None = None
    sort: str = "relevance"
    limit: int = 20
    offset: int = 0
    org_ids: set[uuid.UUID] | None = None  # provider workspace scope
    statuses: Sequence[str] = ("published",)


def _rating_avg():
    return (
        select(func.avg(Review.rating))
        .where(Review.offer_id == Offer.id, PUBLISHED_REVIEW)
        .correlate(Offer)
        .scalar_subquery()
    )


def _rating_count():
    return (
        select(func.count(Review.id))
        .where(Review.offer_id == Offer.id, PUBLISHED_REVIEW)
        .correlate(Offer)
        .scalar_subquery()
    )


def _base_select() -> Select:
    """One row per offer plus the marketplace read-model columns (§8 `GET /offers`)."""
    return (
        select(
            Offer,
            CapacityResource.name.label("resource_name"),
            CapacityResource.lat.label("lat"),
            CapacityResource.lon.label("lon"),
            CapacityResource.address["city"].astext.label("city"),
            CapacityResource.address["country"].astext.label("country"),
            CapacityCategory.id.label("category_id"),
            CapacityCategory.label.label("category_label"),
            CapacityDefinition.name.label("definition_name"),
            CapacityDefinition.unit_label.label("unit_label"),
            CapacityDefinition.max_quantity.label("max_quantity_definition"),
            Organization.name.label("org_name"),
            _rating_avg().label("rating_avg"),
            _rating_count().label("rating_count"),
        )
        .join(CapacityResource, CapacityResource.id == Offer.resource_id)
        .join(CapacityCategory, CapacityCategory.id == CapacityResource.category_id)
        .join(CapacityDefinition, CapacityDefinition.id == Offer.definition_id)
        .join(Organization, Organization.id == Offer.org_id)
    )


def _apply_filters(stmt: Select, p: SearchParams) -> Select:
    statuses = tuple(p.statuses) or ("draft", "published", "paused")
    stmt = stmt.where(Offer.status.in_(statuses))
    if p.org_ids is not None:
        stmt = stmt.where(Offer.org_id.in_(p.org_ids))
    if p.category_id is not None:
        stmt = stmt.where(CapacityCategory.id == p.category_id)
    if p.city:
        stmt = stmt.where(func.lower(CapacityResource.address["city"].astext) == p.city.lower())
    if p.country:
        stmt = stmt.where(
            func.upper(CapacityResource.address["country"].astext) == p.country.upper())
    if p.bbox:
        min_lon, min_lat, max_lon, max_lat = p.bbox
        stmt = stmt.where(CapacityResource.lat.is_not(None),
                          CapacityResource.lon.is_not(None),
                          CapacityResource.lon.between(min_lon, max_lon),
                          CapacityResource.lat.between(min_lat, max_lat))
    if p.max_unit_cents is not None:
        stmt = stmt.where(Offer.unit_amount_cents <= p.max_unit_cents)
    if p.min_quantity is not None:
        stmt = stmt.where((Offer.max_quantity.is_(None)) | (Offer.max_quantity >= p.min_quantity))
    if p.booking_mode:
        stmt = stmt.where(Offer.booking_mode == p.booking_mode)
    if p.min_rating is not None:
        stmt = stmt.where(func.coalesce(_rating_avg(), 0.0) >= p.min_rating)
    if p.q:
        # Core FTS only — pg_trgm/unaccent are not in pgserver (§11). A: title, B: description.
        stmt = stmt.where(_ts_match(p.q))
    return stmt


def _ts_match(query: str):
    """Bind the FTS term into the raw `search_vector` predicate — 2.0 Selects have no bindparams()."""
    return text("offers.search_vector @@ plainto_tsquery('english', :q)").bindparams(
        bindparam("q", value=query))


def _apply_sort(stmt: Select, p: SearchParams) -> Select:
    if p.sort == "price_asc":
        return stmt.order_by(Offer.unit_amount_cents.asc(), Offer.id)
    if p.sort == "price_desc":
        return stmt.order_by(Offer.unit_amount_cents.desc(), Offer.id)
    if p.sort == "newest":
        return stmt.order_by(func.coalesce(Offer.published_at, Offer.created_at).desc(), Offer.id)
    if p.sort == "rating":
        return stmt.order_by(func.coalesce(_rating_avg(), 0.0).desc(), Offer.id)
    if p.q and p.sort == "relevance":
        return stmt.order_by(desc(_ts_rank(p.q)), Offer.created_at.desc(), Offer.id)
    return stmt.order_by(Offer.created_at.desc(), Offer.id)


def _ts_rank(query: str):
    return text("ts_rank(offers.search_vector, plainto_tsquery('english', :q))").bindparams(
        bindparam("q", value=query))


def row_to_dict(row: Any) -> dict:
    offer: Offer = row.Offer
    return {
        "id": offer.id, "definition_id": offer.definition_id, "org_id": offer.org_id,
        "resource_id": offer.resource_id, "title": offer.title, "description": offer.description,
        "pricing_mode": offer.pricing_mode, "unit_amount_cents": offer.unit_amount_cents,
        "currency": offer.currency, "min_lead_time_minutes": offer.min_lead_time_minutes,
        "max_lead_time_days": offer.max_lead_time_days,
        "min_duration_minutes": offer.min_duration_minutes,
        "max_duration_minutes": offer.max_duration_minutes,
        "min_quantity": offer.min_quantity, "max_quantity": offer.max_quantity,
        "booking_mode": offer.booking_mode, "hold_minutes": offer.hold_minutes,
        "cancellation_policy": offer.cancellation_policy, "status": offer.status,
        "published_at": offer.published_at, "created_at": offer.created_at,
        "updated_at": offer.updated_at, "category_id": row.category_id,
        "category_label": row.category_label, "org_name": row.org_name,
        "resource_name": row.resource_name, "definition_name": row.definition_name,
        "unit_label": row.unit_label, "max_quantity_definition": row.max_quantity_definition,
        "city": row.city, "lat": float(row.lat) if row.lat is not None else None,
        "lon": float(row.lon) if row.lon is not None else None,
        "rating_avg": round(float(row.rating_avg), 2) if row.rating_avg is not None else None,
        "rating_count": int(row.rating_count or 0),
    }


async def free_windows_for_offer(session: AsyncSession, offer: Offer,
                                 definition: CapacityDefinition, resource: CapacityResource,
                                 start: datetime, end: datetime) -> list[cap.Window]:
    """Definition windows narrowed by the offer's own quantity ceiling (§5.2/§5.3)."""
    windows = await cap.expand_free_windows(session, definition, resource, start, end)
    ceiling = min(q for q in (offer.max_quantity, definition.max_quantity) if q is not None)
    return [cap.Window(w.start, w.end, min(w.quantity, ceiling)) for w in windows]


async def _free_quantity(session: AsyncSession, offer: Offer,
                         start: datetime, end: datetime) -> int | None:
    """Largest single bookable window in the range, or None when the offer cannot serve it."""
    definition = await session.get(CapacityDefinition, offer.definition_id)
    resource = await session.get(CapacityResource, offer.resource_id)
    if definition is None or resource is None:
        return None
    windows = await free_windows_for_offer(session, offer, definition, resource, start, end)
    return max((w.quantity for w in windows), default=None)


async def search(session: AsyncSession, p: SearchParams) -> tuple[list[dict], int]:
    """One page of offers plus the total number matching the filters."""
    stmt = _apply_sort(_apply_filters(_base_select(), p), p)
    if p.start is not None and p.end is not None:
        candidates = list((await session.execute(stmt.limit(CANDIDATE_CAP))).all())
        feasible: list[dict] = []
        for row in candidates:
            item = row_to_dict(row)
            item["free_quantity"] = await _free_quantity(session, row.Offer, p.start, p.end)
            if item["free_quantity"] is not None:
                feasible.append(item)
        wanted = p.min_quantity or 1
        feasible = [f for f in feasible if f["free_quantity"] >= wanted]
        return feasible[p.offset:p.offset + p.limit], len(feasible)

    total = (await session.execute(
        select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()
    rows = list((await session.execute(stmt.limit(p.limit).offset(p.offset))).all())
    return [row_to_dict(r) for r in rows], int(total)


async def get_offer_row(session: AsyncSession, offer_id: uuid.UUID) -> Any:
    row = (await session.execute(_base_select().where(Offer.id == offer_id))).first()
    if row is None:
        raise NotFound("Offer not found")
    return row


def _validate_quantity(definition: CapacityDefinition, min_q: int | None,
                       max_q: int | None) -> None:
    """An offer may narrow the definition's capacity, never exceed it (§5.3)."""
    if max_q is not None and max_q > definition.max_quantity:
        raise Unprocessable(
            f"max_quantity cannot exceed the definition's {definition.max_quantity}")
    if min_q is not None and max_q is not None and min_q > max_q:
        raise RangeMismatch("max_quantity must be >= min_quantity")


async def create_offer(session: AsyncSession, *, resource: CapacityResource,
                       definition: CapacityDefinition, values: dict,
                       request: Request | None = None) -> Offer:
    _validate_quantity(definition, values.get("min_quantity"), values.get("max_quantity"))
    offer = Offer(definition_id=definition.id, org_id=resource.org_id,
                  resource_id=resource.id, **values)
    session.add(offer)
    await session.flush()
    await record_audit(session, request, action="offer.created", entity_type="offer",
                       entity_id=offer.id, after=values, actor_org_id=resource.org_id)
    return offer


async def update_offer(session: AsyncSession, offer: Offer, changes: dict, *,
                       definition: CapacityDefinition,
                       request: Request | None = None) -> Offer:
    merged = {"min_quantity": offer.min_quantity, "max_quantity": offer.max_quantity, **changes}
    _validate_quantity(definition, merged["min_quantity"], merged["max_quantity"])
    before = {k: getattr(offer, k) for k in changes}
    for field, value in changes.items():
        setattr(offer, field, value)
    await session.flush()
    await record_audit(session, request, action="offer.updated", entity_type="offer",
                       entity_id=offer.id, before=before, after=changes,
                       actor_org_id=offer.org_id)
    return offer


PUBLISH_TARGETS = {"draft": {"published"}, "published": {"paused", "closed"},
                  "paused": {"published", "closed"}, "closed": set()}


async def set_status(session: AsyncSession, offer: Offer, to_status: str, *,
                     request: Request | None = None) -> Offer:
    if to_status not in OFFER_STATUSES:
        raise ValidationFailed(f"Unknown offer status {to_status}")
    if to_status not in PUBLISH_TARGETS[offer.status]:
        raise Conflict(f"Cannot move an offer from {offer.status} to {to_status}")
    if to_status == "published" and not await _has_availability(session, offer):
        raise Unprocessable("Publishing needs at least one active availability rule")
    offer.status = to_status
    if to_status == "published" and offer.published_at is None:
        offer.published_at = datetime.now(timezone.utc)
    await session.flush()
    await record_audit(session, request, action=f"offer.{to_status}", entity_type="offer",
                       entity_id=offer.id, actor_org_id=offer.org_id)
    return offer


async def _has_availability(session: AsyncSession, offer: Offer) -> bool:
    count = (await session.execute(
        select(func.count()).select_from(RecurringAvailability).where(
            RecurringAvailability.definition_id == offer.definition_id,
            RecurringAvailability.is_active.is_(True)))).scalar_one()
    return count > 0
