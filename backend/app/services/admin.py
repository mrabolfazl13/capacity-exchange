"""§8 admin read models: the platform-wide queues an operator works out of.

These lists are deliberately unscoped — that is what makes them admin routes — so the role
gate lives in the router and nothing here takes an actor's organization set. Writes audit
themselves through `record_audit`, the same trail the customer-facing surfaces feed.

Money means the same thing here as on the provider dashboard: the payment-status sets are
imported from that service instead of being restated, so the two screens cannot drift apart.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.models.booking import Booking
from app.models.capacity import CapacityCategory, CapacityResource
from app.models.commerce import Order
from app.models.crosscut import AuditLog, CouponRedemption, Dispute, Promotion, Review
from app.models.identity import Organization, OrgStaff, ROLE_KEYS, User, UserRole
from app.models.marketplace import Offer
from app.services import commerce
from app.services import disputes as dispute_svc
from app.services.dashboard import PAID_PAYMENT_STATUSES, SETTLED_PAYMENT_STATUSES

TOP_CATEGORY_LIMIT = 8
#: §4: `customer` is implicit on every account, so it is not something an admin grants and
#: not a segment an operator can filter on — filtering by it would match every row.
GRANTABLE_ROLES = tuple(role for role in ROLE_KEYS if role != "customer")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _page(session: AsyncSession, stmt: Select,
                limit: int, offset: int) -> tuple[list[Any], int]:
    total = (await session.execute(
        select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()
    rows = (await session.execute(stmt.limit(limit).offset(offset))).all()
    return list(rows), int(total)


def _pattern(term: str) -> str:
    return f"%{term.strip()}%"


# --------------------------------------------------------------------------- users


async def list_users(session: AsyncSession, *, q: str | None = None,
                     role: str | None = None, is_active: bool | None = None,
                     limit: int = 20, offset: int = 0) -> tuple[list[dict], int]:
    """Who signed up, what they are allowed to do, and whose organizations they belong to."""
    stmt = select(User)
    if q:
        pattern = _pattern(q)
        stmt = stmt.where(or_(User.email.ilike(pattern), User.full_name.ilike(pattern)))
    if is_active is not None:
        stmt = stmt.where(User.is_active.is_(is_active))
    if role:
        stmt = stmt.where(User.id.in_(select(UserRole.user_id).where(UserRole.role == role)))
    rows, total = await _page(session, stmt.order_by(User.created_at.desc(), User.id),
                              limit, offset)
    users = [row.User for row in rows]
    ids = [user.id for user in users]
    if not ids:
        return [], total

    grants = (await session.execute(
        select(UserRole.user_id, UserRole.role).where(UserRole.user_id.in_(ids)))).all()
    roles: dict[uuid.UUID, set[str]] = {user_id: set() for user_id in ids}
    for user_id, value in grants:
        roles[user_id].add(value)

    memberships = (await session.execute(
        select(OrgStaff.user_id, Organization.name)
        .join(Organization, Organization.id == OrgStaff.org_id)
        .where(OrgStaff.user_id.in_(ids), OrgStaff.status == "active")
        .order_by(OrgStaff.created_at))).all()
    org_names: dict[uuid.UUID, list[str]] = {user_id: [] for user_id in ids}
    for user_id, name in memberships:
        org_names[user_id].append(name)

    items = [
        {"id": user.id, "email": user.email, "full_name": user.full_name,
         # §4: `customer` is implicit and never stored, same as the token carries it.
         "roles": sorted(roles[user.id] | {"customer"}), "is_active": user.is_active,
         "created_at": user.created_at, "last_login_at": user.last_login_at,
         "org_names": org_names[user.id]}
        for user in users
    ]
    return items, total


# ----------------------------------------------------------------------- providers


async def list_providers(session: AsyncSession, *, q: str | None = None,
                         status: str | None = None, country: str | None = None,
                         limit: int = 20, offset: int = 0) -> tuple[list[dict], int]:
    """The supply side, with the counts that make each profile triageable in one table."""
    stmt = select(Organization)
    if q:
        pattern = _pattern(q)
        # The search covers what the row shows, including the owner's login, which is not a
        # column of the organization itself.
        owners = (select(OrgStaff.org_id).join(User, User.id == OrgStaff.user_id)
                  .where(OrgStaff.role == "org_admin", OrgStaff.status == "active",
                         User.email.ilike(pattern)))
        stmt = stmt.where(or_(Organization.name.ilike(pattern),
                              Organization.slug.ilike(pattern),
                              Organization.contact_email.ilike(pattern),
                              Organization.id.in_(owners)))
    if status:
        stmt = stmt.where(Organization.status == status)
    if country:
        stmt = stmt.where(Organization.country == country.upper())
    rows, total = await _page(
        session, stmt.order_by(Organization.created_at.desc(), Organization.id), limit, offset)
    orgs = [row.Organization for row in rows]
    ids = [org.id for org in orgs]
    if not ids:
        return [], total

    async def _count_by(column) -> dict[uuid.UUID, int]:
        return dict((await session.execute(
            select(column, func.count()).where(column.in_(ids)).group_by(column)
        )).all())

    offers = await _count_by(Offer.org_id)
    bookings = await _count_by(Booking.org_id)
    gmv = dict((await session.execute(
        select(Order.provider_org_id,
               func.coalesce(func.sum(Order.total_cents - Order.refunded_cents), 0))
        .where(Order.provider_org_id.in_(ids),
               Order.payment_status.in_(PAID_PAYMENT_STATUSES))
        .group_by(Order.provider_org_id))).all())
    queue = dict((await session.execute(
        select(Dispute.org_id, func.count())
        .where(Dispute.org_id.in_(ids), Dispute.status.in_(dispute_svc.OPEN_STATUSES))
        .group_by(Dispute.org_id))).all())

    rated = (await session.execute(
        select(Review.org_id, func.avg(Review.rating), func.count())
        .where(Review.org_id.in_(ids), Review.status == "published")
        .group_by(Review.org_id))).all()
    ratings = {org_id: (round(float(avg), 2), int(count)) for org_id, avg, count in rated}

    owners = (await session.execute(
        select(OrgStaff.org_id, User.email)
        .join(User, User.id == OrgStaff.user_id)
        .where(OrgStaff.org_id.in_(ids), OrgStaff.role == "org_admin",
               OrgStaff.status == "active")
        .order_by(OrgStaff.created_at))).all()
    owner: dict[uuid.UUID, str] = {}
    for org_id, email in owners:
        owner.setdefault(org_id, email)

    items = [
        {"id": org.id, "name": org.name, "slug": org.slug, "status": org.status,
         "created_at": org.created_at, "org_admin_email": owner.get(org.id),
         "country": org.country, "currency": org.currency, "timezone": org.timezone,
         "offer_count": int(offers.get(org.id, 0)),
         "booking_count": int(bookings.get(org.id, 0)),
         "gmv_cents": int(gmv.get(org.id, 0)),
         "rating_avg": (ratings.get(org.id) or (None, 0))[0],
         "rating_count": (ratings.get(org.id) or (None, 0))[1],
         "open_disputes": int(queue.get(org.id, 0))}
        for org in orgs
    ]
    return items, total


# ------------------------------------------------------------------------ disputes


async def dispute_queue(session: AsyncSession, *, actor_user_id: uuid.UUID,
                        status: str | None = None,
                        kind: str | None = None, org_id: uuid.UUID | None = None,
                        oldest_first: bool = False,
                        limit: int = 20, offset: int = 0) -> tuple[list[dict], int]:
    """The support queue with the decision context on it: the service, the money, the age."""
    items, total = await dispute_svc.list_disputes(
        session, user_id=actor_user_id, org_ids=set(), is_platform_admin=True,
        support=True, status=status, kind=kind, org_id=org_id, limit=limit, offset=offset)
    if oldest_first:
        items.reverse()
    booking_ids = {item["booking_id"] for item in items}
    if not booking_ids:
        return items, total

    rows = (await session.execute(
        select(Booking.id, Booking.status, Booking.window_start, Booking.window_end,
               Offer.title, User.full_name, User.email,
               Order.total_cents, Order.refunded_cents, Order.payment_status)
        .join(Offer, Offer.id == Booking.offer_id)
        .join(User, User.id == Booking.customer_id)
        .outerjoin(Order, Order.booking_id == Booking.id)
        .where(Booking.id.in_(booking_ids)))).all()
    by_booking = {row.id: row for row in rows}

    now = utcnow()
    for item in items:
        row = by_booking.get(item["booking_id"])
        if row is None:  # the booking was hard-deleted under a dispute that outlived it
            continue
        item.update({
            "booking_status": row.status, "window_start": row.window_start,
            "window_end": row.window_end, "offer_title": row.title,
            "customer_name": row.full_name, "customer_email": row.email,
            "order_total_cents": row.total_cents, "order_refunded_cents": row.refunded_cents,
            "order_payment_status": row.payment_status,
            "age_hours": round((now - item["created_at"]).total_seconds() / 3600, 1),
        })
    return items, total


# ----------------------------------------------------------------------- audit log


async def audit_rows(session: AsyncSession, *, action: str | None = None,
                     entity_type: str | None = None, entity_id: uuid.UUID | None = None,
                     actor_user_id: uuid.UUID | None = None, org_id: uuid.UUID | None = None,
                     start: datetime | None = None, end: datetime | None = None,
                     limit: int = 20, offset: int = 0) -> tuple[list[AuditLog], int]:
    """Append-only trail (§5.1); the filters are exact because `action` is a keyword, not prose."""
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if actor_user_id:
        stmt = stmt.where(AuditLog.actor_user_id == actor_user_id)
    if org_id:
        stmt = stmt.where(AuditLog.actor_org_id == org_id)
    if start:
        stmt = stmt.where(AuditLog.created_at >= start)
    if end:
        stmt = stmt.where(AuditLog.created_at < end)
    total = (await session.execute(
        select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = list((await session.execute(
        stmt.order_by(AuditLog.created_at.desc(), AuditLog.id)
        .limit(limit).offset(offset))).scalars().all())
    return rows, int(total)


# ----------------------------------------------------------------------- analytics


async def _counts(session: AsyncSession, model, column, start: datetime,
                  end: datetime, *conditions) -> dict[str, int]:
    """One day-truncated series, keyed by ISO date, for the window."""
    stmt = (select(func.date(column), func.coalesce(func.count(), 0))
            .select_from(model)
            .where(column >= start, column < end, *conditions)
            .group_by(func.date(column)))
    return {str(day): int(value) for day, value in (await session.execute(stmt)).all()}


async def _money_by_day(session: AsyncSession, column, start: datetime, end: datetime,
                     *conditions) -> dict[str, int]:
    stmt = (select(func.date(Order.created_at),
                   func.coalesce(func.sum(column), 0))
            .where(Order.created_at >= start, Order.created_at < end, *conditions)
            .group_by(func.date(Order.created_at)))
    return {str(day): int(value or 0) for day, value in (await session.execute(stmt)).all()}


def _series(start: datetime, end: datetime, *keys: tuple[str, dict[str, int]]) -> list[dict]:
    """Zero-filled, so a quiet day is a zero on the chart and not a hole in it.

    The last day is the one that actually contains `end`: a default trailing window stops at
    *now*, so its own day has to be on the chart or the freshest activity would vanish.
    """
    last = (end - timedelta(microseconds=1)).date()
    days, cursor = [], start.date()
    while cursor <= last:
        row = {"date": cursor.isoformat()}
        for name, values in keys:
            row[name] = values.get(cursor.isoformat(), 0)
        days.append(row)
        cursor += timedelta(days=1)
    return days


async def platform_analytics(session: AsyncSession, *, start: datetime,
                             end: datetime) -> dict[str, Any]:
    """What happened on the platform in the window, each line labelled with its own clock."""
    async def _count(model, column) -> int:
        return int((await session.execute(
            select(func.count()).select_from(model)
            .where(column >= start, column < end))).scalar_one())

    settled = Order.payment_status.in_(SETTLED_PAYMENT_STATUSES)
    paid = Order.payment_status.in_(PAID_PAYMENT_STATUSES)
    money = (await session.execute(
        select(func.coalesce(func.sum(Order.total_cents - Order.refunded_cents), 0),
               func.coalesce(func.sum(Order.commission_cents), 0),
               func.coalesce(func.sum(Order.refunded_cents), 0), func.count())
        .where(settled, Order.created_at >= start, Order.created_at < end))).one()
    rated = (await session.execute(
        select(func.avg(Review.rating), func.count())
        .where(Review.status == "published", Review.created_at >= start,
               Review.created_at < end))).one()

    booked = dict((await session.execute(
        select(CapacityCategory.id, func.count())
        .select_from(Booking)
        .join(Offer, Offer.id == Booking.offer_id)
        .join(CapacityResource, CapacityResource.id == Offer.resource_id)
        .join(CapacityCategory, CapacityCategory.id == CapacityResource.category_id)
        .where(Booking.created_at >= start, Booking.created_at < end)
        .group_by(CapacityCategory.id))).all())
    earned = dict((await session.execute(
        select(CapacityCategory.id,
               func.coalesce(func.sum(Order.total_cents - Order.refunded_cents), 0))
        .select_from(Order)
        .join(Booking, Booking.id == Order.booking_id)
        .join(Offer, Offer.id == Booking.offer_id)
        .join(CapacityResource, CapacityResource.id == Offer.resource_id)
        .join(CapacityCategory, CapacityCategory.id == CapacityResource.category_id)
        .where(paid, Order.created_at >= start, Order.created_at < end)
        .group_by(CapacityCategory.id))).all())
    category_ids = set(booked) | set(earned)
    labels = dict((await session.execute(
        select(CapacityCategory.id, CapacityCategory.label)
        .where(CapacityCategory.id.in_(category_ids)))).all()) if category_ids else {}
    categories = [
        {"category_id": category_id, "label": labels.get(category_id, ""),
         "bookings": int(booked.get(category_id, 0)),
         "revenue_cents": int(earned.get(category_id, 0))}
        for category_id in category_ids
    ]
    categories.sort(key=lambda r: (-r["revenue_cents"], -r["bookings"], r["label"]))

    return {
        "from": start, "to": end,
        "users_created": await _count(User, User.created_at),
        "providers_created": await _count(Organization, Organization.created_at),
        "offers_created": await _count(Offer, Offer.created_at),
        "bookings_created": await _count(Booking, Booking.created_at),
        "bookings_completed": await _count(Booking, Booking.completed_at),
        "bookings_cancelled": await _count(Booking, Booking.cancelled_at),
        "orders_placed": int(money[3]),
        "gmv_cents": int(money[0]),
        "commission_cents": int(money[1]),
        "refunded_cents": int(money[2]),
        "reviews_published": int(rated[1]),
        "rating_avg": round(float(rated[0]), 2) if rated[1] else None,
        "disputes_opened": await _count(Dispute, Dispute.created_at),
        "disputes_resolved": await _count(Dispute, Dispute.resolved_at),
        "top_categories": categories[:TOP_CATEGORY_LIMIT],
        "daily": _series(start, end,
                         ("bookings_created", await _counts(session, Booking,
                                                            Booking.created_at, start, end)),
                         ("orders_placed", await _counts(session, Order, Order.created_at,
                                                         start, end, settled)),
                         ("gmv_cents", await _money_by_day(session, Order.total_cents - Order.refunded_cents,
                                                   start, end, paid))),
    }


# -------------------------------------------------------------------------- roles


async def load_user(session: AsyncSession, user_id: uuid.UUID) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise NotFound("User not found")
    return user


async def list_roles(session: AsyncSession, user_id: uuid.UUID, *, limit: int = 20,
                     offset: int = 0) -> tuple[list[dict], int]:
    total = (await session.execute(
        select(func.count()).select_from(UserRole).where(UserRole.user_id == user_id)
    )).scalar_one()
    rows = (await session.execute(
        select(UserRole).where(UserRole.user_id == user_id)
        .order_by(UserRole.created_at, UserRole.role).limit(limit).offset(offset)
    )).scalars().all()
    return [{"user_id": row.user_id, "role": row.role, "granted_by": row.granted_by,
             "created_at": row.created_at} for row in rows], int(total)


async def grant_role(session: AsyncSession, user: User, role: str, *,
                     actor_user_id: uuid.UUID, request=None) -> UserRole:
    """A global role is the platform's own grant; staff inside an organization is (§4)."""
    if role not in GRANTABLE_ROLES:
        raise ValidationFailed("Role cannot be granted",
                               details={"allowed": list(GRANTABLE_ROLES)})
    if await session.get(UserRole, (user.id, role)) is not None:
        raise Conflict(f"{role} is already granted to this user")
    grant = UserRole(user_id=user.id, role=role, granted_by=actor_user_id)
    session.add(grant)
    await session.flush()
    await record_audit(session, request, action="admin.role_granted", entity_type="user",
                       entity_id=user.id, after={"role": role}, actor_user_id=actor_user_id)
    return grant


