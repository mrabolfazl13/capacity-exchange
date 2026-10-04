"""CONTRACTS §5.1 identity & org models: tenants, organizations, users, roles, staff, sessions."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPkMixin, ck_enum

ROLE_KEYS = ("platform_admin", "support", "org_admin", "provider", "customer")
ORG_STAFF_ROLES = ("org_admin", "manager", "staff")
ORG_STAFF_STATUSES = ("active", "invited", "revoked")
ORG_STATUSES = ("active", "suspended")
TENANT_STATUSES = ("active", "suspended")


def uuid_fk(target: str, *, nullable: bool = False) -> mapped_column:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey(target, ondelete="CASCADE"), nullable=nullable
    )


class Tenant(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "tenants"

    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'active'"),
        comment="active|suspended",
    )
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    __table_args__ = (
        CheckConstraint(ck_enum("tenants_status", "status", TENANT_STATUSES), name="tenants_status"),
    )


class User(UUIDPkMixin, TimestampMixin, Base):
    """§5.1: email is ALWAYS stored lower()-normalized by the app (citext is unavailable)."""

    __tablename__ = "users"

    tenant_id: Mapped[uuid.UUID] = uuid_fk("tenants.id")
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32))
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    preferred_locale: Mapped[str] = mapped_column(String(8), nullable=False, server_default=text("'en'"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = uuid_fk("users.id", nullable=True)

    roles: Mapped[list["UserRole"]] = relationship(
        back_populates="user", cascade="all, delete-orphan",
        foreign_keys="UserRole.user_id")
    memberships: Mapped[list["OrgStaff"]] = relationship(
        back_populates="user", cascade="all, delete-orphan",
        foreign_keys="OrgStaff.user_id")

    __table_args__ = (
        Index("ix_users_tenant_id_phone", "tenant_id", "phone", unique=True, postgresql_where=text("phone IS NOT NULL")),
    )

    @property
    def normalized_email(self) -> str:
        return self.email.lower()


class UserRole(Base):
    """Global role grants (§4). Composite PK (user_id, role) — no surrogate id."""

    __tablename__ = "user_roles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(32), primary_key=True)
    granted_by: Mapped[uuid.UUID | None] = uuid_fk("users.id", nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())

    user: Mapped[User] = relationship(back_populates="roles", foreign_keys=[user_id])

    __table_args__ = (
        CheckConstraint(ck_enum("user_roles_role", "role", ROLE_KEYS), name="role"),
    )


class Organization(UUIDPkMixin, TimestampMixin, Base):
    """The tenant boundary for provider-side data (§4)."""

    __tablename__ = "organizations"

    tenant_id: Mapped[uuid.UUID] = uuid_fk("tenants.id")
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String(200))
    country: Mapped[str | None] = mapped_column(String(2))
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("'UTC'"))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default=text("'USD'"))
    contact_email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(32))
    address: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'active'"))
    created_by: Mapped[uuid.UUID | None] = uuid_fk("users.id", nullable=True)

    staff: Mapped[list["OrgStaff"]] = relationship(back_populates="org", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint(ck_enum("organizations_status", "status", ORG_STATUSES), name="status"),
        CheckConstraint("country IS NULL OR length(country) = 2", name="country_len"),
        CheckConstraint("currency IS NULL OR length(currency) = 3", name="currency_len"),
        Index("ix_organizations_tenant_id", "tenant_id"),
    )


class OrgStaff(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "org_staff"

    org_id: Mapped[uuid.UUID] = uuid_fk("organizations.id")
    user_id: Mapped[uuid.UUID] = uuid_fk("users.id")
    role: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'staff'"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'active'"))
    invited_by: Mapped[uuid.UUID | None] = uuid_fk("users.id", nullable=True)

    org: Mapped[Organization] = relationship(back_populates="staff")
    user: Mapped[User] = relationship(
        back_populates="memberships", foreign_keys=[user_id])

    __table_args__ = (
        UniqueConstraint("org_id", "user_id", name="uq_org_staff_org_id_user_id"),
        CheckConstraint(ck_enum("org_staff_role", "role", ORG_STAFF_ROLES), name="role"),
        CheckConstraint(ck_enum("org_staff_status", "status", ORG_STAFF_STATUSES), name="status"),
        Index("ix_org_staff_user_id", "user_id"),
    )


class Session(UUIDPkMixin, TimestampMixin, Base):
    """Refresh-token session (§3). `refreshed_at` marks rotation so reuse can revoke the family."""

    __tablename__ = "sessions"

    user_id: Mapped[uuid.UUID] = uuid_fk("users.id")
    org_id: Mapped[uuid.UUID | None] = uuid_fk("organizations.id", nullable=True)
    refresh_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    family: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, server_default=text("gen_random_uuid()"))
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("length(refresh_hash) = 64", name="refresh_hash_len"),
        Index("ix_sessions_refresh_hash", "refresh_hash"),
        Index("ix_sessions_family", "family"),
        Index("ix_sessions_user_id", "user_id"),
    )

    @property
    def is_valid(self) -> bool:
        return self.revoked_at is None and self.refreshed_at is None
