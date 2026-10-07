"""`/api/v1/ai/*` — the assistant surface (CONTRACTS §8).

Five read-only endpoints. Each one runs a deterministic algorithm from
``capacity_ai`` over data this service gathers here; none of them writes, and
none of them is on the booking path — a client that never calls `/ai` loses a
suggestion, not a feature (§12: no fake actions, and AI must degrade gracefully).

Tenancy follows the same rule as the dashboard: anything organization-shaped is
gated on `can_manage_org`, and the pricing sample deliberately carries no
identifiers so a competitor cannot read another org's drafts out of it.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Query

from app.api.v1.deps import Actor, ActorDep, resolve_window
from app.core.deps import SessionDep
from app.core.errors import NotFound, OwnershipRequired, ValidationFailed
from app.services import assistant as svc

from capacity_ai.db import CatalogCategory
from capacity_ai.insights import demand_signal, idle_windows, measure, summarize_copilot
from capacity_ai.listing_draft import draft_listing
from capacity_ai.pricing import MIN_COMPARABLES, suggest_price
from capacity_ai.schemas import (
    CopilotOut,
    ListingDraftOut,
    ListingDraftRequest,
    ParseSearchRequest,
    ParsedQueryOut,
    PriceSuggestRequest,
    PriceSuggestionOut,
    UtilizationInsightsOut,
)
from capacity_ai.semantic_query import CategorySpec, parse_query

router = APIRouter(prefix="/ai", tags=["assistant"])

#: The copilot's usage window: recent history plus the bookable week ahead.
COPILOT_PAST_DAYS = 28
COPILOT_FUTURE_DAYS = 7


def _specs(catalog: list[CatalogCategory]) -> list[CategorySpec]:
    return [CategorySpec(key=c.key, label=c.label, synonyms=c.synonyms) for c in catalog]


def _ids(catalog: list[CatalogCategory]) -> dict[str, str]:
    return {c.key: c.category_id for c in catalog if c.category_id}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _org_scope(actor: Actor, org_id: Optional[uuid.UUID]) -> uuid.UUID:
    target = actor.require_org(org_id or actor.active_org_id)
    if not actor.can_manage_org(target):
        raise OwnershipRequired("You do not manage this organization")
    return target


# ------------------------------------------------------------------ semantic search


@router.post("/parse-search")
async def parse_search(body: ParseSearchRequest, session: SessionDep, actor: ActorDep) -> dict:
    """Free text -> the structured filters `GET /offers` already accepts."""
    catalog = await svc.catalog(session)
    parsed = parse_query(body.text, _now(), _specs(catalog))
    data = ParsedQueryOut.model_validate(asdict(parsed)).model_dump(mode="json")
    data["category_id"] = _ids(catalog).get(parsed.category_key or "")
    return data


# ------------------------------------------------------------------ listing draft


@router.post("/listing-draft")
async def listing_draft(body: ListingDraftRequest, session: SessionDep, actor: ActorDep) -> dict:
    """A provider's raw description -> a fill-in-the-gaps listing draft.

    The draft is returned, never saved: publishing stays an explicit act on
    `POST /resources` (§5.2), and `missing_fields` tells the form which boxes are
    still the provider's decision.
    """
    if body.category_key and not await svc.category_exists(session, body.category_key):
        raise ValidationFailed("Unknown category_key", details={"category_key": body.category_key})
    catalog = await svc.catalog(session)
    draft = draft_listing(
        body.raw_text,
        now=_now(),
        catalog=_specs(catalog),
        category_key=body.category_key,
        currency=body.currency,
    )
    data = ListingDraftOut.model_validate(asdict(draft)).model_dump(mode="json")
    data["category_id"] = _ids(catalog).get(draft.category_key or "")
    return data


# ------------------------------------------------------------------ price suggestion


def _tiers(city: Optional[str], country: Optional[str], mode: Optional[str],
           unit_label: Optional[str]) -> list[tuple[str, dict]]:
    """Widen the comparable sample one axis at a time, cheapest to lose first.

    Geography widens before the billing unit because a Berlin price learned from
    national data is still a price per the same unit, while a per-pallet price
    learned from per-hour listings is simply a different number. The capacity
    mode is the loosest of the three: how a resource is metered matters less
    than what one unit of it costs.
    """
    by_unit = {"unit_label": unit_label} if unit_label else {}
    ladder = [
        (city or country or "", {"city": city, "capacity_mode": mode, **by_unit}),
        (country, {"country": country, "capacity_mode": mode, **by_unit}),
        ("", {"capacity_mode": mode, **by_unit}),
        ("", dict(by_unit)),
        ("", {}),
    ]
    tiers: list[tuple[str, dict]] = []
    seen: set[tuple] = set()
    for region, filters in ladder:
        clean = {k: v for k, v in filters.items() if v}
        marker = (region, tuple(sorted(clean.items())))
        if marker not in seen:
            seen.add(marker)
            tiers.append((region, clean))
    return tiers


def _scope(category_key: str, filters: dict, unit_label: Optional[str]) -> Optional[str]:
    """Name what the sample actually compared, so a widened band reads widened."""
    if unit_label and filters.get("unit_label") != unit_label:
        return f"{category_key}, any billing unit"
    if unit_label:
        return f"{category_key} per {unit_label}"
    return category_key


@router.post("/price-suggest")
async def price_suggest(body: PriceSuggestRequest, session: SessionDep, actor: ActorDep) -> dict:
    """Suggest a price band from published comparables, widening one axis at a time.

    Widening is explicit rather than silent — the rationale names the scope that
    produced the band, because a Berlin price suggested from global data is advice
    the provider should be able to discount.
    """
    category_key, city, country = body.category_key, body.city, body.country
    mode, unit_label = body.capacity_mode, body.unit_label
    offer_id: Optional[uuid.UUID] = None

    if body.offer_id:
        try:
            offer_id = uuid.UUID(body.offer_id)
        except ValueError as exc:
            raise ValidationFailed("`offer_id` must be a UUID") from exc
        shape = await svc.offer_shape(session, offer_id)
        if shape is None:
            raise NotFound("Offer not found")
        category_key = category_key or shape["category_key"]
        city = city or shape["city"]
        country = country or shape["country"]
        mode = mode or shape["capacity_mode"]
        unit_label = unit_label or shape["unit_label"]

    if not category_key:
        raise ValidationFailed("Provide `offer_id` or `category_key`")

    widest: list = []
    for region, filters in _tiers(city, country, mode, unit_label):
        sample = await svc.comparables(
            session,
            category_key=category_key,
            currency=body.currency,
            exclude_offer_id=offer_id,
            **filters,
        )
        if len(widest) < len(sample):
            widest = sample
        if len(sample) >= MIN_COMPARABLES:
            suggestion = suggest_price(
                sample, currency=body.currency, region=region or None,
                scope=_scope(category_key, filters, unit_label))
            return PriceSuggestionOut.model_validate(asdict(suggestion)).model_dump(mode="json")

    floor = max((c.unit_amount_cents for c in widest), default=0)
    suggestion = suggest_price(
        widest, currency=body.currency, region=None,
        scope=_scope(category_key, {}, unit_label),
        cold_start_floor_cents=floor or svc.COLD_START_FLOOR_CENTS)
    return PriceSuggestionOut.model_validate(asdict(suggestion)).model_dump(mode="json")


# ------------------------------------------------------------------ utilization


def _window(date_from: Optional[str], date_to: Optional[str]) -> tuple[datetime, datetime]:
    return resolve_window(date_from, date_to, max_days=svc.MAX_WINDOW_DAYS,
                          default_days=svc.DEFAULT_WINDOW_DAYS)


@router.get("/utilization-insights")
async def utilization_insights(
    session: SessionDep,
    actor: ActorDep,
    org_id: Annotated[uuid.UUID | None, Query()] = None,
    date_from: Annotated[str | None, Query(alias="from")] = None,
    date_to: Annotated[str | None, Query(alias="to")] = None,
) -> dict:
    """Where an organization's published capacity actually went unused."""
    target = _org_scope(actor, org_id)
    start, end = _window(date_from, date_to)
    usage, truncated = await svc.usage(session, org_id=target, start=start, end=end)
    items = [measure(u) for u in usage]
    idle = idle_windows(usage)
    counts = [demand_signal(d) for d in await svc.demand_counts(session, start=start, end=end)]
    payload = UtilizationInsightsOut.model_validate(
        {
            "items": [asdict(i) for i in items],
            "total": len(items),
            "idle_windows": [asdict(w) for w in idle],
            "demand_counts": [asdict(d) for d in counts],
        }
    ).model_dump(mode="json")
    payload["truncated"] = truncated
    payload["window"] = {"from": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "to": end.strftime("%Y-%m-%dT%H:%M:%SZ")}
    return payload


