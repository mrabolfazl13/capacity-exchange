"""Auth/RBAC dependencies (CONTRACTS §3/§4): current_user, require_roles, require_org_membership."""
from __future__ import annotations

import uuid
from typing import Annotated, Any, Callable, Coroutine

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.db import get_session
from app.core.errors import NotFound, NotMember, RateLimited, RoleRequired, Unauthorized
from app.core.security import decode_access_token
from app.models.identity import OrgStaff, Organization, User, UserRole

DEFAULT_TENANT_KEY = "default"


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _bearer_token(request: Request) -> str:
    header = request.headers.get("Authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise Unauthorized("Missing bearer token")
    return token.strip()


def _client_roles(payload: dict[str, Any]) -> list[str]:
    """JWT roles + implicit customer (§4: every user has customer implicitly for reads)."""
    roles = set(payload.get("roles") or [])
    roles.add("customer")
    return sorted(roles)


async def current_user(request: Request, session: SessionDep,
                       settings: SettingsDep) -> User:
    """Resolve the Bearer JWT to an active User; stash org/roles on request.state."""
    token = _bearer_token(request)
    payload = decode_access_token(settings, token)
    try:
        user_id = uuid.UUID(str(payload.get("sub")))
    except ValueError as exc:
        raise Unauthorized("Access token subject is not a user id") from exc
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise Unauthorized("User is inactive or no longer exists")
    request.state.access_payload = payload
    request.state.current_user_id = user.id
    request.state.current_org_id = uuid.UUID(payload["org"]) if payload.get("org") else None
    # Roles are re-read instead of trusted from the token: revoking a support or platform
    # role has to stop working on the next request, not when the access token expires (§4).
    request.state.current_roles = await load_user_roles(session, user.id)
    return user


CurrentUser = Annotated[User, Depends(current_user)]


async def load_user_roles(session: AsyncSession, user_id: uuid.UUID) -> list[str]:
    """Fresh role list for token issuance (explicit grants + implicit customer)."""
    rows = (await session.execute(select(UserRole.role).where(UserRole.user_id == user_id))).scalars().all()
    return _client_roles({"roles": list(rows)})


def require_roles(*required: str) -> Callable[..., Coroutine[Any, Any, User]]:
    """Dependency factory: 403 role_required unless caller holds one of `required`.

    platform_admin passes every role gate (§4).
    """
    async def _dep(user: CurrentUser, request: Request) -> User:
        roles = set(getattr(request.state, "current_roles", []))
        if "platform_admin" in roles or roles & set(required):
            return user
        raise RoleRequired(
            "Requires role: " + ", ".join(required),
            details={"required_roles": list(required)},
        )

    return _dep


async def require_org_membership(request: Request, session: SessionDep,
                                 user: CurrentUser) -> Organization:
    """Active org_staff membership for the org in the path (`org_id`, else `id`); platform_admin bypasses."""
    org_id_s = request.path_params.get("org_id") or request.path_params.get("id")
    if not org_id_s:
        raise NotMember("org_id path parameter required")
    try:
        org_id = uuid.UUID(str(org_id_s))
    except ValueError as exc:
        raise NotMember("Invalid org id") from exc
    roles = set(getattr(request.state, "current_roles", []))
    org = await session.get(Organization, org_id)
    if org is None:
        raise NotFound("Organization not found")
    if "platform_admin" not in roles:
        stmt = select(OrgStaff).where(
            OrgStaff.org_id == org_id,
            OrgStaff.user_id == user.id,
            OrgStaff.status == "active",
        )
        staff = (await session.execute(stmt)).scalar_one_or_none()
        if staff is None:
            raise NotMember("Not a member of this organization")
        request.state.current_org_staff_role = staff.role
    else:
        request.state.current_org_staff_role = "org_admin"
    return org
