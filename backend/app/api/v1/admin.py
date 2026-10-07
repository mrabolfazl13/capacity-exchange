"""`/api/v1/admin` — the platform operator's surface (CONTRACTS §8).

Tenancy inverts here: every list is deliberately unscoped, so the only thing standing
between these routes and another tenant's data is the role gate on each handler. Queries
reuse the same DTOs as the public endpoints and add only the columns an operator needs to
triage without a second request.

Aggregation lives in `app.services.admin`; this module parses query parameters, applies the
gate and shapes the wire payload.
"""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.api.v1.deps import ActorDep, PageDep, envelope, resolve_window
from app.core.deps import SessionDep
from app.core.errors import RoleRequired, ValidationFailed
from app.core.routing import TransactionalRoute
from app.models.crosscut import (
    DISPUTE_KINDS,
    DISPUTE_STATUSES,
    PROMOTION_KINDS,
    PROMO_STATUSES,
)
from app.models.identity import ORG_STATUSES
from app.schemas.capacity import CapacityCategoryOut
from app.schemas.commerce import (
    AdminDisputeOut,
    AdminProviderOut,
    AuditLogOut,
    CategoryPatch,
    CouponRedemptionOut,
    PromotionInput,
    PromotionOut,
    PromotionPatch,
)
from app.schemas.dashboard import PlatformAnalytics
from app.schemas.identity import AdminUserRow, RoleInput, UserRoleOut
from app.services import admin as svc

router = APIRouter(route_class=TransactionalRoute, prefix="/admin", tags=["admin"])

ANALYTICS_MAX_DAYS = 92
ANALYTICS_DEFAULT_DAYS = 30


def _require_admin(actor) -> None:
    if not actor.is_platform_admin:
        raise RoleRequired("Platform role required",
                           details={"required_roles": ["platform_admin"]})


def _one_of(value: str | None, allowed: tuple[str, ...], label: str) -> None:
    if value is not None and value not in allowed:
        raise ValidationFailed(f"Unknown {label}", details={"allowed": list(allowed)})


# --------------------------------------------------------------------------- users


@router.get("/users")
async def list_users(session: SessionDep, actor: ActorDep, page: PageDep,
                     q: Annotated[str | None, Query(max_length=200)] = None,
                     role: Annotated[str | None, Query(max_length=32)] = None,
                     active: bool | None = None) -> dict:
    _require_admin(actor)
    if role == "customer":
        raise ValidationFailed("Every account is a customer; filter by a granted role instead",
                               details={"allowed": list(svc.GRANTABLE_ROLES)})
    _one_of(role, svc.GRANTABLE_ROLES, "role")
    items, total = await svc.list_users(session, q=q, role=role, is_active=active,
                                        limit=page.limit, offset=page.offset)
    return envelope([AdminUserRow.model_validate(i).model_dump(mode="json") for i in items],
                    total, page)


# -------------------------------------------------------------------------- roles


@router.get("/users/{user_id}/roles")
async def list_user_roles(user_id: uuid.UUID, session: SessionDep, actor: ActorDep,
                          page: PageDep) -> dict:
    _require_admin(actor)
    await svc.load_user(session, user_id)
    items, total = await svc.list_roles(session, user_id, limit=page.limit, offset=page.offset)
    return envelope([UserRoleOut.model_validate(i).model_dump(mode="json") for i in items],
                    total, page)


@router.post("/users/{user_id}/roles", status_code=201)
async def grant_user_role(user_id: uuid.UUID, body: RoleInput, request: Request,
                          session: SessionDep, actor: ActorDep) -> dict:
    """A grant reaches the caller on its very next request, and a revocation stops it there."""
    _require_admin(actor)
    user = await svc.load_user(session, user_id)
    grant = await svc.grant_role(session, user, body.role, actor_user_id=actor.user_id,
                                 request=request)
    return UserRoleOut.model_validate(grant).model_dump(mode="json")


