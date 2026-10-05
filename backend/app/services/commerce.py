"""Commerce services (CONTRACTS §5.6): orders, payments, webhooks, refunds, commissions, fulfillment.

Money is always integer minor units (§1). The payment provider is `mock` (§11): an intent
is created, confirmation succeeds immediately, and the webhook path is still real — it
validates the HMAC header and replays protection, so a third-party provider can replace
`confirm_payment` without touching the settlement code.

Settlement (`_settle`) is the single place that moves money state: payment succeeded →
order paid → booking confirmed (if it was hold/draft) → fulfillment row → commission row →
notifications. Importing `app.services.booking` is safe at module level because booking.py
only imports commerce lazily inside `cancel()`.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.config import Settings
from app.core.errors import (
    Conflict,
    NotFound,
    OwnershipRequired,
    ValidationFailed,
)
from app.models.booking import Booking
from app.models.commerce import (
    FULFILLMENT_STATUSES,
    Commission,
    Fulfillment,
    Order,
    Payment,
    PaymentEvent,
    Refund,
)
from app.models.crosscut import CouponRedemption, Promotion
from app.models.marketplace import Offer
from app.services import auth as auth_svc
from app.services import booking as booking_svc
from app.services.notifications import notify, notify_org

PROVIDER_KEYS = ("mock", "bank_transfer", "manual")
_ORDER_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _order_number(now: datetime) -> str:
    stamp = now.strftime("%Y%m%d")
    suffix = "".join(secrets.choice(_ORDER_ALPHABET) for _ in range(6))
    return f"CX-{stamp}-{suffix}"


def cents_amount(value: int, rate_bp: int) -> int:
    """Commission/discount arithmetic in minor units, rounded half-up (§1 no floats in APIs)."""
    if not 0 <= rate_bp <= 10000:
        raise ValidationFailed("rate_bp must be within 0..10000")
    return (int(value) * int(rate_bp) + 5000) // 10000


async def load_order(session: AsyncSession, order_id: uuid.UUID) -> Order:
    order = await session.get(Order, order_id)
    if order is None:
        raise NotFound("Order not found")
    return order


async def order_for_booking(session: AsyncSession, booking_id: uuid.UUID) -> Order | None:
    return (
        await session.execute(select(Order).where(Order.booking_id == booking_id).limit(1))
    ).scalar_one_or_none()


async def _active_payment(session: AsyncSession, order_id: uuid.UUID) -> Payment | None:
    return (
        await session.execute(
            select(Payment)
            .where(Payment.order_id == order_id,
                   Payment.status.in_(("created", "pending", "succeeded")))
            .order_by(Payment.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _validate_coupon_shape(config: dict) -> tuple[str, int, str]:
    kind = config.get("type")
    if kind == "pct":
        bp = int(config.get("bp", 0))
        if not 0 <= bp <= 10000:
            raise ValidationFailed("Coupon percentage out of range")
        return "pct", bp, ""
    if kind == "fixed":
        cents = int(config.get("cents", 0))
        if cents < 0:
            raise ValidationFailed("Coupon amount must be positive")
        return "fixed", cents, str(config.get("currency", "USD"))
    raise ValidationFailed("Unknown coupon discount_config")


async def quote_discount(session: AsyncSession, *, code: str | None, buyer_id: uuid.UUID,
                         subtotal_cents: int, currency: str) -> tuple[Promotion | None, int]:
    """Return (promotion, discount_cents) for a coupon code; raises when it cannot apply."""
    if not code:
        return None, 0
    promo = (
        await session.execute(
            select(Promotion).where(Promotion.code == code.upper()).limit(1)
        )
    ).scalar_one_or_none()
    if promo is None or promo.status != "active":
        raise Conflict("Coupon code is not valid", details={"code": code.upper()})
    now = utcnow()
    if not (promo.starts_at <= now < promo.ends_at):
        raise Conflict("Coupon code is outside its validity window")
    if subtotal_cents < promo.min_order_cents:
        raise Conflict("Order is below the coupon minimum",
                       details={"min_order_cents": promo.min_order_cents})
    if promo.usage_limit is not None and promo.used_count >= promo.usage_limit:
        raise Conflict("Coupon usage limit reached")
    if promo.per_user_limit is not None:
        used = (
            await session.execute(
                select(func.count()).select_from(CouponRedemption).where(
                    CouponRedemption.promotion_id == promo.id,
                    CouponRedemption.user_id == buyer_id)
            )
        ).scalar_one()
        if used >= promo.per_user_limit:
            raise Conflict("Coupon already used by this customer")
    kind, amount, promo_currency = _validate_coupon_shape(promo.discount_config)
    if kind == "pct":
        discount = cents_amount(subtotal_cents, amount)
    else:
        if promo_currency and promo_currency != currency:
            raise Conflict("Coupon currency does not match the order",
                           details={"coupon_currency": promo_currency, "currency": currency})
        discount = min(amount, subtotal_cents)
    return promo, min(discount, subtotal_cents)


async def create_order(session: AsyncSession, *, booking: Booking, offer: Offer | None = None,
                       buyer_id: uuid.UUID, coupon_code: str | None = None,
                       request: Request | None = None) -> Order:
    """Open the order that carries a booking's money (§5.6: automatic on confirm)."""
    existing = await order_for_booking(session, booking.id)
    if existing is not None:
        return existing
    offer = offer or await session.get(Offer, booking.offer_id)
    if offer is None:
        raise NotFound("Offer not found for booking")

    subtotal = booking.total_cents
    promo, discount = await quote_discount(
        session, code=coupon_code, buyer_id=buyer_id,
        subtotal_cents=subtotal, currency=booking.currency)
    total = max(0, subtotal - discount)
    rate_bp = offer.commission_rate_bp if offer.commission_rate_bp is not None else None
    order = Order(
        number=_order_number(utcnow()),
        buyer_id=buyer_id,
        provider_org_id=booking.org_id,
        booking_id=booking.id,
        promotion_id=promo.id if promo else None,
        subtotal_cents=subtotal,
        discount_cents=discount,
        commission_cents=0,
        total_cents=total,
        currency=booking.currency,
        line_items=[{
            "description": offer.title,
            "qty": booking.quantity,
            "unit_cents": booking.unit_amount_cents,
            "total_cents": subtotal,
        }],
        payment_status="unpaid" if total > 0 else "not_required",
        status="placed",
        placed_at=utcnow(),
    )
    session.add(order)
    await session.flush()
    if promo is not None and discount > 0:
        promo.used_count += 1
        session.add(CouponRedemption(promotion_id=promo.id, user_id=buyer_id,
                                     order_id=order.id, discount_cents=discount))
        await session.flush()
    await record_audit(session, request, action="order.created", entity_type="order",
                       entity_id=order.id,
                       after={"booking_id": str(booking.id), "total_cents": order.total_cents,
                              "discount_cents": order.discount_cents})
    await notify(session, user_id=buyer_id, kind="order.created",
                 title=f"Order {order.number}",
                 body=f"Your order for {offer.title} is {order.total_cents} {order.currency} cents.",
                 data={"order_id": str(order.id), "booking_id": str(booking.id)})
    return order


