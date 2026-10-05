"""§5.1 identity DTOs: users, organizations, token pairs, register/login payloads."""
from __future__ import annotations

from typing import Literal

from pydantic import EmailStr, Field, field_validator

from app.schemas.common import Address, ORMModel, WireDateTime, WireUUID

RoleKey = Literal["platform_admin", "support", "org_admin", "provider", "customer"]
OrgStaffRole = Literal["org_admin", "manager", "staff"]


class UserOut(ORMModel):
    id: WireUUID
    tenant_id: WireUUID
    email: str
    phone: str | None = None
    full_name: str
    preferred_locale: str
    is_active: bool
    last_login_at: WireDateTime | None = None
    created_at: WireDateTime
    roles: list[RoleKey] = []
    active_org_id: WireUUID | None = None


class OrganizationOut(ORMModel):
    id: WireUUID
    tenant_id: WireUUID
    name: str
    slug: str
    legal_name: str | None = None
    country: str | None = None
    timezone: str
    currency: str
    contact_email: str | None = None
    phone: str | None = None
    address: Address | None = None
    status: Literal["active", "suspended"]
    created_at: WireDateTime


class TokenPair(ORMModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int | None = None


class RegisterResponse(TokenPair):
    user: UserOut | None = None


class LoginInput(ORMModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class OrganizationInput(ORMModel):
    name: str = Field(min_length=2, max_length=200)
    slug: str | None = Field(default=None, min_length=2, max_length=64)
    legal_name: str | None = Field(default=None, max_length=200)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    timezone: str = "UTC"
    currency: str = Field(default="USD", min_length=3, max_length=3)
    contact_email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=32)
    address: Address | None = None

    @field_validator("currency", mode="before")
    @classmethod
    def _upper_currency(cls, v: str) -> str:
        return v.upper() if isinstance(v, str) else v

    @field_validator("country", mode="before")
    @classmethod
    def _upper_country(cls, v: str | None) -> str | None:
        return v.upper() if isinstance(v, str) else v

    @field_validator("slug")
    @classmethod
    def _slug_shape(cls, v: str | None) -> str | None:
        if v is None:
            return v
        allowed = set("abcdefghijklmnopqrstuvwxyz0123456789-")
        if not all(c in allowed for c in v) or v.startswith("-") or v.endswith("-"):
            raise ValueError("slug may contain only lowercase letters, digits and hyphens")
        return v


class RegisterCustomerInput(ORMModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)
    full_name: str = Field(min_length=2, max_length=200)
    phone: str | None = Field(default=None, max_length=32)
    preferred_locale: str = Field(default="en", max_length=8)

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email(cls, v: str) -> str:
        # citext is unavailable on this Postgres build, so §5.1 normalises in the app.
        return v.lower().strip() if isinstance(v, str) else v


class RegisterProviderInput(RegisterCustomerInput):
    account_type: Literal["provider"]
    organization: OrganizationInput


class LogoutInput(ORMModel):
    refresh_token: str | None = None


class UserPatch(ORMModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=200)
    preferred_locale: str | None = Field(default=None, max_length=8)
    phone: str | None = Field(default=None, max_length=32)


class OrgStaffOut(ORMModel):
    id: WireUUID
    org_id: WireUUID
    user_id: WireUUID
    role: OrgStaffRole
    status: Literal["active", "invited", "revoked"]
    created_at: WireDateTime


class OrgStaffInput(ORMModel):
    email: EmailStr
    role: OrgStaffRole = "staff"

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email(cls, v: str) -> str:
        return v.lower().strip() if isinstance(v, str) else v


class AdminUserRow(ORMModel):
    id: WireUUID
    email: str
    full_name: str
    roles: list[RoleKey] = []
    is_active: bool
    created_at: WireDateTime
    last_login_at: WireDateTime | None = None
