"""`/api/v1/dashboard` — the three audience home screens (CONTRACTS §8).

Aggregates live in `app.services.dashboard`; this module only applies tenancy (§4) and the
wire DTOs (§1). The provider and customer screens reuse the shared booking/order payload
builders so a dashboard card and the matching detail view can never render differently.
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from app.api.v1.deps import ActorDep, booking_payload, order_payload, parse_window
from app.core.deps import SessionDep
from app.core.errors import OwnershipRequired, ValidationFailed
from app.schemas.dashboard import (
    AdminDashboard,
    CustomerDashboard,
    ProviderDashboard,
    TopOfferRow,
)
from app.services import dashboard as svc

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _window(date_from: str | None, date_to: str | None) -> tuple[Any, Any]:
    """`?from&to` are a pair or absent; absent means the trailing window (§8)."""
    if bool(date_from) != bool(date_to):
        raise ValidationFailed("`from` and `to` must be provided together")
    if not date_from:
        return svc.default_window()
    return parse_window(date_from, date_to, max_days=svc.MAX_WINDOW_DAYS)


@router.get("/provider")
async def provider_dashboard(session: SessionDep, actor: ActorDep,
                             date_from: Annotated[str | None, Query(alias="from")] = None,
                             date_to: Annotated[str | None, Query(alias="to")] = None) -> dict:
    org_id = actor.require_org(actor.active_org_id)
    if not actor.can_manage_org(org_id):
        raise OwnershipRequired("You do not manage this organization")
    start, end = _window(date_from, date_to)
    rows = await svc.provider_dashboard(session, org_id=org_id, start=start, end=end)
    out = ProviderDashboard.model_validate(
        {k: v for k, v in rows.items() if k not in ("upcoming_bookings", "top_offers")}
    ).model_dump(mode="json")
    out["upcoming_bookings"] = [await booking_payload(session, b) for b in rows["upcoming_bookings"]]
    out["top_offers"] = [TopOfferRow.model_validate(r).model_dump(mode="json")
                         for r in rows["top_offers"]]
    return out


@router.get("/customer")
async def customer_dashboard(session: SessionDep, actor: ActorDep) -> dict:
    rows = await svc.customer_dashboard(session, user_id=actor.user_id)
    out = CustomerDashboard.model_validate(
        {k: v for k, v in rows.items() if k not in ("active_bookings", "recent_orders")}
    ).model_dump(mode="json")
    out["active_bookings"] = [await booking_payload(session, b) for b in rows["active_bookings"]]
    out["recent_orders"] = [await order_payload(session, o) for o in rows["recent_orders"]]
    return out


@router.get("/admin")
async def admin_dashboard(session: SessionDep, actor: ActorDep) -> dict:
    if not actor.is_platform_admin:
        raise OwnershipRequired("Platform role required")
    return AdminDashboard.model_validate(
        await svc.admin_dashboard(session)).model_dump(mode="json")
