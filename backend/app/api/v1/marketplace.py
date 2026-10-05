"""`/api/v1/offers` — marketplace listings and search (CONTRACTS §5.3/§8)."""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.api.v1.deps import ActorDep, AttrRow, PageDep, envelope, idempotent_write, parse_bound
from app.core.deps import SessionDep
from app.core.errors import (
    NotFound,
    OwnershipRequired,
    RangeMismatch,
    Unprocessable,
    ValidationFailed,
)
from app.models.capacity import CapacityDefinition, CapacityResource
from app.models.marketplace import Offer
from app.schemas.capacity import FreeWindow
from app.schemas.marketplace import OfferInput, OfferOut, OfferPatch, OfferSort
from app.services import marketplace as svc

OFFER_STATUSES = ("draft", "published", "paused", "closed")

router = APIRouter(prefix="/offers", tags=["marketplace"])


def _dump(payload) -> dict:
    return OfferOut.model_validate(payload).model_dump(mode="json")


def _bounds(date_from: str | None, date_to: str | None):
    start, end = (parse_bound(date_from) if date_from else None,
                  parse_bound(date_to, end=True) if date_to else None)
    if (start is None) != (end is None):
        raise ValidationFailed("`from` and `to` must be provided together")
    if start and end and end <= start:
        raise RangeMismatch("`to` must be after `from`")
    return start, end


def _bbox(value: str | None) -> tuple[float, float, float, float] | None:
    """`bbox=min_lon,min_lat,max_lon,max_lat` (§8)."""
    if not value:
        return None
    parts = [p.strip() for p in value.split(",")]
    if len(parts) != 4:
        raise ValidationFailed("bbox needs min_lon,min_lat,max_lon,max_lat")
    try:
        min_lon, min_lat, max_lon, max_lat = (float(p) for p in parts)
    except ValueError as exc:
        raise ValidationFailed("bbox values must be numbers") from exc
    if max_lon <= min_lon or max_lat <= min_lat:
        raise RangeMismatch("bbox max corners must be greater than the min corners")
    return min_lon, min_lat, max_lon, max_lat


async def _definition_for_org(session, actor, definition_id: uuid.UUID):
    definition = await session.get(CapacityDefinition, definition_id)
    if definition is None:
        raise NotFound("Capacity definition not found")
    resource = await session.get(CapacityResource, definition.resource_id)
    if not actor.can_manage_org(resource.org_id):
        raise OwnershipRequired("Only the owning organization can list this capacity")
    return definition, resource


@router.get("")
async def search_offers(session: SessionDep, actor: ActorDep, page: PageDep,
                        q: Annotated[str | None, Query(max_length=200)] = None,
                        category_id: uuid.UUID | None = None,
                        city: Annotated[str | None, Query(max_length=120)] = None,
                        country: Annotated[str | None, Query(max_length=2)] = None,
                        booking_mode: Annotated[str | None, Query()] = None,
                        sort: Annotated[OfferSort, Query()] = "relevance",
                        bbox: str | None = None,
                        date_from: Annotated[str | None, Query(alias="from")] = None,
                        date_to: Annotated[str | None, Query(alias="to")] = None,
                        min_quantity: Annotated[int | None, Query(ge=1)] = None,
                        max_unit_cents: Annotated[int | None, Query(ge=0)] = None,
                        min_rating: Annotated[float | None, Query(ge=0, le=5)] = None,
                        status: Annotated[str | None, Query()] = None,
                        mine: bool = False) -> dict:
    """Public search returns published offers; `mine=true` is the provider's own list (§8)."""
    if mine:
        if not actor.org_ids:
            return envelope([], 0, page)
        org_ids = actor.org_ids
        statuses = (status,) if status in OFFER_STATUSES else ("draft", "published", "paused")
    elif actor.is_platform_admin and status in OFFER_STATUSES:
        org_ids, statuses = None, (status,)
    else:
        org_ids, statuses = None, ("published",)
    if not mine and status == "closed":
        raise ValidationFailed("Closed listings are only visible to their owner")

    start, end = _bounds(date_from, date_to)
    params = svc.SearchParams(
        q=q, category_id=category_id, city=city, country=country, bbox=_bbox(bbox),
        start=start, end=end, min_quantity=min_quantity, max_unit_cents=max_unit_cents,
        min_rating=min_rating, booking_mode=booking_mode, sort=sort,
        limit=page.limit, offset=page.offset, org_ids=org_ids, statuses=statuses)
    items, total = await svc.search(session, params)
    return envelope([_dump(i) for i in items], total, page)