# ------------------------------------------------------------------ copilot


@router.get("/copilot")
async def copilot(
    session: SessionDep,
    actor: ActorDep,
    org_id: Annotated[uuid.UUID | None, Query()] = None,
) -> dict:
    """One paragraph the provider can act on, from the same reads as their dashboard.

    Utilization and idle patterns are the dashboard's service-time numbers; the
    money sentence is cash-time, so it says what was collected rather than what
    will be delivered.
    """
    target = _org_scope(actor, org_id)
    context = await svc.copilot_context(session, org_id=target)
    end = svc.utcnow()
    # History on one side, the bookable near future on the other: an idle pattern
    # is only worth acting on if the same hours are still coming up, and a
    # purely trailing window cannot say that. Revenue stays a trailing 30 days.
    start = end - timedelta(days=COPILOT_PAST_DAYS)
    horizon = end + timedelta(days=COPILOT_FUTURE_DAYS)
    usage, truncated = await svc.usage(session, org_id=target, start=start, end=horizon)
    counts = list(await svc.demand_counts(session, start=start, end=end))
    summary = summarize_copilot(
        usage=usage,
        demands=counts,
        next_7d_bookings=context["next_7d_bookings"],
        at_risk_holds=context["at_risk_holds"],
        revenue_last_30d_cents=context["revenue_last_30d_cents"],
        currency=context["currency"],
        org_name=context["org_name"] or "Your organization",
    )
    payload = CopilotOut.model_validate(asdict(summary)).model_dump(mode="json")
    payload["truncated"] = truncated
    return payload