async def revoke_role(session: AsyncSession, user: User, role: str, *,
                      actor_user_id: uuid.UUID, request=None) -> None:
    grant = await session.get(UserRole, (user.id, role))
    if grant is None:
        raise NotFound(f"{role} is not granted to this user")
    if role == "platform_admin":
        # Losing every administrator would lock the console with no way back in.
        remaining = (await session.execute(
            select(func.count()).select_from(UserRole)
            .where(UserRole.role == "platform_admin", UserRole.user_id != user.id)
        )).scalar_one()
        if remaining == 0:
            raise Conflict("The platform must keep at least one administrator")
    await session.delete(grant)
    await session.flush()
    await record_audit(session, request, action="admin.role_revoked", entity_type="user",
                       entity_id=user.id, before={"role": role}, actor_user_id=actor_user_id)


# --------------------------------------------------------------------- promotions


def _check_scope(applies: dict) -> None:
    """A scope filter that `commerce` cannot evaluate, or that cannot match an id, would turn
    a coupon into money the operator thinks is off but the order accepts."""
    unknown = set(applies) - set(commerce.COUPON_SCOPE_KEYS)
    if unknown:
        raise ValidationFailed("Unknown coupon scope filter",
                               details={"unknown": sorted(unknown),
                                        "allowed": list(commerce.COUPON_SCOPE_KEYS)})
    for key, wanted in applies.items():
        try:
            uuid.UUID(str(wanted))
        except ValueError:
            raise ValidationFailed(f"{key} must be an id",
                                   details={key: wanted}) from None


