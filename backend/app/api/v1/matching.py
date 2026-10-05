"""`/api/v1/demands` and `/api/v1/matches` — reverse marketplace (CONTRACTS §5.3/§8).

A customer posts what they need, the scorer proposes the offers that can actually serve it,
and the provider answers by accepting (a draft booking the customer confirms) or declining.
"""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.api.v1.deps import ActorDep, PageDep, envelope
from app.core.deps import SessionDep
from app.core.errors import NotFound, OwnershipRequired, ValidationFailed
from app.models.marketplace import DEMAND_STATUSES, MATCH_STATUSES, Demand, Match, Offer
from app.schemas.marketplace import DemandInput, DemandOut, DemandPatch, MatchOut, OfferOut
from app.services import marketplace as market
from app.services import matching as svc

router = APIRouter(tags=["matching"])


async def _demand_payload(session, demand: Demand) -> dict:
    label, count = await svc.demand_read_model(session, demand)
    data = DemandOut.model_validate(demand).model_dump(mode="json")
    data["category_label"] = label
    data["match_count"] = count
    return data


async def _match_payload(session, match: Match, *, with_demand: bool = False) -> dict:
    data = MatchOut.model_validate(match).model_dump(mode="json")
    row = await market.get_offer_row(session, match.offer_id)
    data["offer"] = OfferOut.model_validate(market.row_to_dict(row)).model_dump(mode="json")
    if with_demand:
        demand = await session.get(Demand, match.demand_id)
        data["demand"] = (DemandOut.model_validate(demand).model_dump(mode="json")
                          if demand else None)
    booking_id = await svc.booking_id_for_match(session, match.id)
    data["booking_id"] = str(booking_id) if booking_id else None
    return data


def _check_status(value: str | None, allowed: tuple[str, ...], label: str) -> None:
    if value is not None and value not in allowed:
        raise ValidationFailed(f"Unknown {label}", details={"allowed": list(allowed)})


# --------------------------------------------------------------------------- demands


@router.get("/demands")
async def list_demands(session: SessionDep, actor: ActorDep, page: PageDep,
                       provider: bool = False, mine: bool = False,
                       status: Annotated[str | None, Query(max_length=16)] = None) -> dict:
    """`provider=true` is the matching inbox for the caller's organization(s)."""
    _check_status(status, DEMAND_STATUSES, "demand status")
    items, total = await svc.list_demands(
        session, user_id=actor.user_id, org_ids=actor.org_ids,
        is_platform_admin=actor.is_platform_admin, provider=provider and not mine,
        status=status, limit=page.limit, offset=page.offset)
    return envelope([await _demand_payload(session, d) for d in items], int(total), page)


@router.post("/demands", status_code=201)
async def create_demand(body: DemandInput, request: Request, session: SessionDep,
                        actor: ActorDep) -> dict:
    demand = await svc.create_demand(
        session, customer_id=actor.user_id, values=body.model_dump(), request=request)
    return await _demand_payload(session, demand)


@router.get("/demands/{demand_id}")
async def get_demand(demand_id: uuid.UUID, session: SessionDep, actor: ActorDep) -> dict:
    return await _demand_payload(session, await _demand(session, actor, demand_id))


@router.patch("/demands/{demand_id}")
async def patch_demand(demand_id: uuid.UUID, body: DemandPatch, request: Request,
                       session: SessionDep, actor: ActorDep) -> dict:
    demand = await _demand(session, actor, demand_id)
    _require_customer(actor, demand)
    changes = body.model_dump(exclude_unset=True)
    await svc.update_demand(session, demand, changes, request=request)
    return await _demand_payload(session, demand)


@router.post("/demands/{demand_id}/close")
async def close_demand(demand_id: uuid.UUID, request: Request, session: SessionDep,
                       actor: ActorDep) -> dict:
    demand = await _demand(session, actor, demand_id)
    _require_customer(actor, demand)
    await svc.set_status(session, demand, "closed", actor_user_id=actor.user_id,
                         request=request)
    return await _demand_payload(session, demand)


