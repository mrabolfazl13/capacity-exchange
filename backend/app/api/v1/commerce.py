"""`/api/v1/orders`, `/payments`, `/fulfillments` — the money surface (CONTRACTS §5.6/§8).

Tenancy and settlement live in `app.services.commerce`; this module only scopes the caller,
applies the §2 envelope and keeps the idempotent writes (§5.7) on the documented routes.
`POST /payments/webhook` is the one unauthenticated write: the HMAC header authorises it,
which is what a third-party provider can actually send.
"""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.api.v1.deps import ActorDep, PageDep, envelope, idempotent_write, order_payload
from app.core.deps import SessionDep, SettingsDep
from app.core.errors import (
    InvalidStateTransition,
    OwnershipRequired,
    ValidationFailed,
)
from app.core.routing import TransactionalRoute
from app.models.commerce import ORDER_PAYMENT_STATUSES, ORDER_STATUSES, Order, Payment
from app.schemas.commerce import (
    FulfillmentNoteInput,
    FulfillmentOut,
    OrderCreateInput,
    PaymentIntentInput,
    PaymentOut,
)
from app.services import booking as booking_svc
from app.services import commerce as svc

router = APIRouter(route_class=TransactionalRoute, tags=["commerce"])


async def _order(session: SessionDep, actor: ActorDep, order_id: uuid.UUID) -> Order:
    return await svc.get_order_for_actor(
        session, order_id, user_id=actor.user_id, org_ids=actor.org_ids,
        is_platform_admin=actor.is_platform_admin)


async def _payment(session: SessionDep, actor: ActorDep, payment_id: uuid.UUID) -> Payment:
    """A payment is visible exactly as far as its order is."""
    payment = await svc.load_payment(session, payment_id)
    await _order(session, actor, payment.order_id)
    return payment


# --------------------------------------------------------------------------- orders


@router.get("/orders")
async def list_orders(session: SessionDep, actor: ActorDep, page: PageDep,
                      provider: bool = False,
                      status: Annotated[str | None, Query(max_length=16)] = None,
                      payment_status: Annotated[str | None, Query(max_length=24)] = None) -> dict:
    if status is not None and status not in ORDER_STATUSES:
        raise ValidationFailed("Unknown order status", details={"allowed": list(ORDER_STATUSES)})
    if payment_status is not None and payment_status not in ORDER_PAYMENT_STATUSES:
        raise ValidationFailed("Unknown payment status",
                               details={"allowed": list(ORDER_PAYMENT_STATUSES)})
    items, total = await svc.list_orders_for_actor(
        session, user_id=actor.user_id, org_ids=actor.org_ids,
        is_platform_admin=actor.is_platform_admin, provider=provider,
        status=status, payment_status=payment_status,
        limit=page.limit, offset=page.offset)
    rows = [await order_payload(session, order) for order in items]
    return envelope(rows, int(total), page)


@router.post("/orders", status_code=201)
async def create_order(body: OrderCreateInput, request: Request, session: SessionDep,
                       actor: ActorDep) -> dict:
    """Open the order for a live booking; confirmation already did this automatically (§5.6)."""
    payload = body.model_dump(mode="json")

    async def build() -> dict:
        booking = await booking_svc.get_for_actor(
            session, body.booking_id, user_id=actor.user_id, org_ids=actor.org_ids,
            is_platform_admin=actor.is_platform_admin)
        if booking.customer_id != actor.user_id and not actor.is_platform_admin:
            raise OwnershipRequired("Only the buying customer can open an order")
        if not booking.is_active:
            raise InvalidStateTransition(
                f"Booking is {booking.status} and cannot carry an order",
                details={"booking_id": str(booking.id), "status": booking.status})
        order = await svc.create_order(session, booking=booking, buyer_id=booking.customer_id,
                                       coupon_code=body.coupon_code, request=request)
        return await order_payload(session, order)

    return await idempotent_write(session, request, route="POST /orders",
                                  actor_id=actor.user_id, payload=payload, status_code=201,
                                  build=build)