def promotion_to_dict(promo: Promotion, *, created_by_name: str | None = None) -> dict:
    return {
        "id": promo.id, "name": promo.name, "kind": promo.kind, "code": promo.code,
        "discount_config": promo.discount_config, "min_order_cents": promo.min_order_cents,
        "applies_to": promo.applies_to, "usage_limit": promo.usage_limit,
        "used_count": promo.used_count, "per_user_limit": promo.per_user_limit,
        "starts_at": promo.starts_at, "ends_at": promo.ends_at, "status": promo.status,
        "created_by": promo.created_by, "created_at": promo.created_at,
        "created_by_name": created_by_name,
    }


async def list_promotions(session: AsyncSession, *, q: str | None = None,
                          status: str | None = None, kind: str | None = None,
                          limit: int = 20,
                          offset: int = 0) -> tuple[list[dict], int]:
    stmt = (select(Promotion, User.full_name.label("created_by_name"))
            .outerjoin(User, User.id == Promotion.created_by))
    if q:
        pattern = _pattern(q)
        stmt = stmt.where(or_(Promotion.name.ilike(pattern), Promotion.code.ilike(pattern)))
    if status:
        stmt = stmt.where(Promotion.status == status)
    if kind:
        stmt = stmt.where(Promotion.kind == kind)
    rows, total = await _page(session, stmt.order_by(Promotion.created_at.desc(), Promotion.id),
                              limit, offset)
    return [promotion_to_dict(row.Promotion, created_by_name=row.created_by_name)
            for row in rows], total