@router.post("", status_code=201)
async def create_offer(body: OfferInput, request: Request, session: SessionDep,
                       actor: ActorDep) -> dict:
    definition, resource = await _definition_for_org(session, actor, body.definition_id)
    if resource.status == "archived":
        raise Unprocessable("Archived capacity cannot be listed")
    payload = body.model_dump(mode="json")

    async def build() -> dict:
        values = {k: v for k, v in payload.items() if k != "definition_id"}
        if body.cancellation_policy is None:
            values.pop("cancellation_policy", None)  # let the §5.3 server default apply
        offer = await svc.create_offer(session, resource=resource, definition=definition,
                                       values=values, request=request)
        return _dump(svc.row_to_dict(await svc.get_offer_row(session, offer.id)))

    return await idempotent_write(session, request, route="POST /offers",
                                  actor_id=actor.user_id, payload=payload,
                                  status_code=201, build=build)


@router.get("/{offer_id}")
async def get_offer(offer_id: uuid.UUID, session: SessionDep, actor: ActorDep) -> dict:
    row = await svc.get_offer_row(session, offer_id)
    offer: Offer = row.Offer
    if offer.status != "published" and not actor.can_manage_org(offer.org_id):
        raise OwnershipRequired("This listing is not published")
    return _dump(svc.row_to_dict(row))


async def _manage(session, actor, offer_id: uuid.UUID) -> Offer:
    offer = await session.get(Offer, offer_id)
    if offer is None:
        raise NotFound("Offer not found")
    if not actor.can_manage_org(offer.org_id):
        raise OwnershipRequired("Only the owning organization can change this listing")
    return offer


@router.patch("/{offer_id}")
async def patch_offer(offer_id: uuid.UUID, body: OfferPatch, request: Request,
                      session: SessionDep, actor: ActorDep) -> dict:
    offer = await _manage(session, actor, offer_id)
    definition = await session.get(CapacityDefinition, offer.definition_id)
    changes = body.model_dump(exclude_unset=True, mode="json")
    await svc.update_offer(session, offer, changes, definition=definition, request=request)
    return _dump(svc.row_to_dict(await svc.get_offer_row(session, offer.id)))


async def _transition(request: Request, session: SessionDep, actor: ActorDep,
                      offer_id: uuid.UUID, to_status: str) -> dict:
    offer = await _manage(session, actor, offer_id)
    await svc.set_status(session, offer, to_status, request=request)
    return _dump(svc.row_to_dict(await svc.get_offer_row(session, offer.id)))


@router.post("/{offer_id}/publish")
async def publish_offer(offer_id: uuid.UUID, request: Request, session: SessionDep,
                        actor: ActorDep) -> dict:
    return await _transition(request, session, actor, offer_id, "published")


@router.post("/{offer_id}/pause")
async def pause_offer(offer_id: uuid.UUID, request: Request, session: SessionDep,
                      actor: ActorDep) -> dict:
    return await _transition(request, session, actor, offer_id, "paused")


@router.post("/{offer_id}/close")
async def close_offer(offer_id: uuid.UUID, request: Request, session: SessionDep,
                      actor: ActorDep) -> dict:
    return await _transition(request, session, actor, offer_id, "closed")


@router.get("/{offer_id}/availability")
async def offer_availability(offer_id: uuid.UUID, session: SessionDep, actor: ActorDep,
                             date_from: Annotated[str, Query(alias="from")],
                             date_to: Annotated[str, Query(alias="to")]) -> dict:
    """Free windows narrowed by the offer's quantity ceiling — the booking picker's view."""
    row = await svc.get_offer_row(session, offer_id)
    offer: Offer = row.Offer
    if offer.status != "published" and not actor.can_manage_org(offer.org_id):
        raise OwnershipRequired("This listing is not published")
    start, end = _bounds(date_from, date_to)
    if start is None:
        raise ValidationFailed("`from` and `to` are required")
    definition = await session.get(CapacityDefinition, offer.definition_id)
    resource = await session.get(CapacityResource, offer.resource_id)
    windows = await svc.free_windows_for_offer(session, offer, definition, resource, start, end)
    items = [FreeWindow.model_validate(
        AttrRow(window_start=w.start, window_end=w.end, free_quantity=w.quantity)).model_dump(
        mode="json") for w in windows]
    return {"items": items, "total": len(items), "limit": len(items), "offset": 0}
