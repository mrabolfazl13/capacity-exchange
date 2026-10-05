"""`/api/v1/reviews` + the public review wall of an offer (CONTRACTS §5.7/§8)."""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.api.v1.deps import ActorDep, PageDep, envelope
from app.core.deps import SessionDep
from app.core.errors import OwnershipRequired, ValidationFailed
from app.models.crosscut import REVIEW_STATUSES, Review
from app.schemas.commerce import ReviewInput, ReviewOut, ReviewReplyInput
from app.services import booking as booking_svc
from app.services import marketplace as market
from app.services import reviews as svc

router = APIRouter(tags=["reviews"])


def _payload(row) -> dict:
    return ReviewOut.model_validate(svc.row_to_dict(row)).model_dump(mode="json")


async def _load(session, actor, review_id: uuid.UUID) -> Review:
    """A review is readable by its author, the reviewed org and trust & safety staff."""
    review = await svc.load_review(session, review_id)
    if (actor.is_platform_admin or actor.is_support or review.reviewer_id == actor.user_id
            or review.org_id in actor.org_ids):
        return review
    raise OwnershipRequired("You do not have access to this review")


def _require_admin(actor) -> None:
    if not actor.is_platform_admin:
        raise OwnershipRequired("Only a platform administrator can moderate reviews")


@router.post("/reviews", status_code=201)
async def create_review(body: ReviewInput, request: Request, session: SessionDep,
                        actor: ActorDep) -> dict:
    """The customer rates a completed fulfilment; the offer's average updates with it."""
    booking = await booking_svc.get_for_actor(
        session, body.booking_id, user_id=actor.user_id, org_ids=actor.org_ids,
        is_platform_admin=actor.is_platform_admin)
    if booking.customer_id != actor.user_id:
        raise OwnershipRequired("Only the customer who used the service can review it")
    review = await svc.create_review(session, booking=booking, rating=body.rating,
                                     comment=body.comment, fulfillment_id=body.fulfillment_id,
                                     reviewer_id=actor.user_id, request=request)
    return _payload(await svc.load_row(session, review.id))


@router.get("/reviews")
async def list_reviews(session: SessionDep, actor: ActorDep, page: PageDep,
                       provider: bool = False, offer_id: uuid.UUID | None = None,
                       booking_id: uuid.UUID | None = None,
                       rating: Annotated[int | None, Query(ge=1, le=5)] = None,
                       status: Annotated[str | None, Query(max_length=24)] = None) -> dict:
    """Customer view is what the caller wrote; `provider=true` is what the org received."""
    if status is not None and status not in REVIEW_STATUSES:
        raise ValidationFailed("Unknown review status",
                               details={"allowed": list(REVIEW_STATUSES)})
    items, total = await svc.list_reviews(
        session, user_id=actor.user_id, org_ids=actor.org_ids,
        is_platform_admin=actor.is_platform_admin, provider=provider, offer_id=offer_id,
        booking_id=booking_id, status=status, rating=rating,
        limit=page.limit, offset=page.offset)
    return envelope(items, int(total), page)


@router.get("/reviews/{review_id}")
async def get_review(review_id: uuid.UUID, session: SessionDep, actor: ActorDep) -> dict:
    await _load(session, actor, review_id)
    return _payload(await svc.load_row(session, review_id))


@router.post("/reviews/{review_id}/reply")
async def reply_to_review(review_id: uuid.UUID, body: ReviewReplyInput, request: Request,
                          session: SessionDep, actor: ActorDep) -> dict:
    review = await _load(session, actor, review_id)
    # The author of a review can read it but must not be able to answer as the provider.
    if not actor.can_manage_org(review.org_id):
        raise OwnershipRequired("Only the reviewed organization can reply")
    await svc.reply(session, review, body=body.provider_reply, org_id=review.org_id,
                    actor_user_id=actor.user_id, request=request)
    return _payload(await svc.load_row(session, review_id))


@router.post("/reviews/{review_id}/remove")
async def remove_review(review_id: uuid.UUID, request: Request, session: SessionDep,
                        actor: ActorDep) -> dict:
    """Moderation: the review stops showing and stops counting towards the average."""
    _require_admin(actor)
    review = await svc.load_review(session, review_id)
    await svc.set_status(session, review, "removed", request=request)
    return _payload(await svc.load_row(session, review_id))


@router.post("/reviews/{review_id}/restore")
async def restore_review(review_id: uuid.UUID, request: Request, session: SessionDep,
                         actor: ActorDep) -> dict:
    _require_admin(actor)
    review = await svc.load_review(session, review_id)
    await svc.set_status(session, review, "published", request=request)
    return _payload(await svc.load_row(session, review_id))


@router.get("/offers/{offer_id}/reviews")
async def offer_reviews(offer_id: uuid.UUID, session: SessionDep, actor: ActorDep,
                        page: PageDep) -> dict:
    """The public wall: published reviews only, newest first, provider replies attached."""
    await market.get_offer_row(session, offer_id)
    items, total = await svc.offer_reviews(session, offer_id, limit=page.limit,
                                           offset=page.offset)
    return envelope(items, int(total), page)