@router.get("/orders/{order_id}")
async def get_order(order_id: uuid.UUID, session: SessionDep, actor: ActorDep) -> dict:
    return await order_payload(session, await _order(session, actor, order_id))


@router.get("/orders/{order_id}/payments")
async def order_payments(order_id: uuid.UUID, session: SessionDep, actor: ActorDep,
                         page: PageDep) -> dict:
    await _order(session, actor, order_id)
    payments = await svc.payments_for_order(session, order_id)
    items = [PaymentOut.model_validate(p).model_dump(mode="json") for p in payments]
    return envelope(items, len(items), page)


# --------------------------------------------------------------------------- payments


@router.post("/payments/intents", status_code=201)
async def create_payment_intent(body: PaymentIntentInput, request: Request, session: SessionDep,
                                actor: ActorDep, settings: SettingsDep) -> dict:
    """§5.6: ask the configured provider for an intent; a repeat returns the live payment."""
    payload = body.model_dump(mode="json")

    async def build() -> dict:
        order = await _order(session, actor, body.order_id)
        if order.buyer_id != actor.user_id and not actor.is_platform_admin:
            raise OwnershipRequired("Only the buying customer can start a payment")
        payment = await svc.create_payment_intent(
            session, order=order, provider_key=body.provider_key,
            actor_user_id=actor.user_id, settings=settings, request=request)
        return PaymentOut.model_validate(payment).model_dump(mode="json")

    return await idempotent_write(session, request, route="POST /payments/intents",
                                  actor_id=actor.user_id, payload=payload, status_code=201,
                                  build=build)


@router.post("/payments/{payment_id}/confirm")
async def confirm_payment(payment_id: uuid.UUID, request: Request, session: SessionDep,
                          actor: ActorDep, settings: SettingsDep) -> dict:
    """Mock-provider capture (§11): settles order, booking, fulfillment and commission."""
    async def build() -> dict:
        payment = await _payment(session, actor, payment_id)
        await svc.confirm_payment(session, payment, settings=settings,
                                  actor_user_id=actor.user_id, request=request)
        return PaymentOut.model_validate(payment).model_dump(mode="json")

    return await idempotent_write(session, request, route=f"POST /payments/{payment_id}/confirm",
                                  actor_id=actor.user_id, payload={"payment_id": str(payment_id)},
                                  status_code=200, build=build)


@router.get("/payments/{payment_id}")
async def get_payment(payment_id: uuid.UUID, session: SessionDep, actor: ActorDep) -> dict:
    return PaymentOut.model_validate(await _payment(session, actor, payment_id)
                                     ).model_dump(mode="json")


@router.post("/payments/webhook")
async def payments_webhook(request: Request, session: SessionDep,
                           settings: SettingsDep) -> dict:
    """Provider callback: HMAC-verified body, replayed events answered as duplicates (§5.6)."""
    return await svc.handle_webhook(session, settings=settings,
                                    raw_body=await request.body(),
                                    signature=request.headers.get("X-Webhook-Signature"),
                                    request=request)


# --------------------------------------------------------------------------- fulfillment


@router.post("/fulfillments/{fulfillment_id}/notes")
async def add_fulfillment_note(fulfillment_id: uuid.UUID, body: FulfillmentNoteInput,
                               request: Request, session: SessionDep, actor: ActorDep) -> dict:
    fulfillment = await svc.load_fulfillment(session, fulfillment_id)
    if not actor.can_manage_org(fulfillment.org_id):
        raise OwnershipRequired("Only the providing organization can annotate a fulfillment")
    await svc.add_fulfillment_note(session, fulfillment, body=body.body,
                                   author_id=actor.user_id, request=request)
    return {"ok": True,
            "fulfillment": FulfillmentOut.model_validate(fulfillment).model_dump(mode="json")}
