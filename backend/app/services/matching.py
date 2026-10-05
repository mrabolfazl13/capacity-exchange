"""Demands + matching (CONTRACTS §5.3/§8): a deterministic, explainable scorer.

No model, no network: every point comes from data the platform already stores, and every
score ships with the reasons that produced it, so both sides can audit a suggestion. The
`ai/` surface may re-rank these later, but the shipped booking path never waits for it
(§12: no fake actions).

Scoring is 1000 points over five signals — capacity fit 300, price 200, distance 250,
rating 150, text relevance 100. The candidate set is whatever `marketplace.search` already
accepts for the demand's category, place, quantity, window and budget, so a match can never
promise a slot the capacity engine would refuse.
"""
from __future__ import annotations

import math
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import Request
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.errors import (
    Conflict,
    InvalidStateTransition,
    NotFound,
    OwnershipRequired,
    ValidationFailed,
)
from app.models.capacity import CapacityCategory
from app.models.booking import Booking
from app.models.marketplace import DEMAND_STATUSES, Demand, Match, Offer
from app.services import booking as booking_svc
from app.services import marketplace as market
from app.services.notifications import notify

MAX_MATCHES = 20
CANDIDATE_LIMIT = 50
NEARBY_KM = 50.0

W_AVAILABILITY, W_PRICE, W_DISTANCE, W_RATING, W_TEXT = 300.0, 200.0, 250.0, 150.0, 100.0

DEMAND_TRANSITIONS: dict[str, set[str]] = {
    "open": {"matched", "closed", "cancelled"},
    "matched": {"closed", "cancelled"},
    "closed": set(),
    "cancelled": set(),
}

