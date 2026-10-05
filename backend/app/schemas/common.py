"""Shared DTO plumbing: ORM base model, §2 list envelope, and §1 wire formats.

CONTRACTS §1 fixes the wire representation — UUID strings, ISO-8601 UTC with a literal
`Z`, lowercase enums, money as integer minor units — so serialisation is centralised here
rather than repeated per schema.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Annotated, Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, PlainSerializer

T = TypeVar("T")


def _fmt_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _fmt_date(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _fmt_time(value: time | None) -> str | None:
    return value.isoformat() if value is not None else None


WireDateTime = Annotated[datetime, PlainSerializer(_fmt_dt, return_type=str, when_used="json")]
WireDate = Annotated[date, PlainSerializer(_fmt_date, return_type=str, when_used="json")]
WireTime = Annotated[time, PlainSerializer(_fmt_time, return_type=str, when_used="json")]
WireUUID = Annotated[UUID, PlainSerializer(str, return_type=str, when_used="json")]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, str_strip_whitespace=True)


class InputModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class ListEnvelope(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


def envelope(items: list[T], total: int, limit: int, offset: int) -> ListEnvelope[T]:
    return ListEnvelope(items=items, total=total, limit=limit, offset=offset)


class PageParams(BaseModel):
    """`limit` clamped to 100 per §2; oversized requests are narrowed, never rejected."""

    limit: int = 20
    offset: int = 0

    @property
    def clamped_limit(self) -> int:
        return max(1, min(100, self.limit))

    @property
    def safe_offset(self) -> int:
        return max(0, self.offset)


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