async def load_promotion(session: AsyncSession, promotion_id: uuid.UUID) -> Promotion:
    promo = await session.get(Promotion, promotion_id)
    if promo is None:
        raise NotFound("Promotion not found")
    return promo


async def create_promotion(session: AsyncSession, values: dict, *, actor_user_id: uuid.UUID,
                           request=None) -> Promotion:
    """Store what the operator wrote; `used_count` only ever moves through a real order."""
    _check_scope(values.get("applies_to") or {})
    code = values.get("code")
    if code is not None:
        taken = (await session.execute(
            select(Promotion.id).where(Promotion.code == code).limit(1))).scalar_one_or_none()
        if taken is not None:
            raise Conflict("Coupon code already exists", details={"code": code})
    promo = Promotion(created_by=actor_user_id, **values)
    session.add(promo)
    await session.flush()
    await record_audit(session, request, action="admin.promotion_created",
                       entity_type="promotion", entity_id=promo.id,
                       after={"code": promo.code, "kind": promo.kind, "status": promo.status,
                              "discount_config": promo.discount_config})
    return promo


def _auditable(value: Any) -> Any:
    """A JSONB column cannot hold a datetime, so the trail keeps the §1 wire form of it."""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return value


async def patch_promotion(session: AsyncSession, promo: Promotion, changes: dict, *,
                          actor_user_id: uuid.UUID, request=None) -> Promotion:
    """Limits and windows may move; the redemption count belongs to the orders, not here."""
    if "applies_to" in changes:
        _check_scope(changes["applies_to"] or {})
    starts = changes.get("starts_at", promo.starts_at)
    ends = changes.get("ends_at", promo.ends_at)
    if ends <= starts:
        raise ValidationFailed("ends_at must be after starts_at")
    limit = changes.get("usage_limit", promo.usage_limit)
    if limit is not None and limit < promo.used_count:
        raise ValidationFailed("usage_limit is below the coupons already redeemed",
                               details={"used_count": promo.used_count})
    if changes.get("status") == "active" and ends <= utcnow():
        raise Conflict("This window has already closed, so nothing could redeem it")
    before = {key: _auditable(getattr(promo, key)) for key in changes}
    after = {key: _auditable(value) for key, value in changes.items()}
    for key, value in changes.items():
        setattr(promo, key, value)
    await session.flush()
    await record_audit(session, request, action="admin.promotion_updated",
                       entity_type="promotion", entity_id=promo.id,
                       before=before, after=after, actor_user_id=actor_user_id)
    return promo