@router.post("/demands/{demand_id}/cancel")
async def cancel_demand(demand_id: uuid.UUID, request: Request, session: SessionDep,
                        actor: ActorDep) -> dict:
    demand = await _demand(session, actor, demand_id)
    _require_customer(actor, demand)
    await svc.set_status(session, demand, "cancelled", actor_user_id=actor.user_id,
                         request=request)
    return await _demand_payload(session, demand)


@router.get("/demands/{demand_id}/matches")
async def demand_matches(demand_id: uuid.UUID, session: SessionDep, actor: ActorDep,
                         page: PageDep) -> dict:
    """Live re-scoring of the demand against offers the capacity engine would accept."""
    demand = await _demand(session, actor, demand_id)
    matches = await svc.refresh_matches(session, demand)
    items = [await _match_payload(session, m) for m in matches]
    return envelope(items[page.offset:page.offset + page.limit], len(items), page)


async def _demand(session, actor, demand_id: uuid.UUID) -> Demand:
    return await svc.get_for_actor(
        session, demand_id, user_id=actor.user_id, org_ids=actor.org_ids,
        is_platform_admin=actor.is_platform_admin)


def _require_customer(actor, demand: Demand) -> None:
    if demand.customer_id != actor.user_id and not actor.is_platform_admin:
        raise OwnershipRequired("Only the customer who posted the demand can change it")


# --------------------------------------------------------------------------- matches


@router.get("/matches")
async def list_matches(session: SessionDep, actor: ActorDep, page: PageDep,
                       provider: bool = False, demand_id: uuid.UUID | None = None,
                       status: Annotated[str | None, Query(max_length=16)] = None) -> dict:
    _check_status(status, MATCH_STATUSES, "match status")
    items, total = await svc.list_matches_for_actor(
        session, user_id=actor.user_id, org_ids=actor.org_ids,
        is_platform_admin=actor.is_platform_admin, provider=provider,
        demand_id=demand_id, status=status, limit=page.limit, offset=page.offset)
    return envelope([await _match_payload(session, m, with_demand=provider)
                     for m in items], int(total), page)


@router.get("/matches/{match_id}")
async def get_match(match_id: uuid.UUID, session: SessionDep, actor: ActorDep) -> dict:
    match = await svc.get_match_for_actor(
        session, match_id, user_id=actor.user_id, org_ids=actor.org_ids,
        is_platform_admin=actor.is_platform_admin)
    return await _match_payload(session, match, with_demand=True)


@router.post("/matches/{match_id}/accept")
async def accept_match(match_id: uuid.UUID, request: Request, session: SessionDep,
                       actor: ActorDep) -> dict:
    """Provider accepts the demand: creates the draft booking the customer confirms (§5.5)."""
    match, org_id = await _match_for_provider(session, actor, match_id)
    await svc.accept_match(session, match, actor_user_id=actor.user_id, org_id=org_id,
                           request=request)
    return await _match_payload(session, match)


@router.post("/matches/{match_id}/reject")
async def reject_match(match_id: uuid.UUID, request: Request, session: SessionDep,
                       actor: ActorDep) -> dict:
    match, org_id = await _match_for_provider(session, actor, match_id)
    await svc.reject_match(session, match, actor_user_id=actor.user_id, org_id=org_id,
                           request=request)
    return await _match_payload(session, match)


async def _match_for_provider(session, actor, match_id: uuid.UUID) -> tuple[Match, uuid.UUID]:
    """Resolve the match and prove the caller runs the offering organization."""
    match = await svc.get_match_for_actor(
        session, match_id, user_id=actor.user_id, org_ids=actor.org_ids,
        is_platform_admin=actor.is_platform_admin)
    offer = await session.get(Offer, match.offer_id)
    if offer is None:
        raise NotFound("Offer not found for match")
    if not actor.can_manage_org(offer.org_id):
        raise OwnershipRequired("Only the providing organization can act on this match")
    return match, offer.org_id
