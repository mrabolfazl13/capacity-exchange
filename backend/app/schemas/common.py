"""Shared DTO plumbing: ORM base model, §2 list envelope, and §1 wire formats.

CONTRACTS §1 fixes the wire representation — UUID strings, ISO-8601 UTC with a literal
`Z`, lowercase enums, money as integer minor units — so serialisation is centralised here
rather than repeated per schema.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timezone
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, AfterValidator, PlainSerializer

#: Deliberately accepts reserved/special-use domains: CONTRACTS §9 seeds accounts on
#: `.test` and the E2E harness registers `@e2e.test`, which pydantic's built-in EmailStr
#: rejects. Uniqueness is enforced by the database, not by TLD allow-lists.
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?"
                      r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?)+$")


def normalize_email(value: str) -> str:
    cleaned = value.strip().lower()
    if not EMAIL_RE.match(cleaned):
        raise ValueError("value is not a valid email address")
    return cleaned


EmailField = Annotated[str, AfterValidator(normalize_email)]


def _fmt_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    # §1: ISO-8601 UTC with a literal Z. Second precision keeps one canonical shape for
    # every timestamp on the wire instead of leaking whatever the driver rounded.
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _fmt_date(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _fmt_time(value: time | None) -> str | None:
    return value.isoformat(timespec="seconds") if value is not None else None


WireDateTime = Annotated[datetime, PlainSerializer(_fmt_dt, return_type=str, when_used="json")]
WireDate = Annotated[date, PlainSerializer(_fmt_date, return_type=str, when_used="json")]
WireTime = Annotated[time, PlainSerializer(_fmt_time, return_type=str, when_used="json")]
WireUUID = Annotated[UUID, PlainSerializer(str, return_type=str, when_used="json")]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, str_strip_whitespace=True)


class InputModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class Address(BaseModel):
    line1: str | None = None
    line2: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None


class Photo(BaseModel):
    url: str
    caption: str | None = None
    sort_order: int = 0


class DocumentRef(BaseModel):
    name: str
    url: str
    kind: str | None = None


class CancellationPolicyBand(BaseModel):
    hours_before: int
    refund_pct: int


class OkResponse(ORMModel):
    ok: bool = True