async def get_order_for_actor(session: AsyncSession, order_id: uuid.UUID, *, user_id: uuid.UUID,
                              org_ids: set[uuid.UUID], is_platform_admin: bool) -> Order:
    order = await load_order(session, order_id)
    if is_platform_admin or order.buyer_id == user_id or order.provider_org_id in org_ids:
        return order
    raise OwnershipRequired("You do not have access to this order")


async def create_payment_intent(session: AsyncSession, *, order: Order, provider_key: str,
                                actor_user_id: uuid.UUID, settings: Settings,
                                request: Request | None = None) -> Payment:
    """§5.6 `POST /payments/intents`. Re-asking for a live intent returns the same payment."""
    if order.payment_status == "paid":
        raise Conflict("Order is already paid", details={"order_id": str(order.id)})
    if order.total_cents <= 0:
        raise ValidationFailed("Order has nothing to pay")
    if provider_key not in PROVIDER_KEYS:
        raise ValidationFailed("Unsupported payment provider",
                               details={"allowed": list(PROVIDER_KEYS)})
    current = await _active_payment(session, order.id)
    if current is not None:
        return current
    payment = Payment(
        order_id=order.id,
        provider_key=provider_key,
        amount_cents=order.total_cents,
        currency=order.currency,
        status="created",
        client_secret=f"{provider_key}_secret_{secrets.token_hex(16)}",
        meta={"created_by": str(actor_user_id), "provider_default": settings.payment_provider},
    )
    session.add(payment)
    await session.flush()
    await record_audit(session, request, action="payment.intent", entity_type="payment",
                       entity_id=payment.id,
                       after={"order_id": str(order.id), "amount_cents": payment.amount_cents})
    return payment


