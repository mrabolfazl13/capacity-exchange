"""Auth service (CONTRACTS §3): Argon2id passwords, JWT access tokens, rotating refresh sessions.

Refresh rotation is the security-critical part: a token that has already been `refreshed_at`
is evidence of replay, so the entire `family` is revoked rather than just that row. The
legitimate client then fails with `session_revoked` and must log in again — which is the
correct outcome when a refresh token has leaked.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import Request
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.config import Settings
from app.core.errors import (
    Conflict,
    InvalidCredentials,
    NotFound,
    SessionRevoked,
    Unauthorized,
)
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    password_needs_rehash,
    refresh_expires_at,
    verify_password,
)
from app.models.identity import OrgStaff, Organization, Session, Tenant, User, UserRole
from app.schemas.identity import (
    OrganizationInput,
    RegisterInput,
)

DEFAULT_TENANT_KEY = "default"
logger = logging.getLogger("capacityexchange.auth")
PROVIDER_ROLES = ("org_admin", "provider")
CUSTOMER_ROLES: tuple[str, ...] = ()  # `customer` is implicit for every user (§4)


async def get_or_create_default_tenant(session: AsyncSession) -> Tenant:
    tenant = (
        await session.execute(select(Tenant).where(Tenant.key == DEFAULT_TENANT_KEY))
    ).scalar_one_or_none()
    if tenant is not None:
        return tenant
    tenant = Tenant(key=DEFAULT_TENANT_KEY, name="Capacity Exchange", status="active")
    session.add(tenant)
    await session.flush()
    return tenant


async def get_user_roles(session: AsyncSession, user_id: uuid.UUID) -> list[str]:
    rows = (await session.execute(select(UserRole.role).where(UserRole.user_id == user_id))).scalars().all()
    roles = set(rows) | {"customer"}
    return sorted(roles)


async def get_active_org_id(session: AsyncSession, user_id: uuid.UUID) -> uuid.UUID | None:
    row = (
        await session.execute(
            select(OrgStaff.org_id)
            .where(OrgStaff.user_id == user_id, OrgStaff.status == "active")
            .order_by(OrgStaff.created_at)
            .limit(1)
        )
    ).scalar_one_or_none()
    return row


async def get_user_org_ids(session: AsyncSession, user_id: uuid.UUID) -> set[uuid.UUID]:
    """Every org the user actively belongs to — the tenancy scope for provider-side reads (§4)."""
    rows = (
        await session.execute(
            select(OrgStaff.org_id).where(OrgStaff.user_id == user_id,
                                          OrgStaff.status == "active")
        )
    ).scalars().all()
    return set(rows)


def _slugify(name: str) -> str:
    cleaned = "".join(c if c.isalnum() else "-" for c in name.strip().lower())
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-")[:64] or "org"


async def _unique_slug(session: AsyncSession, base: str) -> str:
    slug = base
    suffix = 2
    while (
        await session.execute(select(Organization.id).where(Organization.slug == slug))
    ).scalar_one_or_none() is not None:
        slug = f"{base[:56]}-{suffix}"
        suffix += 1
    return slug


async def issue_session(
    session: AsyncSession,
    settings: Settings,
    *,
    user: User,
    org_id: uuid.UUID | None,
    request: Request | None,
    family: uuid.UUID | None = None,
) -> tuple[str, str, datetime]:
    """Create a refresh row and return (access_jwt, refresh_plaintext, access_expires_at)."""
    refresh_plain, refresh_hash = generate_refresh_token()
    ip = request.client.host if request and request.client else None
    user_agent = request.headers.get("user-agent") if request else None
    session_row = Session(
        user_id=user.id,
        org_id=org_id,
        refresh_hash=refresh_hash,
        family=family or uuid.uuid4(),
        ip=ip,
        user_agent=user_agent[:500] if user_agent else None,
        expires_at=refresh_expires_at(settings),
    )
    session.add(session_row)
    await session.flush()
    roles = await get_user_roles(session, user.id)
    access, expires_at = create_access_token(settings, user_id=user.id, org_id=org_id, roles=roles)
    return access, refresh_plain, expires_at


async def register(session: AsyncSession, settings: Settings, payload: RegisterInput,
                   request: Request | None = None) -> dict:
    """Public registration for both account types; provider also creates its organization."""
    email = payload.email.lower().strip()
    existing = (await session.execute(select(User).where(func.lower(User.email) == email))).scalar_one_or_none()
    if existing is not None:
        # §2: taken email is a 409 conflict on the resource, never a 422 that echoes the address.
        raise Conflict("An account with this email already exists")

    tenant = await get_or_create_default_tenant(session)
    user = User(
        tenant_id=tenant.id,
        email=email,
        phone=payload.phone,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        preferred_locale=getattr(payload, "preferred_locale", "en") or "en",
    )
    session.add(user)
    await session.flush()

    org_id: uuid.UUID | None = None
    if payload.is_provider:
        organization = payload.organization or OrganizationInput(name=payload.full_name)
        org = await create_organization(session, user, organization, tenant.id)
        org_id = org.id
        for role in PROVIDER_ROLES:
            session.add(UserRole(user_id=user.id, role=role, granted_by=user.id))
    else:
        for role in CUSTOMER_ROLES:
            session.add(UserRole(user_id=user.id, role=role, granted_by=user.id))
    await session.flush()

    await record_audit(session, request, action="user.register", entity_type="user",
                       entity_id=user.id, after={"email": email,
                                                 "provider": org_id is not None})
    access, refresh, expires_at = await issue_session(
        session, settings, user=user, org_id=org_id, request=request)
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": int((expires_at - datetime.now(timezone.utc)).total_seconds()),
        "user": await user_dto(session, user, org_id),
    }


async def create_organization(session: AsyncSession, user: User, data: OrganizationInput,
                              tenant_id: uuid.UUID) -> Organization:
    """Create an org and make `user` its org_admin staff member."""
    slug = await _unique_slug(session, data.slug or _slugify(data.name))
    org = Organization(
        tenant_id=tenant_id,
        name=data.name,
        slug=slug,
        legal_name=data.legal_name,
        country=data.country,
        timezone=data.timezone or "UTC",
        currency=(data.currency or "USD").upper(),
        contact_email=data.contact_email,
        phone=data.phone,
        address=data.address.model_dump(exclude_none=True) if data.address else None,
        created_by=user.id,
    )
    session.add(org)
    await session.flush()
    session.add(OrgStaff(org_id=org.id, user_id=user.id, role="org_admin", status="active"))
    await session.flush()
    return org


async def login(session: AsyncSession, settings: Settings, email: str, password: str,
                request: Request | None = None) -> dict:
    email = email.lower().strip()
    user = (
        await session.execute(select(User).where(func.lower(User.email) == email))
    ).scalar_one_or_none()
    # One message for both branches: distinguishing them would enumerate accounts (§4 matrix B).
    if user is None or not verify_password(password, user.password_hash):
        raise InvalidCredentials("Invalid email or password")
    if not user.is_active:
        raise InvalidCredentials("Invalid email or password")

    if password_needs_upgrade(user.password_hash):
        user.password_hash = hash_password(password)

    org_id = await get_active_org_id(session, user.id)
    user.last_login_at = datetime.now(timezone.utc)
    await session.flush()
    await record_audit(session, request, action="user.login", entity_type="user",
                       entity_id=user.id, after={"at": user.last_login_at.isoformat()})
    access, refresh, expires_at = await issue_session(
        session, settings, user=user, org_id=org_id, request=request)
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": int((expires_at - datetime.now(timezone.utc)).total_seconds()),
    }


def password_needs_upgrade(password_hash: str) -> bool:
    return password_needs_rehash(password_hash)


async def refresh(session: AsyncSession, settings: Settings, refresh_token: str,
                  request: Request | None = None) -> dict:
    """Rotate a refresh token; replay of a rotated token revokes the whole family."""
    digest = hash_refresh_token(refresh_token)
    row = (
        await session.execute(select(Session).where(Session.refresh_hash == digest))
    ).scalar_one_or_none()
    if row is None:
        raise Unauthorized("Refresh token is not recognised")
    if row.refreshed_at is not None:
        # Checked before `revoked_at`: rotation marks the old row both ways, so a replay
        # of a rotated token must reach this branch to revoke the rest of the family (§3).
        revoked = await revoke_family(session, row.family)
        # Committed here on purpose: the request still answers 401, and the session
        # dependency rolls back on exceptions — a revocation must survive that rollback.
        await session.commit()
        logger.warning("refresh token replay detected: user=%s family=%s revoked=%d session(s)",
                       row.user_id, row.family, revoked)
        raise SessionRevoked("Refresh token was already used; all sessions in this family were revoked")
    if row.revoked_at is not None:
        raise SessionRevoked("Session has been revoked")
    if row.expires_at <= datetime.now(timezone.utc):
        raise SessionRevoked("Refresh token has expired")

    user = await session.get(User, row.user_id)
    if user is None or not user.is_active:
        raise SessionRevoked("Account is no longer active")

    now = datetime.now(timezone.utc)
    row.refreshed_at = now
    row.revoked_at = now
    await session.flush()

    access, new_refresh, expires_at = await issue_session(
        session, settings, user=user, org_id=row.org_id, request=request, family=row.family)
    return {
        "access_token": access,
        "refresh_token": new_refresh,
        "token_type": "bearer",
        "expires_in": int((expires_at - now).total_seconds()),
    }


async def revoke_family(session: AsyncSession, family: uuid.UUID) -> int:
    now = datetime.now(timezone.utc)
    result = await session.execute(
        update(Session)
        .where(Session.family == family, Session.revoked_at.is_(None))
        .values(revoked_at=now, updated_at=now)
    )
    await session.flush()
    return result.rowcount or 0


async def logout(session: AsyncSession, refresh_token: str | None, *,
                 user_id: uuid.UUID | None = None) -> int:
    """Revoke one session by token, or every session of a caller when no token is given."""
    now = datetime.now(timezone.utc)
    stmt = update(Session).where(Session.revoked_at.is_(None)).values(revoked_at=now, updated_at=now)
    if refresh_token:
        stmt = stmt.where(Session.refresh_hash == hash_refresh_token(refresh_token))
    elif user_id is not None:
        stmt = stmt.where(Session.user_id == user_id)
    else:
        return 0
    result = await session.execute(stmt)
    await session.flush()
    return result.rowcount or 0


async def user_dto(session: AsyncSession, user: User, org_id: uuid.UUID | None = None) -> dict:
    roles = await get_user_roles(session, user.id)
    if org_id is None:
        org_id = await get_active_org_id(session, user.id)
    return {
        "id": user.id,
        "tenant_id": user.tenant_id,
        "email": user.email,
        "phone": user.phone,
        "full_name": user.full_name,
        "preferred_locale": user.preferred_locale,
        "is_active": user.is_active,
        "last_login_at": user.last_login_at,
        "created_at": user.created_at,
        "roles": roles,
        "active_org_id": org_id,
    }


async def resolve_organization(session: AsyncSession, org_id: uuid.UUID) -> Organization:
    org = await session.get(Organization, org_id)
    if org is None:
        raise NotFound("Organization not found")
    return org
