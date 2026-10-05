"""Reviews (CONTRACTS §5.7/§8): one review per completed fulfilment, plus the provider reply.

Ratings only count while `status == 'published'`, which is what the offer read-model subquery
filters on, so moderating a review to `removed` rewrites the offer's average by itself.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.errors import (
    AlreadyRated,
    Conflict,
    InvalidStateTransition,
    NotFound,
    OwnershipRequired,
    ValidationFailed,
)
from app.models.booking import Booking
from app.models.crosscut import REVIEW_STATUSES, Review
from app.models.identity import User
from app.models.marketplace import Offer
from app.services import commerce
from app.services.notifications import notify, notify_org


def _base_select() -> Select:
    return (
        select(Review, User.full_name.label("reviewer_name"), Offer.title.label("offer_title"))
        .join(User, User.id == Review.reviewer_id)
        .join(Offer, Offer.id == Review.offer_id)
    )


def row_to_dict(row: Any) -> dict:
    review: Review = row.Review
    return {
        "id": review.id, "booking_id": review.booking_id,
        "fulfillment_id": review.fulfillment_id, "offer_id": review.offer_id,
        "org_id": review.org_id, "reviewer_id": review.reviewer_id, "rating": review.rating,
        "comment": review.comment, "provider_reply": review.provider_reply,
        "replied_at": review.replied_at, "status": review.status,
        "created_at": review.created_at, "reviewer_name": row.reviewer_name,
        "offer_title": row.offer_title,
    }


async def review_for_fulfillment(session: AsyncSession,
                                 fulfillment_id: uuid.UUID) -> Review | None:
    return (await session.execute(
        select(Review).where(Review.fulfillment_id == fulfillment_id).limit(1)
    )).scalar_one_or_none()


async def load_row(session: AsyncSession, review_id: uuid.UUID) -> Any:
    row = (await session.execute(_base_select().where(Review.id == review_id))).first()
    if row is None:
        raise NotFound("Review not found")
    return row


async def load_review(session: AsyncSession, review_id: uuid.UUID) -> Review:
    return (await load_row(session, review_id)).Review


async def create_review(session: AsyncSession, *, booking: Booking, rating: int,
                        comment: str | None, reviewer_id: uuid.UUID,
                        fulfillment_id: uuid.UUID | None = None,
                        request: Request | None = None) -> Review:
    """Only the customer who completed a booking may rate it, and only once (§5.7)."""
    if not 1 <= rating <= 5:
        raise ValidationFailed("Rating must be between 1 and 5")
    if booking.status != "completed":
        raise InvalidStateTransition(
            f"A review needs a completed booking, this one is '{booking.status}'")

    fulfillment = await commerce.fulfillment_for_booking(session, booking.id)
    if fulfillment is None:
        raise NotFound("This booking has no fulfilment record to review")
    if fulfillment_id is not None and fulfillment_id != fulfillment.id:
        raise ValidationFailed("fulfillment_id does not belong to this booking")

    existing = await review_for_fulfillment(session, fulfillment.id)
    if existing is not None:
        raise AlreadyRated("This fulfilment has already been reviewed",
                           details={"review_id": str(existing.id)})

    review = Review(booking_id=booking.id, fulfillment_id=fulfillment.id,
                    offer_id=booking.offer_id, org_id=booking.org_id,
                    reviewer_id=reviewer_id, rating=rating,
                    comment=(comment or "").strip()[:4000] or None, status="published")
    session.add(review)
    await session.flush()

    average = (await session.execute(
        select(func.avg(Review.rating)).where(Review.offer_id == booking.offer_id,
                                              Review.status == "published")
    )).scalar_one()
    offer = await session.get(Offer, booking.offer_id)
    await record_audit(session, request, action="review.created", entity_type="review",
                       entity_id=review.id, after={"rating": rating,
                                                    "offer_id": str(booking.offer_id)})
    await notify_org(session, booking.org_id, kind="review.created",
                     title=f"New {rating}-star review",
                     body=f"Your {offer.title if offer else 'offer'} now averages "
                          f"{round(float(average or rating), 2)} stars.",
                     data={"review_id": str(review.id), "offer_id": str(booking.offer_id),
                           "booking_id": str(booking.id)},
                     exclude_user_id=reviewer_id)
    return review


async def reply(session: AsyncSession, review: Review, *, body: str, org_id: uuid.UUID,
                actor_user_id: uuid.UUID, request: Request | None = None) -> Review:
    """The provider answers publicly, once — a reply is part of the review, not a thread."""
    if review.org_id != org_id:
        raise OwnershipRequired("Only the reviewed organization can reply")
    if review.status != "published":
        raise Conflict(f"Cannot reply to a review that is {review.status}")
    if review.provider_reply:
        raise Conflict("This review already has a provider reply")
    review.provider_reply = body.strip()[:4000]
    review.replied_at = commerce.utcnow()
    await session.flush()
    await record_audit(session, request, action="review.replied", entity_type="review",
                       entity_id=review.id, after={"length": len(review.provider_reply)})
    await notify(session, user_id=review.reviewer_id, kind="review.replied",
                 title="A provider replied to your review",
                 body=review.provider_reply[:200], data={"review_id": str(review.id)})
    return review


async def set_status(session: AsyncSession, review: Review, to_status: str, *,
                     request: Request | None = None) -> Review:
    """Moderation: a removed review stops counting towards the offer's rating."""
    if to_status not in REVIEW_STATUSES:
        raise ValidationFailed("Unknown review status",
                               details={"allowed": list(REVIEW_STATUSES)})
    if to_status == review.status:
        raise Conflict(f"Review is already {to_status}")
    before = review.status
    review.status = to_status
    await session.flush()
    await record_audit(session, request, action=f"review.{to_status}", entity_type="review",
                       entity_id=review.id, before={"status": before},
                       after={"status": to_status})
    return review


async def offer_reviews(session: AsyncSession, offer_id: uuid.UUID, *, limit: int = 20,
                        offset: int = 0) -> tuple[list[dict], int]:
    """The public review wall of one offer: published rows only, newest first."""
    stmt = _base_select().where(Review.offer_id == offer_id, Review.status == "published")
    total = (await session.execute(
        select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()
    rows = (await session.execute(stmt.order_by(Review.created_at.desc(), Review.id)
                                  .limit(limit).offset(offset))).all()
    return [row_to_dict(r) for r in rows], int(total)


async def list_reviews(session: AsyncSession, *, user_id: uuid.UUID, org_ids: set[uuid.UUID],
                       is_platform_admin: bool, provider: bool = False,
                       offer_id: uuid.UUID | None = None,
                       booking_id: uuid.UUID | None = None, status: str | None = None,
                       rating: int | None = None, limit: int = 20,
                       offset: int = 0) -> tuple[list[dict], int]:
    """Customer view is what the caller wrote; provider view is what their org received."""
    stmt = _base_select()
    if not is_platform_admin:
        stmt = stmt.where(Review.org_id.in_(org_ids) if provider
                          else Review.reviewer_id == user_id)
    if offer_id is not None:
        stmt = stmt.where(Review.offer_id == offer_id)
    if booking_id is not None:
        stmt = stmt.where(Review.booking_id == booking_id)
    if status:
        stmt = stmt.where(Review.status == status)
    if rating:
        stmt = stmt.where(Review.rating == rating)
    total = (await session.execute(
        select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()
    rows = (await session.execute(stmt.order_by(Review.created_at.desc(), Review.id)
                                  .limit(limit).offset(offset))).all()
    return [row_to_dict(r) for r in rows], int(total)