async def promotion_redemptions(session: AsyncSession, promotion_id: uuid.UUID, *,
                                limit: int = 20,
                                offset: int = 0) -> tuple[list[dict], int]:
    """Who actually took the discount, on which order, and what it came off."""
    stmt = (select(CouponRedemption, User.full_name, User.email, Order.number,
                   Order.total_cents)
            .join(User, User.id == CouponRedemption.user_id)
            .outerjoin(Order, Order.id == CouponRedemption.order_id)
            .where(CouponRedemption.promotion_id == promotion_id)
            .order_by(CouponRedemption.redeemed_at.desc(), CouponRedemption.id))
    rows, total = await _page(session, stmt, limit, offset)
    return [
        {"id": row.CouponRedemption.id, "promotion_id": row.CouponRedemption.promotion_id,
         "user_id": row.CouponRedemption.user_id, "order_id": row.CouponRedemption.order_id,
         "discount_cents": row.CouponRedemption.discount_cents,
         "redeemed_at": row.CouponRedemption.redeemed_at, "user_name": row.full_name,
         "user_email": row.email, "order_number": row.number,
         "order_total_cents": row.total_cents}
        for row in rows
    ], total


# ---------------------------------------------------------------------- categories


async def list_categories(session: AsyncSession, *, active_only: bool = False,
                          limit: int = 100,
                          offset: int = 0) -> tuple[list[CapacityCategory], int]:
    """The registry an admin edits: retired rows stay visible so they can be restored."""
    stmt = select(CapacityCategory)
    if active_only:
        stmt = stmt.where(CapacityCategory.is_active.is_(True))
    total = (await session.execute(
        select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = list((await session.execute(
        stmt.order_by(CapacityCategory.key).limit(limit).offset(offset)
    )).scalars().all())
    return rows, int(total)


async def load_category(session: AsyncSession, category_id: uuid.UUID) -> CapacityCategory:
    category = await session.get(CapacityCategory, category_id)
    if category is None:
        raise NotFound("Category not found")
    return category


async def patch_category(session: AsyncSession, category: CapacityCategory, *,
                         label: str | None, is_active: bool | None,
                         actor_user_id: uuid.UUID, request=None) -> CapacityCategory:
    """Retiring a category hides it from search and the wizard; live offers keep their row."""
    changes = {key: value for key, value in (("label", label), ("is_active", is_active))
               if value is not None}
    if not changes:
        raise ValidationFailed("Nothing to change on this category")
    before = {key: getattr(category, key) for key in changes}
    for key, value in changes.items():
        setattr(category, key, value)
    await session.flush()
    await record_audit(session, request, action="admin.category_updated",
                       entity_type="capacity_category", entity_id=category.id,
                       before=before, after=changes, actor_user_id=actor_user_id)
    return category
