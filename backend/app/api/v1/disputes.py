"""`/api/v1/disputes` — trust & safety remediation over a booking (CONTRACTS §5.7/§8)."""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.api.v1.deps import ActorDep, PageDep, envelope
from app.core.deps import SessionDep
from app.core.errors import OwnershipRequired, ValidationFailed
from app.models.crosscut import DISPUTE_KINDS, DISPUTE_STATUSES
from app.schemas.commerce import DisputeCreateInput, DisputeOut, DisputeResolveInput
from app.services import booking as booking_svc
from app.services import disputes as svc

router = APIRouter(prefix="/disputes", tags=["disputes"])


def _payload(row) -> dict:
    return DisputeOut.model_validate(svc.row_to_dict(row)).model_dump(mode="json")


@router.post("", status_code=201)
async def open_dispute(body: DisputeCreateInput, request: Request, session: SessionDep,
                       actor: ActorDep) -> dict:
    """Either party of a live or finished booking can raise a complaint."""
    booking = await booking_svc.get_for_actor(
        session, body.booking_id, user_id=actor.user_id, org_ids=actor.org_ids,
        is_platform_admin=actor.is_platform_admin)
    if not (booking.customer_id == actor.user_id or actor.can_manage_org(booking.org_id)):
        raise OwnershipRequired("Only the customer or the providing organization can dispute")
    dispute = await svc.open_dispute(session, booking=booking, kind=body.kind,
                                     description=body.description,
                                     complainant_id=actor.user_id, request=request)
    return _payload(await svc.load_row(session, dispute.id))


@router.get("")
async def list_disputes(session: SessionDep, actor: ActorDep, page: PageDep,
                        provider: bool = False,
                        status: Annotated[str | None, Query(max_length=32)] = None,
                        kind: Annotated[str | None, Query(max_length=24)] = None,
                        org_id: uuid.UUID | None = None) -> dict:
    """Support/admin see the queue, providers their own org, customers their own bookings."""
    if status is not None and status not in DISPUTE_STATUSES:
        raise ValidationFailed("Unknown dispute status",
                               details={"allowed": list(DISPUTE_STATUSES)})
    if kind is not None and kind not in DISPUTE_KINDS:
        raise ValidationFailed("Unknown dispute kind",
                               details={"allowed": list(DISPUTE_KINDS)})
    items, total = await svc.list_disputes(
        session, user_id=actor.user_id, org_ids=actor.org_ids,
        is_platform_admin=actor.is_platform_admin, support=actor.is_support,
        provider=provider, status=status, kind=kind, org_id=org_id,
        limit=page.limit, offset=page.offset)
    return envelope(items, int(total), page)


@router.get("/{dispute_id}")
async def get_dispute(dispute_id: uuid.UUID, session: SessionDep, actor: ActorDep) -> dict:
    return _payload(await svc.get_row_for_actor(
        session, dispute_id, user_id=actor.user_id, org_ids=actor.org_ids,
        is_platform_admin=actor.is_platform_admin))


@router.post("/{dispute_id}/resolve")
async def resolve_dispute(dispute_id: uuid.UUID, body: DisputeResolveInput, request: Request,
                          session: SessionDep, actor: ActorDep) -> dict:
    """A support decision; a refund it names is executed against the order, not pretended."""
    if not actor.is_support:
        raise OwnershipRequired("Only support or a platform administrator can resolve disputes")
    dispute = (await svc.load_row(session, dispute_id)).Dispute
    _, refunded = await svc.resolve(session, dispute, status=body.status,
                                   resolution_note=body.resolution_note,
                                   refund_cents=body.refund_cents,
                                   actor_user_id=actor.user_id, request=request)
    payload = _payload(await svc.load_row(session, dispute_id))
    payload["refunded_cents"] = refunded
    return payload