_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "have", "will", "your", "our",
    "need", "needs", "want", "looking", "please", "about", "into", "when", "than", "them",
    "they", "there", "these", "some", "any", "days", "day",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _terms(value: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (value or "").lower())
            if len(t) > 3 and t not in _STOPWORDS}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    h = (math.sin(d_phi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2)
    return 2 * radius * math.asin(math.sqrt(h))


def _address_value(demand: Demand, key: str) -> str | None:
    value = (demand.address or {}).get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _availability(demand: Demand, row: dict, reasons: list[str]) -> float:
    needed = max(1, demand.quantity)
    if row["free_quantity"] is not None:
        free = row["free_quantity"]
    else:
        ceilings = [q for q in (row["max_quantity"], row["max_quantity_definition"]) if q]
        free = min(ceilings) if ceilings else 0
    reasons.append(f"{free} unit{'s' if free != 1 else ''} available, {needed} needed")
    return W_AVAILABILITY * min(1.0, free / needed)


def _price(demand: Demand, row: dict, reasons: list[str]) -> float:
    unit = row["unit_amount_cents"]
    low, high = demand.budget_min_cents, demand.budget_max_cents
    if high is None:
        reasons.append("no budget ceiling given")
        return W_PRICE * 0.7
    if unit <= 0:
        reasons.append("listed free of charge")
        return W_PRICE
    if low is not None and unit <= low:
        reasons.append(f"{unit} cents per unit, at or under your budget floor")
        return W_PRICE
    reference = low or 0
    span = max(1, high - reference)
    used = max(0, unit - reference)
    reasons.append(f"{unit} cents per unit inside your {high} ceiling")
    return W_PRICE * max(0.1, 1 - used / span)


def _distance(demand: Demand, row: dict, reasons: list[str]) -> float:
    if None not in (demand.lat, demand.lon, row["lat"], row["lon"]):
        km = haversine_km(float(demand.lat), float(demand.lon), row["lat"], row["lon"])
        reasons.append(f"{round(km, 1)} km from the requested place")
        return W_DISTANCE * max(0.0, 1 - km / NEARBY_KM)
    wanted_city = _address_value(demand, "city")
    offer_city = row["city"]
    if wanted_city and offer_city:
        same = wanted_city.lower() == offer_city.lower()
        reasons.append(f"in {offer_city}" if same else f"{offer_city}, outside your city")
        return W_DISTANCE * (0.7 if same else 0.3)
    reasons.append("location cannot be compared")
    return W_DISTANCE * 0.4


def _rating(row: dict, reasons: list[str]) -> float:
    count = int(row["rating_count"] or 0)
    if count == 0 or row["rating_avg"] is None:
        reasons.append("no published reviews yet")
        return W_RATING * 0.4
    reasons.append(f"rated {row['rating_avg']}/5 by {count} customer" + ("s" if count != 1 else ""))
    return W_RATING * min(1.0, row["rating_avg"] / 5.0)


def _text(demand: Demand, row: dict, reasons: list[str]) -> float:
    wanted = _terms(demand.description)
    if not wanted:
        reasons.append("demand has no comparable keywords")
        return W_TEXT * 0.3
    hits = wanted & _terms(f"{row['title']} {row['description']}")
    if not hits:
        reasons.append("no keyword overlap with your description")
        return 0.0
    reasons.append(f"listing text matches {len(hits)} of your keywords")
    return W_TEXT * min(1.0, len(hits) / 4)


def score_candidate(demand: Demand, row: dict) -> tuple[float, list[str]]:
    """(score, reasons) for one offer against one demand — the whole rubric in one place."""
    reasons: list[str] = []
    if demand.category_id is not None and row["category_id"] == demand.category_id:
        reasons.append(f"exact category: {row['category_label']}")
    parts = (_availability(demand, row, reasons) + _price(demand, row, reasons)
             + _distance(demand, row, reasons) + _rating(row, reasons)
             + _text(demand, row, reasons))
    return round(min(1000.0, max(0.0, parts)), 3), reasons


def _search_params(demand: Demand) -> market.SearchParams:
    return market.SearchParams(
        category_id=demand.category_id,
        city=_address_value(demand, "city"),
        country=_address_value(demand, "country"),
        start=demand.desired_start,
        end=demand.desired_end,
        min_quantity=demand.quantity,
        max_unit_cents=demand.budget_max_cents,
        limit=CANDIDATE_LIMIT,
        offset=0,
    )


async def list_matches(session: AsyncSession, demand_id: uuid.UUID) -> list[Match]:
    """The demand's proposal list: expired rows are history, not matches to act on."""
    return list((
        await session.execute(
            select(Match).where(Match.demand_id == demand_id, Match.status != "expired")
            .order_by(Match.score.desc(), Match.created_at, Match.id))
    ).scalars().all())


async def refresh_matches(session: AsyncSession, demand: Demand, *,
                          limit: int = MAX_MATCHES) -> list[Match]:
    """Re-score the live candidates and keep the provider decisions that were already made.

    Only `suggested`/`expired` rows are rewritten, so an accepted or declined match never
    comes back as a fresh proposal; a suggestion whose offer stopped qualifying expires.
    """
    if demand.status != "open":
        return await list_matches(session, demand.id)
    rows, _total = await market.search(session, _search_params(demand))
    scored = sorted(((score_candidate(demand, row), row) for row in rows),
                    key=lambda pair: pair[0][0], reverse=True)[:limit]

    keep: list[uuid.UUID] = []
    for (score, reasons), row in scored:
        keep.append(row["id"])
        await session.execute(
            pg_insert(Match).values(demand_id=demand.id, offer_id=row["id"], score=score,
                                    reasons=reasons, status="suggested")
            .on_conflict_do_update(
                index_elements=["demand_id", "offer_id"],
                set_={"score": score, "reasons": reasons, "status": "suggested"},
                where=Match.status.in_(("suggested", "expired")),
            ))
    if keep:
        await session.execute(
            update(Match)
            .where(Match.demand_id == demand.id, Match.status == "suggested",
                   Match.offer_id.not_in(keep))
            .values(status="expired", updated_at=utcnow()))
    else:
        await session.execute(
            update(Match).where(Match.demand_id == demand.id, Match.status == "suggested")
            .values(status="expired", updated_at=utcnow()))
    await session.flush()
    return await list_matches(session, demand.id)


# --------------------------------------------------------------------------- demand CRUD


async def create_demand(session: AsyncSession, *, customer_id: uuid.UUID, values: dict,
                        org_id: uuid.UUID | None = None,
                        request: Request | None = None) -> Demand:
    values = dict(values)
    start, end = values.get("desired_start"), values.get("desired_end")
    if (start is None) != (end is None):
        raise ValidationFailed("desired_start and desired_end must be provided together")
    if start and end and end <= start:
        raise ValidationFailed("desired_end must be after desired_start")
    demand = Demand(customer_id=customer_id, org_id=org_id, **values)
    session.add(demand)
    await session.flush()
    await record_audit(session, request, action="demand.created", entity_type="demand",
                       entity_id=demand.id, after={k: str(v) for k, v in values.items()})
    return demand


async def update_demand(session: AsyncSession, demand: Demand, changes: dict, *,
                        request: Request | None = None) -> Demand:
    changes = {k: v for k, v in changes.items() if v is not None}
    if not changes:
        return demand
    if demand.status != "open":
        raise InvalidStateTransition(f"A {demand.status} demand cannot be edited")
    merged = {**{k: getattr(demand, k) for k in changes}, **changes}
    start, end = merged.get("desired_start"), merged.get("desired_end")
    if (start is None) != (end is None):
        raise ValidationFailed("desired_start and desired_end must be provided together")
    if start and end and end <= start:
        raise ValidationFailed("desired_end must be after desired_start")
    before = {k: getattr(demand, k) for k in changes}
    for key, value in changes.items():
        setattr(demand, key, value)
    await session.flush()
    await record_audit(session, request, action="demand.updated", entity_type="demand",
                       entity_id=demand.id, before=before, after=changes)
    return demand


async def set_status(session: AsyncSession, demand: Demand, to_status: str, *,
                     actor_user_id: uuid.UUID, request: Request | None = None) -> Demand:
    if to_status not in DEMAND_STATUSES:
        raise ValidationFailed(f"Unknown demand status {to_status}")
    if to_status not in DEMAND_TRANSITIONS[demand.status]:
        raise Conflict(f"Cannot move a demand from {demand.status} to {to_status}")
    demand.status = to_status
    await session.flush()
    await record_audit(session, request, action=f"demand.{to_status}", entity_type="demand",
                       entity_id=demand.id, actor_org_id=demand.org_id,
                       after={"status": to_status, "actor_user_id": str(actor_user_id)})
    return demand


async def get_for_actor(session: AsyncSession, demand_id: uuid.UUID, *, user_id: uuid.UUID,
                        org_ids: set[uuid.UUID],
                        is_platform_admin: bool) -> Demand:
    demand = await session.get(Demand, demand_id)
    if demand is None:
        raise NotFound("Demand not found")
    if is_platform_admin or demand.customer_id == user_id:
        return demand
    if org_ids:
        offered = (await session.execute(
            select(Match.id)
            .join(Offer, Offer.id == Match.offer_id)
            .where(Match.demand_id == demand.id, Offer.org_id.in_(org_ids)).limit(1)
        )).scalar_one_or_none()
        if offered is not None:
            return demand
    raise OwnershipRequired("You do not have access to this demand")


async def list_demands(session: AsyncSession, *, user_id: uuid.UUID, org_ids: set[uuid.UUID],
                       is_platform_admin: bool, provider: bool = False,
                       status: str | None = None, limit: int = 20,
                       offset: int = 0) -> tuple[list[Demand], int]:
    """Customer view is the caller's own demands; provider view is the matching inbox."""
    stmt = select(Demand)
    if not is_platform_admin:
        if provider:
            stmt = (stmt.join(Match, Match.demand_id == Demand.id)
                    .join(Offer, Offer.id == Match.offer_id)
                    .where(Offer.org_id.in_(org_ids)).distinct())
        else:
            stmt = stmt.where(Demand.customer_id == user_id)
    if status:
        stmt = stmt.where(Demand.status == status)
    total = (await session.execute(
        select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()
    rows = (await session.execute(stmt.order_by(Demand.created_at.desc(), Demand.id)
                                  .limit(limit).offset(offset))).scalars().all()
    return list(rows), int(total)


async def demand_read_model(session: AsyncSession,
                            demand: Demand) -> tuple[str | None, int]:
    """(category_label, match_count) so the demand list needs no second request."""
    label = None
    if demand.category_id is not None:
        label = (await session.execute(
            select(CapacityCategory.label).where(CapacityCategory.id == demand.category_id)
        )).scalar_one_or_none()
    count = (await session.execute(
        select(func.count()).select_from(Match)
        .where(Match.demand_id == demand.id, Match.status.in_(("suggested", "accepted")))
    )).scalar_one()
    return label, int(count)


# --------------------------------------------------------------------------- match actions


async def get_match_for_actor(session: AsyncSession, match_id: uuid.UUID, *, user_id: uuid.UUID,
                              org_ids: set[uuid.UUID],
                              is_platform_admin: bool) -> Match:
    match = await session.get(Match, match_id)
    if match is None:
        raise NotFound("Match not found")
    await get_for_actor(session, match.demand_id, user_id=user_id, org_ids=org_ids,
                        is_platform_admin=is_platform_admin)
    return match


async def list_matches_for_actor(session: AsyncSession, *, user_id: uuid.UUID,
                                 org_ids: set[uuid.UUID], is_platform_admin: bool,
                                 provider: bool = False, demand_id: uuid.UUID | None = None,
                                 status: str | None = None, limit: int = 20,
                                 offset: int = 0) -> tuple[list[Match], int]:
    stmt = select(Match)
    if not is_platform_admin:
        if provider:
            stmt = stmt.join(Offer, Offer.id == Match.offer_id).where(Offer.org_id.in_(org_ids))
        else:
            stmt = stmt.join(Demand, Demand.id == Match.demand_id).where(
                Demand.customer_id == user_id)
    if demand_id is not None:
        stmt = stmt.where(Match.demand_id == demand_id)
    if status:
        stmt = stmt.where(Match.status == status)
    total = (await session.execute(
        select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()
    rows = (await session.execute(stmt.order_by(Match.score.desc(), Match.created_at, Match.id)
                                  .limit(limit).offset(offset))).scalars().all()
    return list(rows), int(total)


async def accept_match(session: AsyncSession, match: Match, *, actor_user_id: uuid.UUID,
                       org_id: uuid.UUID, request: Request | None = None) -> Booking:
    """Provider answer to a demand: a `draft` booking the customer confirms and pays for."""
    offer = await session.get(Offer, match.offer_id)
    if offer is None:
        raise NotFound("Offer not found for match")
    if offer.org_id != org_id:
        raise OwnershipRequired("This match belongs to another organization")
    if match.status != "suggested":
        raise InvalidStateTransition(
            f"Match is in status '{match.status}' and cannot be accepted")
    demand = await session.get(Demand, match.demand_id)
    if demand is None:
        raise NotFound("Demand not found for match")
    if demand.status != "open":
        raise Conflict(f"Demand is {demand.status} and no longer open for acceptance")
    if demand.desired_start is None or demand.desired_end is None:
        raise ValidationFailed("This demand has no time window to book")

    booking = await booking_svc.create_booking(
        session, customer_id=demand.customer_id, request=request, offer_id=offer.id,
        start=demand.desired_start, end=demand.desired_end, quantity=demand.quantity,
        source_match_id=match.id, created_by_user_id=actor_user_id, status="draft")
    match.status = "accepted"
    match.provider_action_at = utcnow()
    demand.status = "matched"
    await session.flush()
    await record_audit(session, request, action="match.accepted", entity_type="match",
                       entity_id=match.id, actor_org_id=org_id,
                       after={"booking_id": str(booking.id), "offer_id": str(offer.id)})
    await notify(session, user_id=demand.customer_id, kind="match.accepted",
                 title="A provider accepted your demand",
                 body=f"{offer.title} is held for your requested window. "
                      f"Confirm the booking to lock it in.",
                 data={"booking_id": str(booking.id), "match_id": str(match.id),
                       "demand_id": str(demand.id)})
    return booking


async def reject_match(session: AsyncSession, match: Match, *, actor_user_id: uuid.UUID,
                       org_id: uuid.UUID, request: Request | None = None) -> Match:
    offer = await session.get(Offer, match.offer_id)
    if offer is None or offer.org_id != org_id:
        raise OwnershipRequired("This match belongs to another organization")
    if match.status != "suggested":
        raise InvalidStateTransition(
            f"Match is in status '{match.status}' and cannot be declined")
    match.status = "declined"
    match.provider_action_at = utcnow()
    await session.flush()
    await record_audit(session, request, action="match.declined", entity_type="match",
                       entity_id=match.id, actor_org_id=org_id)
    demand = await session.get(Demand, match.demand_id)
    if demand is not None:
        await notify(session, user_id=demand.customer_id, kind="match.declined",
                     title="A provider declined your demand",
                     body=f"{offer.title} is not available for your requested window.",
                     data={"match_id": str(match.id), "demand_id": str(demand.id)})
    return match


async def booking_id_for_match(session: AsyncSession,
                               match_id: uuid.UUID) -> uuid.UUID | None:
    return (await session.execute(
        select(Booking.id).where(Booking.source_match_id == match_id).limit(1)
    )).scalar_one_or_none()