@router.delete("/users/{user_id}/roles/{role}", status_code=204)
async def revoke_user_role(user_id: uuid.UUID, role: str, request: Request, session: SessionDep,
                           actor: ActorDep) -> None:
    _require_admin(actor)
    _one_of(role, svc.GRANTABLE_ROLES, "role")
    user = await svc.load_user(session, user_id)
    await svc.revoke_role(session, user, role, actor_user_id=actor.user_id, request=request)


# ----------------------------------------------------------------------- providers


@router.get("/providers")
async def list_providers(session: SessionDep, actor: ActorDep, page: PageDep,
                         q: Annotated[str | None, Query(max_length=200)] = None,
                         status: Annotated[str | None, Query(max_length=32)] = None,
                         country: Annotated[str | None, Query(min_length=2, max_length=2)] = None
                         ) -> dict:
    """The supply side with the counts that make each profile triageable in one table."""
    _require_admin(actor)
    _one_of(status, ORG_STATUSES, "organization status")
    items, total = await svc.list_providers(session, q=q, status=status, country=country,
                                            limit=page.limit, offset=page.offset)
    return envelope([AdminProviderOut.model_validate(i).model_dump(mode="json") for i in items],
                    total, page)


# ------------------------------------------------------------------------ disputes


@router.get("/disputes")
async def dispute_queue(session: SessionDep, actor: ActorDep, page: PageDep,
                        status: Annotated[str | None, Query(max_length=32)] = None,
                        kind: Annotated[str | None, Query(max_length=24)] = None,
                        org_id: uuid.UUID | None = None,
                        oldest_first: bool = False) -> dict:
    """Support's working list: each row carries the service, the money and its age."""
    if not actor.is_support:
        raise RoleRequired("Support role required",
                             details={"required_roles": ["support"]})
    _one_of(status, DISPUTE_STATUSES, "dispute status")
    _one_of(kind, DISPUTE_KINDS, "dispute kind")
    items, total = await svc.dispute_queue(
        session, actor_user_id=actor.user_id, status=status, kind=kind, org_id=org_id,
        oldest_first=oldest_first, limit=page.limit, offset=page.offset)
    return envelope([AdminDisputeOut.model_validate(i).model_dump(mode="json") for i in items],
                    total, page)


# ----------------------------------------------------------------------- audit log


@router.get("/audit-logs")
async def list_audit_logs(session: SessionDep, actor: ActorDep, page: PageDep,
                          action: Annotated[str | None, Query(max_length=64)] = None,
                          entity_type: Annotated[str | None, Query(max_length=64)] = None,
                          entity_id: uuid.UUID | None = None,
                          actor_user_id: uuid.UUID | None = None,
                          org_id: uuid.UUID | None = None,
                          date_from: Annotated[str | None, Query(alias="from", max_length=32)] = None,
                          date_to: Annotated[str | None, Query(alias="to", max_length=32)] = None,
                          ) -> dict:
    """The append-only trail (§5.1), newest first, over the trailing window unless one is given."""
    _require_admin(actor)
    start, end = resolve_window(date_from, date_to, max_days=ANALYTICS_MAX_DAYS,
                                default_days=ANALYTICS_DEFAULT_DAYS)
    rows, total = await svc.audit_rows(
        session, action=action, entity_type=entity_type, entity_id=entity_id,
        actor_user_id=actor_user_id, org_id=org_id, start=start, end=end,
        limit=page.limit, offset=page.offset)
    return envelope([AuditLogOut.model_validate(r).model_dump(mode="json") for r in rows],
                    total, page)


# ----------------------------------------------------------------------- analytics


@router.get("/analytics")
async def analytics(session: SessionDep, actor: ActorDep,
                    date_from: Annotated[str | None, Query(alias="from", max_length=32)] = None,
                    date_to: Annotated[str | None, Query(alias="to", max_length=32)] = None,
                    ) -> dict:
    _require_admin(actor)
    start, end = resolve_window(date_from, date_to, max_days=ANALYTICS_MAX_DAYS,
                                default_days=ANALYTICS_DEFAULT_DAYS)
    rows = await svc.platform_analytics(session, start=start, end=end)
    return PlatformAnalytics.model_validate(rows).model_dump(mode="json", by_alias=True)