async def load_payment(session: AsyncSession, payment_id: uuid.UUID) -> Payment:
    payment = await session.get(Payment, payment_id)
    if payment is None:
        raise NotFound("Payment not found")
    return payment


async def _settle(session: AsyncSession, payment: Payment, *, settings: Settings,
                  actor_user_id: uuid.UUID | None = None,
                  request: Request | None = None,
                  reason: str = "payment captured") -> dict[str, Any]:
    """Single writer of the paid state: payment -> order -> booking -> fulfillment -> commission."""
    order = await load_order(session, payment.order_id)
    now = utcnow()
    outcome: dict[str, Any] = {"order_id": str(order.id), "payment_id": str(payment.id)}

    if payment.status != "succeeded":
        payment.status = "succeeded"
        payment.confirmed_at = now
        payment.provider_payment_id = payment.provider_payment_id or \
            f"{payment.provider_key}_{uuid.UUID(secrets.token_hex(8)).hex[:16]}"

    if order.status in ("draft", "placed"):
        order.payment_status = "paid"
        order.status = "paid"
        booking = await session.get(Booking, order.booking_id) if order.booking_id else None
        offer = await session.get(Offer, booking.offer_id) if booking else None
        rate_bp = settings.default_commission_bp
        if offer is not None and offer.commission_rate_bp is not None:
            rate_bp = offer.commission_rate_bp
        commission = cents_amount(order.total_cents, rate_bp)
        order.commission_cents = commission
        session.add(Commission(order_id=order.id, org_id=order.provider_org_id,
                               basis_cents=order.total_cents, rate_bp=rate_bp,
                               amount_cents=commission, currency=order.currency,
                               booked_at=now))
        outcome["commission_cents"] = commission
        outcome["commission_rate_bp"] = rate_bp

        if booking is not None:
            if booking.status in ("hold", "draft"):
                await booking_svc.transition(session, booking, "confirmed",
                                             actor_user_id=actor_user_id or booking.customer_id,
                                             reason=reason, request=request)
            booking.payment_status = "paid"
            if booking.org_id == order.provider_org_id:
                await notify_org(session, booking.org_id, kind="booking.paid",
                                 title="Payment received",
                                 body=f"Order {order.number} has been paid "
                                      f"({order.total_cents} {order.currency} cents).",
                                 data={"booking_id": str(booking.id), "order_id": str(order.id)},
                                 exclude_user_id=actor_user_id)
            outcome["booking_id"] = str(booking.id)
        await notify(session, user_id=order.buyer_id, kind="order.paid",
                     title=f"Order {order.number} paid",
                     body=f"Payment of {payment.amount_cents} {payment.currency} cents confirmed.",
                     data={"order_id": str(order.id), "payment_id": str(payment.id)})

    if order.booking_id:
        has_fulfillment = (
            await session.execute(
                select(func.count()).select_from(Fulfillment).where(
                    Fulfillment.booking_id == order.booking_id)
            )
        ).scalar_one()
        if not has_fulfillment:
            booking = await session.get(Booking, order.booking_id)
            if booking is not None:
                session.add(Fulfillment(booking_id=booking.id, org_id=booking.org_id,
                                        status="pending", started_at=now,
                                        notes=[{"body": reason, "author_id": None,
                                                "created_at": now.isoformat()}]))
    await session.flush()
    await record_audit(session, request, action="payment.succeeded", entity_type="payment",
                       entity_id=payment.id, after=outcome)
    return outcome


async def confirm_payment(session: AsyncSession, payment: Payment, *, settings: Settings,
                          actor_user_id: uuid.UUID, request: Request | None = None,
                          failure_reason: str | None = None) -> Payment:
    """Mock provider confirmation (§11 PAYMENT_PROVIDER=mock): immediate, idempotent."""
    if payment.status == "succeeded":
        return payment
    if payment.status not in ("created", "pending"):
        raise InvalidPaymentState(payment)
    order = await load_order(session, payment.order_id)
    org_ids = await auth_svc.get_user_org_ids(session, actor_user_id)
    if order.buyer_id != actor_user_id and order.provider_org_id not in org_ids:
        raise OwnershipRequired("Only the buyer can confirm a payment intent")
    if failure_reason:
        payment.status = "failed"
        payment.failure_reason = failure_reason[:500]
        await session.flush()
        await record_audit(session, request, action="payment.failed", entity_type="payment",
                           entity_id=payment.id, after={"reason": payment.failure_reason})
        await notify(session, user_id=order.buyer_id, kind="payment.failed",
                     title="Payment failed", body=payment.failure_reason,
                     data={"payment_id": str(payment.id), "order_id": str(order.id)})
        return payment
    await _settle(session, payment, settings=settings, actor_user_id=actor_user_id,
                  request=request)
    return payment


