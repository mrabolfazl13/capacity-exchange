"""`/api/v1/organizations` — provider tenant management and staff (§4, §8)."""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select

from app.api.v1.deps import Actor, ActorDep, PageDep, envelope, get_actor
from app.core.deps import SessionDep
from app.core.errors import Conflict, NotFound, OwnershipRequired
from app.core.routing import TransactionalRoute
from app.models.identity import OrgStaff, Organization, User, UserRole
from app.schemas.identity import (
    OrganizationInput,
    OrganizationOut,
    OrgStaffInput,
    OrgStaffOut,
)
from app.services import auth as svc

router = APIRouter(route_class=TransactionalRoute, prefix="/organizations", tags=["organizations"])


def _org_out(org: Organization) -> dict:
    return OrganizationOut.model_validate(org).model_dump(mode="json")


async def _require_member(session, actor: Actor, org_id: uuid.UUID) -> OrgStaff:
    row = (
        await session.execute(
            select(OrgStaff).where(OrgStaff.org_id == org_id, OrgStaff.user_id == actor.user_id,
                                   OrgStaff.status == "active")
        )
    ).scalar_one_or_none()
    if row is None and not actor.is_platform_admin:
        raise OwnershipRequired("You are not an active member of this organization")
    return row


@router.post("", status_code=201)
async def create_organization(body: OrganizationInput, request: Request, session: SessionDep,
                              actor: ActorDep) -> dict:
    """Register-flow helper: a user can add a second provider org after signing up."""
    if not body.name.strip():
        raise Conflict("Organization name is required")
    tenant = await svc.get_or_create_default_tenant(session)
    user = await session.get(User, actor.user_id)
    org = await svc.create_organization(session, user, body, tenant.id)
    session.add(UserRole(user_id=user.id, role="org_admin", granted_by=user.id))
    session.add(UserRole(user_id=user.id, role="provider", granted_by=user.id))
    await session.flush()
    return _org_out(org)


@router.get("/mine")
async def my_organizations(session: SessionDep, actor: ActorDep) -> dict:
    rows = list((
        await session.execute(
            select(Organization).where(Organization.id.in_(actor.org_ids)).order_by(
                Organization.created_at)
        )
    ).scalars().all()) if actor.org_ids else []
    if actor.is_platform_admin:
        rows = list((
            await session.execute(select(Organization).order_by(Organization.created_at))
        ).scalars().all())
    return {"items": [_org_out(o) for o in rows], "total": len(rows),
            "limit": len(rows), "offset": 0}


@router.get("/{org_id}")
async def get_organization(org_id: uuid.UUID, session: SessionDep, actor: ActorDep) -> dict:
    await _require_member(session, actor, org_id)
    org = await session.get(Organization, org_id)
    if org is None:
        raise NotFound("Organization not found")
    return _org_out(org)


@router.patch("/{org_id}")
async def patch_organization(org_id: uuid.UUID, body: OrganizationInput, session: SessionDep,
                             actor: ActorDep) -> dict:
    await _require_member(session, actor, org_id)
    org = await session.get(Organization, org_id)
    if org is None:
        raise NotFound("Organization not found")
    for name, value in body.model_dump(exclude_none=True).items():
        if name == "slug":
            continue  # slug is the public identity of the tenant; it does not change
        setattr(org, name, value.model_dump(exclude_none=True) if name == "address" else value)
    await session.flush()
    return _org_out(org)


@router.get("/{org_id}/staff")
async def list_staff(org_id: uuid.UUID, session: SessionDep, actor: ActorDep,
                     page: PageDep) -> dict:
    await _require_member(session, actor, org_id)
    total = (
        await session.execute(
            select(func.count()).select_from(OrgStaff).where(OrgStaff.org_id == org_id)
        )
    ).scalar_one()
    rows = list((
        await session.execute(
            select(OrgStaff).where(OrgStaff.org_id == org_id)
            .order_by(OrgStaff.created_at).limit(page.limit).offset(page.offset)
        )
    ).scalars().all())
    items = []
    for row in rows:
        user = await session.get(User, row.user_id)
        item = OrgStaffOut.model_validate(row).model_dump(mode="json")
        item["email"] = user.email if user else None
        item["full_name"] = user.full_name if user else None
        items.append(item)
    return envelope(items, total, page)


@router.post("/{org_id}/staff", status_code=201)
async def add_staff(org_id: uuid.UUID, body: OrgStaffInput, session: SessionDep,
                    actor: ActorDep) -> dict:
    """Invite an existing account by email; unknown emails are 404, never silently created."""
    me = await _require_member(session, actor, org_id)
    if me.role not in ("org_admin",) and not actor.is_platform_admin:
        raise OwnershipRequired("Only an organization admin can manage staff")
    user = (
        await session.execute(select(User).where(func.lower(User.email) == body.email.lower()))
    ).scalar_one_or_none()
    if user is None:
        raise NotFound("No account exists with that email")
    existing = (
        await session.execute(
            select(OrgStaff).where(OrgStaff.org_id == org_id, OrgStaff.user_id == user.id)
        )
    ).scalar_one_or_none()
    if existing is not None and existing.status == "active":
        raise Conflict("That user is already a member of this organization")
    row = existing or OrgStaff(org_id=org_id, user_id=user.id, invited_by=actor.user_id)
    row.role = body.role
    row.status = "active"
    session.add(row)
    if "provider" not in await svc.get_user_roles(session, user.id):
        session.add(UserRole(user_id=user.id, role="provider", granted_by=actor.user_id))
    await session.flush()
    return OrgStaffOut.model_validate(row).model_dump(mode="json")


@router.delete("/{org_id}/staff/{user_id}", status_code=204)
async def revoke_staff(org_id: uuid.UUID, user_id: uuid.UUID, session: SessionDep,
                       actor: ActorDep) -> None:
    me = await _require_member(session, actor, org_id)
    if me.role not in ("org_admin",) and not actor.is_platform_admin:
        raise OwnershipRequired("Only an organization admin can manage staff")
    row = (
        await session.execute(
            select(OrgStaff).where(OrgStaff.org_id == org_id, OrgStaff.user_id == user_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("Staff membership not found")
    if row.role == "org_admin" and user_id != actor.user_id:
        # Handing over ownership is an explicit admin action, not a side effect of DELETE.
        raise Conflict("Transfer org_admin before revoking this member")
    row.status = "revoked"
    await session.flush()
    return None