# --------------------------------------------------------------------- promotions


@router.get("/promotions")
async def list_promotions(session: SessionDep, actor: ActorDep, page: PageDep,
                          q: Annotated[str | None, Query(max_length=200)] = None,
                          status: Annotated[str | None, Query(max_length=16)] = None,
                          kind: Annotated[str | None, Query(max_length=16)] = None) -> dict:
    _require_admin(actor)
    _one_of(status, PROMO_STATUSES, "promotion status")
    _one_of(kind, PROMOTION_KINDS, "promotion kind")
    items, total = await svc.list_promotions(session, q=q, status=status, kind=kind,
                                             limit=page.limit, offset=page.offset)
    return envelope([PromotionOut.model_validate(i).model_dump(mode="json") for i in items],
                    total, page)


@router.post("/promotions", status_code=201)
async def create_promotion(body: PromotionInput, request: Request, session: SessionDep,
                           actor: ActorDep) -> dict:
    """A coupon is live money the moment it is `active`; the order path reads the same row."""
    _require_admin(actor)
    promotion = await svc.create_promotion(session, body.model_dump(),
                                           actor_user_id=actor.user_id, request=request)
    return PromotionOut.model_validate(
        svc.promotion_to_dict(promotion, created_by_name=actor.full_name)
    ).model_dump(mode="json")


@router.get("/promotions/{promotion_id}")
async def get_promotion(promotion_id: uuid.UUID, session: SessionDep, actor: ActorDep) -> dict:
    _require_admin(actor)
    return PromotionOut.model_validate(
        svc.promotion_to_dict(await svc.load_promotion(session, promotion_id))
    ).model_dump(mode="json")


@router.patch("/promotions/{promotion_id}")
async def patch_promotion(promotion_id: uuid.UUID, body: PromotionPatch, request: Request,
                          session: SessionDep, actor: ActorDep) -> dict:
    """Limits, window and status move; the redemption count belongs to the orders (§5.6)."""
    _require_admin(actor)
    promotion = await svc.load_promotion(session, promotion_id)
    promotion = await svc.patch_promotion(session, promotion, body.model_dump(exclude_none=True),
                                          actor_user_id=actor.user_id, request=request)
    return PromotionOut.model_validate(
        svc.promotion_to_dict(promotion, created_by_name=actor.full_name)
    ).model_dump(mode="json")


@router.get("/promotions/{promotion_id}/redemptions")
async def promotion_redemptions(promotion_id: uuid.UUID, session: SessionDep, actor: ActorDep,
                                page: PageDep) -> dict:
    _require_admin(actor)
    await svc.load_promotion(session, promotion_id)
    items, total = await svc.promotion_redemptions(session, promotion_id, limit=page.limit,
                                                   offset=page.offset)
    return envelope([CouponRedemptionOut.model_validate(i).model_dump(mode="json")
                     for i in items], total, page)


# ---------------------------------------------------------------------- categories


@router.get("/categories")
async def list_categories(session: SessionDep, actor: ActorDep, page: PageDep,
                          active_only: bool = False) -> dict:
    """Retired categories stay listed for admins so they can be restored (§5.2)."""
    _require_admin(actor)
    rows, total = await svc.list_categories(session, active_only=active_only,
                                           limit=page.limit, offset=page.offset)
    return envelope([CapacityCategoryOut.model_validate(r).model_dump(mode="json") for r in rows],
                    total, page)


@router.patch("/categories/{category_id}")
async def patch_category(category_id: uuid.UUID, body: CategoryPatch, request: Request,
                         session: SessionDep, actor: ActorDep) -> dict:
    """Retiring hides the category from search and the wizard; existing offers keep their row."""
    _require_admin(actor)
    category = await svc.load_category(session, category_id)
    category = await svc.patch_category(session, category, label=body.label,
                                        is_active=body.is_active,
                                        actor_user_id=actor.user_id, request=request)
    return CapacityCategoryOut.model_validate(category).model_dump(mode="json")
