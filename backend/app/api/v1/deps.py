"""Shared API plumbing: pagination envelope (§2), actor scope (§4), idempotent writes (§5.7)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Any, Awaitable, Callable

from fastapi import Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse

from app.core.deps import SessionDep
from app.core.errors import AppError, RangeMismatch, ValidationFailed
from app.models.booking import Booking
from app.models.capacity import CapacityDefinition, CapacityResource
from app.models.commerce import Order
from app.models.crosscut import Review
from app.models.identity import Organization, User
from app.models.marketplace import Offer
from app.schemas.booking import BookingOut
from app.schemas.commerce import FulfillmentOut, OrderOut
from app.schemas.marketplace import OfferOut
from app.services import auth as auth_svc
from app.services import commerce
from app.services import idempotency as idem

MAX_LIMIT = 100


@dataclass(frozen=True)
class Page:
    limit: int
    offset: int

    def slice(self) -> tuple[int, int]:
        return self.limit, self.offset


def page_params(limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 20,
                offset: Annotated[int, Query(ge=0)] = 0) -> Page:
    """§2 lists: `limit` default 20 max 100, `offset` default 0."""
    return Page(limit=min(limit, MAX_LIMIT), offset=offset)


PageDep = Annotated[Page, Depends(page_params)]


def envelope(items: list[Any], total: int, page: Page) -> dict[str, Any]:
    return {"items": items, "total": total, "limit": page.limit, "offset": page.offset}


@dataclass
class Actor:
    """The caller's identity plus everything tenancy checks need (§4)."""

    user_id: uuid.UUID
    email: str
    full_name: str
    roles: set[str]
    org_ids: set[uuid.UUID] = field(default_factory=set)
    active_org_id: uuid.UUID | None = None

    @property
    def is_platform_admin(self) -> bool:
        return "platform_admin" in self.roles

    @property
    def is_support(self) -> bool:
        return "support" in self.roles or self.is_platform_admin

    def can_manage_org(self, org_id: uuid.UUID | None) -> bool:
        return self.is_platform_admin or (org_id is not None and org_id in self.org_ids)

    def require_org(self, org_id: uuid.UUID | None) -> uuid.UUID:
        if org_id is None:
            raise ValidationFailed("This action needs an active organization")
        return org_id


async def get_actor(request: Request, session: SessionDep) -> Actor:
    """Build the actor scope from the verified JWT plus fresh DB state.

    Roles come from the token (that is what the caller authenticated as) but org
    membership is re-read, so revoking staff access takes effect on the next request
    rather than when the 30-minute access token happens to expire.
    """
    from app.core.deps import current_user

    user = await current_user(request, session, request.app.state.settings)
    roles = set(getattr(request.state, "current_roles", []) or [])
    org_ids = await auth_svc.get_user_org_ids(session, user.id)
    active = getattr(request.state, "current_org_id", None)
    if active is None:
        active = getattr(user, "active_org_id", None) or (next(iter(org_ids)) if org_ids else None)
    return Actor(user_id=user.id, email=user.email, full_name=user.full_name, roles=roles,
                 org_ids=org_ids, active_org_id=active)


ActorDep = Annotated[Actor, Depends(get_actor)]


def idempotency_key(request: Request) -> str | None:
    return idem.require_key_or_none(request.headers.get("Idempotency-Key"))


class AttrRow:
    """Attribute bag, so a computed row can be validated by the same DTO as an ORM entity."""

    def __init__(self, **fields: Any) -> None:
        self.__dict__.update(fields)


def parse_bound(value: str, *, end: bool = False) -> datetime:
    """Accept `YYYY-MM-DD` or a full ISO-8601 timestamp; a bare date means [00:00, next 00:00)."""
    raw = value.strip()
    try:
        if len(raw) <= 10:
            day = date.fromisoformat(raw)
            dt = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
            return dt + timedelta(days=1) if end else dt
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RangeMismatch(f"Invalid date or timestamp: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_window(date_from: str, date_to: str, *, max_days: int) -> tuple[datetime, datetime]:
    start, end = parse_bound(date_from), parse_bound(date_to, end=True)
    if end <= start:
        raise RangeMismatch("`to` must be after `from`")
    if (end - start) > timedelta(days=max_days):
        raise RangeMismatch(f"Query window is limited to {max_days} days")
    return start, end


async def idempotent_write(
    session: AsyncSession,
    request: Request,
    *,
    route: str,
    actor_id: uuid.UUID,
    payload: Any,
    status_code: int,
    build: Callable[[], Awaitable[dict[str, Any]]],
) -> JSONResponse:
    """Run `build` at most once per (user, route, key) and replay the stored response.

    On any failure the claim is released, so a client retrying after a 409 is not
    locked out by its own earlier attempt (§5.7).
    """
    claim = await idem.claim(session, user_id=actor_id, route=route,
                             key=idempotency_key(request), payload=payload)
    if claim is not None and claim.replayed:
        return JSONResponse(status_code=claim.replay_status or status_code,
                            content=claim.replay_body)
    try:
        body = await build()
    except AppError:
        # Controlled domain failures leave the transaction usable; unexpected DB errors
        # do not, and their rollback already discards the claim.
        await idem.release(session, claim)
        raise
    await idem.record(session, claim, status=status_code, body=body)
    return JSONResponse(status_code=status_code, content=body)


# ---------------------------------------------------------------- read models


async def booking_payload(session: AsyncSession, booking: Booking) -> dict[str, Any]:
    """Booking plus the joined context the UI needs in one round trip (§8).

    The dashboard, the booking list and the detail view all render the same card, so the
    join lives here rather than in each router.
    """
    offer = await session.get(Offer, booking.offer_id)
    order = await commerce.order_for_booking(session, booking.id)
    fulfillment = await commerce.fulfillment_for_booking(session, booking.id)
    definition = await session.get(CapacityDefinition, booking.definition_id)
    resource = await session.get(CapacityResource, definition.resource_id) if definition else None
    org = await session.get(Organization, booking.org_id)
    customer = await session.get(User, booking.customer_id)
    reviewed = (await session.execute(
        select(func.count()).select_from(Review).where(Review.booking_id == booking.id)
    )).scalar_one()

    data = BookingOut.model_validate(booking).model_dump(mode="json")
    data["order_id"] = str(order.id) if order else None
    data["offer"] = OfferOut.model_validate(offer).model_dump(mode="json") if offer else None
    data["order"] = OrderOut.model_validate(order).model_dump(mode="json") if order else None
    data["fulfillment"] = (FulfillmentOut.model_validate(fulfillment).model_dump(mode="json")
                           if fulfillment else None)
    data["offer_title"] = offer.title if offer else None
    data["org_name"] = org.name if org else None
    data["resource_name"] = resource.name if resource else None
    data["customer_name"] = customer.full_name if customer else None
    data["review_submitted"] = reviewed > 0
    return data


async def order_payload(session: AsyncSession, order: Order) -> dict[str, Any]:
    data = OrderOut.model_validate(order).model_dump(mode="json")
    org = await session.get(Organization, order.provider_org_id)
    data["provider_org_name"] = org.name if org else None
    return data
