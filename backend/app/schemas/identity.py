"""§5.1 identity DTOs: users, organizations, token pairs, register/login payloads."""
from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.schemas.common import Address, EmailField, ORMModel, WireDateTime, WireUUID

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
    email: EmailField
    password: str = Field(min_length=1, max_length=256)


class RefreshInput(ORMModel):
    refresh_token: str = Field(min_length=16, max_length=512)


class OrganizationInput(ORMModel):
    name: str = Field(min_length=2, max_length=200)
    slug: str | None = Field(default=None, min_length=2, max_length=64)
    legal_name: str | None = Field(default=None, max_length=200)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    timezone: str = "UTC"
    currency: str = Field(default="USD", min_length=3, max_length=3)
    contact_email: EmailField | None = None
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


class RegisterInput(ORMModel):
    """One public register endpoint for both account types (§3).

    `account_type` is explicit from the desktop client; the E2E harness and any client
    that only sends `organization` still means provider, so the presence of an
    organization infers it rather than silently creating an org-less provider.
    """

    email: EmailField
    password: str = Field(min_length=8, max_length=256)
    full_name: str = Field(min_length=2, max_length=200)
    phone: str | None = Field(default=None, max_length=32)
    preferred_locale: str = Field(default="en", max_length=8)
    account_type: Literal["customer", "provider"] | None = None
    organization: OrganizationInput | None = None

    @model_validator(mode="after")
    def _infer_account_type(self) -> "RegisterInput":
        if self.account_type is None:
            self.account_type = "provider" if self.organization is not None else "customer"
        return self

    @property
    def is_provider(self) -> bool:
        return self.account_type == "provider"


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
    email: EmailField
    role: OrgStaffRole = "staff"

class AdminUserRow(ORMModel):
    id: WireUUID
    email: str
    full_name: str
    roles: list[RoleKey] = []
    is_active: bool
    created_at: WireDateTime
    last_login_at: WireDateTime | None = None