def verify_webhook_signature(raw_body: bytes, header: str | None, secret: str) -> dict:
    """`X-Webhook-Signature: v1=<hex hmac sha256(raw body)>` — constant-time, 403 on mismatch."""
    from app.core.errors import Unauthorized

    if not header or "=" not in header:
        raise Unauthorized("Missing webhook signature")
    version, _, digest = header.partition("=")
    if version.strip() != "v1":
        raise Unauthorized("Unsupported webhook signature version")
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, digest.strip().lower()):
        raise Unauthorized("Webhook signature does not match")
    try:
        payload = json.loads(raw_body or b"{}")
    except json.JSONDecodeError as exc:
        raise ValidationFailed("Webhook body is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValidationFailed("Webhook body must be a JSON object")
    return payload


async def handle_webhook(session: AsyncSession, *, payload: dict[str, Any], settings: Settings,
                         raw_body: bytes, signature: str | None,
                         request: Request | None = None) -> dict[str, Any]:
    """§5.6 webhook: verified, stored in `payment_events`, replayed safely (§5.7 idempotency)."""
    verify_webhook_signature(raw_body, signature, settings.webhook_secret)
    event_type = str(payload.get("type") or "")
    event_key = str(payload.get("id") or "")
    payment_ref = str(payload.get("payment_id") or "")
    if not event_key or not payment_ref:
        raise ValidationFailed("Webhook needs id and payment_id")
    try:
        payment_id = uuid.UUID(payment_ref)
    except ValueError as exc:
        raise ValidationFailed("payment_id must be a UUID") from exc

    seen = (
        await session.execute(select(PaymentEvent).where(PaymentEvent.event_key == event_key)
                              .limit(1))
    ).scalar_one_or_none()
    if seen is not None:
        return {"status": "duplicate", "event_key": event_key}

    payment = await session.get(Payment, payment_id)
    if payment is None:
        raise NotFound("Payment not found for webhook event")

    session.add(PaymentEvent(payment_id=payment.id, event_key=event_key[:160],
                             type=event_type[:64], payload=payload,
                             occurred_at=_parse_dt(payload.get("occurred_at")) or utcnow()))
    await session.flush()

    if event_type == "payment.succeeded":
        await _settle(session, payment, settings=settings, request=request,
                      reason="webhook capture")
    elif event_type == "payment.failed":
        payment.status = "failed"
        payment.failure_reason = str(payload.get("failure_reason") or "declined")[:500]
    elif event_type == "payment.refunded":
        await _apply_refund(session, payment,
                            amount_cents=int(payload.get("amount_cents")
                                             or payment.amount_cents),
                            reason=str(payload.get("reason") or "provider refund"),
                            actor_user_id=None)
    else:
        # Unknown events are recorded but never mutate state (§2: honest, no silent 500).
        await session.flush()
        return {"status": "ignored", "event_key": event_key, "type": event_type}
    await session.flush()
    await record_audit(session, request, action=f"webhook.{event_type}", entity_type="payment",
                       entity_id=payment.id, after={"event_key": event_key})
    return {"status": "processed", "event_key": event_key, "payment_status": payment.status}


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


async def _apply_refund(session: AsyncSession, payment: Payment, *, amount_cents: int,
                        reason: str, actor_user_id: uuid.UUID | None,
                        status: str = "succeeded") -> Refund | None:
    """Move order/payment refund state and record the refund row; None when nothing is owed.

    `refunds.amount_cents > 0` is a CHECK, so a zero-value refund is not insertable — the
    order simply lands on its refunded status.
    """
    order = await load_order(session, payment.order_id)
    amount = min(int(amount_cents), order.total_cents - order.refunded_cents)
    if amount <= 0:
        order.payment_status = "refunded"
        if order.status in ("draft", "placed", "paid"):
            order.status = "refunded"
        await session.flush()
        return None
    order.refunded_cents += amount
    fully_refunded = order.refunded_cents >= order.total_cents
    order.payment_status = "refunded" if fully_refunded else "partially_refunded"
    if fully_refunded and order.status in ("draft", "placed", "paid", "fulfilled"):
        order.status = "refunded"
    if fully_refunded and payment.status == "succeeded":
        payment.status = "refunded"
    refund = Refund(payment_id=payment.id, order_id=order.id, amount_cents=amount,
                    currency=order.currency, reason=reason[:2000], status=status,
                    processed_by=actor_user_id, processed_at=utcnow() if status != "pending" else None)
    session.add(refund)
    await session.flush()
    return refund


async def refund_for_cancellation(session: AsyncSession, booking: Booking, *,
                                  offer: Offer | None, actor_user_id: uuid.UUID) -> int:
    """Policy-driven refund used by `booking.cancel()` (§5.6): returns cents refunded."""
    order = await order_for_booking(session, booking.id)
    if order is None:
        return 0
    payment = await _active_payment(session, order.id)
    if payment is None or payment.status != "succeeded":
        return 0
    hours_before = (booking.window_start - utcnow()).total_seconds() / 3600
    pct = booking_svc.refund_pct_for(offer.cancellation_policy if offer else [], hours_before)
    outstanding = order.total_cents - order.refunded_cents
    amount = cents_amount(outstanding, pct * 100)
    if amount <= 0:
        return 0
    refund = await _apply_refund(session, payment, amount_cents=amount,
                                 reason=f"cancellation policy ({pct}% at "
                                        f"{round(hours_before, 1)}h before start)",
                                 actor_user_id=actor_user_id)
    if refund is None:
        return 0
    booking.payment_status = "refunded" if order.payment_status == "refunded" \
        else "partially_refunded"
    await session.flush()
    return refund.amount_cents


async def list_orders_for_actor(session: AsyncSession, *, user_id: uuid.UUID,
                                org_ids: set[uuid.UUID], is_platform_admin: bool,
                                limit: int = 20, offset: int = 0) -> tuple[list[Order], int]:
    stmt = select(Order)
    if not is_platform_admin:
        stmt = stmt.where((Order.buyer_id == user_id) | (Order.provider_org_id.in_(org_ids)))
    total = (
        await session.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()
    items = list((
        await session.execute(stmt.order_by(Order.created_at.desc()).limit(limit).offset(offset))
    ).scalars().all())
    return items, total


async def payments_for_order(session: AsyncSession, order_id: uuid.UUID) -> list[Payment]:
    return list((
        await session.execute(
            select(Payment).where(Payment.order_id == order_id)
            .order_by(Payment.created_at)
        )
    ).scalars().all())


async def fulfillment_for_booking(session: AsyncSession,
                                  booking_id: uuid.UUID) -> Fulfillment | None:
    return (
        await session.execute(
            select(Fulfillment).where(Fulfillment.booking_id == booking_id).limit(1)
        )
    ).scalar_one_or_none()


async def add_fulfillment_note(session: AsyncSession, fulfillment: Fulfillment, *,
                               body: str, author_id: uuid.UUID,
                               request: Request | None = None) -> Fulfillment:
    notes = list(fulfillment.notes or [])
    notes.append({"body": body[:4000], "author_id": str(author_id),
                  "created_at": utcnow().isoformat()})
    fulfillment.notes = notes
    await session.flush()
    await record_audit(session, request, action="fulfillment.note", entity_type="fulfillment",
                       entity_id=fulfillment.id, after={"note_count": len(notes)})
    return fulfillment


async def set_fulfillment_status(session: AsyncSession, fulfillment: Fulfillment,
                                 status: str, *, actor_user_id: uuid.UUID,
                                 request: Request | None = None) -> Fulfillment:
    if status not in FULFILLMENT_STATUSES:
        raise ValidationFailed("Unknown fulfillment status",
                               details={"allowed": list(FULFILLMENT_STATUSES)})
    before = fulfillment.status
    fulfillment.status = status
    if status == "completed":
        fulfillment.completed_at = fulfillment.completed_at or utcnow()
        fulfillment.completed_by = actor_user_id
    await session.flush()
    await record_audit(session, request, action=f"fulfillment.{status}",
                       entity_type="fulfillment", entity_id=fulfillment.id,
                       before={"status": before}, after={"status": status})
    return fulfillment


class InvalidPaymentState(Conflict):
    """A payment outside created/pending cannot be confirmed (§5.6)."""

    def __init__(self, payment: Payment) -> None:
        super().__init__(f"Payment is in status '{payment.status}' and cannot be confirmed",
                         code="invalid_state_transition", http_status=400)
