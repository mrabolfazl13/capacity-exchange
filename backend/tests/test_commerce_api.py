"""§5.6 money surface: orders, payment intents, mock capture, webhooks, refunds, fulfilment notes.

Every assertion runs through the HTTP routes — the path the desktop and mobile clients ship —
with real Postgres settlement state behind it.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from tests.test_auth_api import auth, login, register
from tests.test_booking_api import DAY, END, OTHER_DAY, START, free_quantity, hold, live_offer
from tests.test_capacity_api import make_provider

COUPON = "LAUNCH10"
OTHER_START = f"{OTHER_DAY}T10:00:00Z"
OTHER_END = f"{OTHER_DAY}T12:00:00Z"


async def book_offer(client, tokens, offer_id: str, **changes) -> dict:
    """Instant conversion: confirmed booking + order + pending fulfillment in one call."""
    resp = await client.post("/bookings", headers=auth(tokens), json={
        "offer_id": offer_id, "window_start": START, "window_end": END, **changes})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def orders_of(client, tokens, **params) -> dict:
    resp = await client.get("/orders", headers=auth(tokens), params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def intent(client, tokens, order_id: str, **changes) -> dict:
    resp = await client.post("/payments/intents", headers=auth(tokens),
                             json={"order_id": order_id, **changes})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def confirm(client, tokens, payment_id: str, key: str | None = None) -> tuple[int, dict]:
    headers = {**auth(tokens), **({"Idempotency-Key": key} if key else {})}
    resp = await client.post(f"/payments/{payment_id}/confirm", headers=headers, json={})
    return resp.status_code, resp.json()


def sign(body: dict, secret: str) -> tuple[bytes, dict]:
    """§5.6: `X-Webhook-Signature: v1=<hex hmac sha256 of the raw body>`."""
    raw = json.dumps(body).encode()
    digest = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return raw, {"X-Webhook-Signature": f"v1={digest}", "Content-Type": "application/json"}


async def activate_coupon(db, *, bp: int = 1000, per_user_limit: int | None = 1,
                          min_order_cents: int = 5000) -> str:
    from app.models.crosscut import Promotion

    now = datetime.now(timezone.utc)
    db.add(Promotion(name="Launch discount", kind="coupon", code=COUPON,
                     discount_config={"type": "pct", "bp": bp}, min_order_cents=min_order_cents,
                     applies_to={}, usage_limit=10, used_count=0,
                     per_user_limit=per_user_limit, starts_at=now - timedelta(days=1),
                     ends_at=now + timedelta(days=30), status="active"))
    await db.commit()
    return COUPON


async def test_instant_booking_pays_out_through_the_documented_flow(client):
    provider = await make_provider(client, "cm.prov@example.test")
    offer = await live_offer(client, provider, quantity=2)
    buyer = await register(client, "cm.buyer@example.test")

    booking = await book_offer(client, buyer, offer["id"])
    assert booking["status"] == "confirmed"
    assert booking["payment_status"] == "unpaid"
    assert booking["total_cents"] == 10000
    order_id = booking["order_id"]
    assert order_id and booking["order"]["status"] == "placed"
    assert booking["order"]["number"].startswith("CX-")
    assert booking["fulfillment"]["status"] == "pending"

    listed = await orders_of(client, buyer)
    assert listed["total"] == 1
    order = listed["items"][0]
    assert order["id"] == order_id and order["booking_id"] == booking["id"]
    assert order["subtotal_cents"] == 10000 and order["discount_cents"] == 0
    assert order["commission_cents"] == 0  # booked at settlement, not at placement
    assert order["total_cents"] == 10000 and order["currency"] == "USD"
    assert order["provider_org_name"] == "Room Owner Org"
    assert order["line_items"] == [{"description": offer["title"], "qty": 1,
                                    "unit_cents": 10000, "total_cents": 10000}]
    assert order["placed_at"].endswith("Z") and order["created_at"].endswith("Z")

    payment = await intent(client, buyer, order_id)
    assert payment["status"] == "created"
    assert payment["provider_key"] == "mock" and payment["amount_cents"] == 10000
    assert payment["client_secret"].startswith("mock_secret_")
    assert payment["confirmed_at"] is None and payment["failure_reason"] is None

    # A second intent without any key returns the live payment rather than a parallel one.
    again = await intent(client, buyer, order_id)
    assert again["id"] == payment["id"]

    code, settled = await confirm(client, buyer, payment["id"], key="pay-k1")
    assert code == 200, settled
    assert settled["status"] == "succeeded" and settled["confirmed_at"].endswith("Z")
    assert settled["provider_payment_id"].startswith("mock_")

    code, replay = await confirm(client, buyer, payment["id"], key="pay-k2")
    assert code == 200 and replay["id"] == payment["id"] and replay["status"] == "succeeded"

    detail = await client.get(f"/bookings/{booking['id']}", headers=auth(buyer))
    assert detail.status_code == 200
    body = detail.json()
    assert body["status"] == "confirmed" and body["payment_status"] == "paid"
    assert body["order"]["status"] == "paid"
    assert body["order"]["commission_cents"] == 1000  # §5.6 default_commission_bp = 1000
    assert body["fulfillment"]["status"] == "pending"
    assert body["fulfillment"]["notes"][0]["body"] == "booking confirmed"

    as_provider = await orders_of(client, provider, provider="true")
    assert [o["id"] for o in as_provider["items"]] == [order_id]
    assert as_provider["items"][0]["payment_status"] == "paid"

    payments = await client.get(f"/orders/{order_id}/payments", headers=auth(buyer))
    assert payments.status_code == 200
    envelope = payments.json()
    assert envelope["total"] == 1 and envelope["items"][0]["id"] == payment["id"]
    single = await client.get(f"/payments/{payment['id']}", headers=auth(buyer))
    assert single.status_code == 200 and single.json()["status"] == "succeeded"
    assert await free_quantity(client, buyer, offer["definition_id"]) == 1


async def test_order_and_payment_visibility_is_scoped_by_tenancy(client):
    provider = await make_provider(client, "cm.scope.prov@example.test")
    offer = await live_offer(client, provider, quantity=2)
    buyer = await register(client, "cm.scope.buyer@example.test")
    stranger = await register(client, "cm.scope.stranger@example.test")

    booking = await book_offer(client, buyer, offer["id"])
    order_id = booking["order_id"]

    assert (await client.get(f"/orders/{order_id}", headers=auth(provider))).status_code == 200
    blocked = await client.get(f"/orders/{order_id}", headers=auth(stranger))
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "ownership_required"
    assert (await client.get(f"/orders/{order_id}/payments", headers=auth(stranger))
            ).status_code == 403
    assert (await client.get("/orders", headers=auth(stranger))).json()["items"] == []
    # A customer has no provider side, so the org view stays empty instead of leaking.
    assert (await orders_of(client, buyer, provider="true"))["items"] == []
    assert (await client.get(f"/orders/{order_id}")).status_code == 401

    missing = await client.get(f"/orders/{uuid.uuid4()}", headers=auth(buyer))
    assert missing.status_code == 404

    payment = await intent(client, buyer, order_id)
    assert (await client.get(f"/payments/{payment['id']}", headers=auth(stranger))
            ).status_code == 403
    provider_confirm = await client.post(f"/payments/{payment['id']}/confirm",
                                         headers=auth(stranger), json={})
    assert provider_confirm.status_code == 403
    stranger_intent = await client.post("/payments/intents", headers=auth(stranger),
                                        json={"order_id": order_id})
    assert stranger_intent.status_code == 403


async def test_payment_intent_rejects_the_states_that_cannot_be_paid(client):
    provider = await make_provider(client, "cm.reject.prov@example.test")
    offer = await live_offer(client, provider, quantity=3)
    buyer = await register(client, "cm.reject.buyer@example.test")

    wrong_provider = await client.post("/payments/intents", headers=auth(buyer), json={
        "order_id": (await book_offer(client, buyer, offer["id"]))["order_id"],
        "provider_key": "paypal"})
    assert wrong_provider.status_code == 400
    assert wrong_provider.json()["error"]["code"] == "validation_error"
    assert wrong_provider.json()["error"]["details"]["allowed"] == ["mock", "bank_transfer",
                                                                    "manual"]

    free_offer = await live_offer(client, provider, quantity=2, name="Free Corner",
                                  unit_amount_cents=0)
    free_booking = await book_offer(client, buyer, free_offer["id"])
    assert free_booking["total_cents"] == 0
    assert free_booking["order"]["payment_status"] == "not_required"
    nothing = await client.post("/payments/intents", headers=auth(buyer), json={
        "order_id": free_booking["order_id"]})
    assert nothing.status_code == 400
    assert nothing.json()["error"]["message"] == "Order has nothing to pay"
    # The free booking still gets the operational record payment would have created.
    assert free_booking["fulfillment"]["status"] == "pending"

    booked = await book_offer(client, buyer, offer["id"])
    await confirm(client, buyer, (await intent(client, buyer, booked["order_id"]))["id"])
    twice = await client.post("/payments/intents", headers=auth(buyer), json={
        "order_id": booked["order_id"]})
    assert twice.status_code == 409
    assert twice.json()["error"]["code"] == "conflict"

    bad_body = await client.post("/payments/intents", headers=auth(buyer), json={})
    assert bad_body.status_code == 422
    assert (await client.post(f"/payments/{uuid.uuid4()}/confirm", headers=auth(buyer),
                              json={})).status_code == 404


async def test_webhook_settles_refunds_and_refuses_unsigned_bodies(client, app):
    provider = await make_provider(client, "cm.hook.prov@example.test")
    offer = await live_offer(client, provider, quantity=2)
    buyer = await register(client, "cm.hook.buyer@example.test")
    secret = app.state.settings.webhook_secret

    booking = await book_offer(client, buyer, offer["id"])
    payment = await intent(client, buyer, booking["order_id"])
    event = {"id": "evt_1", "type": "payment.succeeded", "payment_id": payment["id"],
             "occurred_at": f"{DAY}T09:00:00Z"}
    raw, headers = sign(event, secret)

    unsigned = await client.post("/payments/webhook", content=raw, headers={
        "Content-Type": "application/json"})
    assert unsigned.status_code == 401
    assert unsigned.json()["error"]["code"] == "unauthorized"
    forged, forged_headers = sign(event, "not-the-secret")
    bad = await client.post("/payments/webhook", content=forged, headers=forged_headers)
    assert bad.status_code == 401
    assert bad.json()["error"]["message"] == "Webhook signature does not match"

    processed = await client.post("/payments/webhook", content=raw, headers=headers)
    assert processed.status_code == 200, processed.text
    assert processed.json() == {"status": "processed", "event_key": "evt_1",
                                "payment_status": "succeeded"}

    after = await client.get(f"/bookings/{booking['id']}", headers=auth(buyer))
    assert after.json()["payment_status"] == "paid"
    assert after.json()["order"]["commission_cents"] == 1000

    replay_raw, replay_headers = sign(event, secret)
    duplicate = await client.post("/payments/webhook", content=replay_raw, headers=replay_headers)
    assert duplicate.json() == {"status": "duplicate", "event_key": "evt_1"}

    noise = {"id": "evt_noise", "type": "provider.nothing", "payment_id": payment["id"]}
    noise_raw, noise_headers = sign(noise, secret)
    ignored = await client.post("/payments/webhook", content=noise_raw, headers=noise_headers)
    assert ignored.json()["status"] == "ignored"

    refund = {"id": "evt_refund", "type": "payment.refunded", "payment_id": payment["id"],
              "amount_cents": 4000, "reason": "partial goodwill"}
    refund_raw, refund_headers = sign(refund, secret)
    refunded = await client.post("/payments/webhook", content=refund_raw, headers=refund_headers)
    assert refunded.status_code == 200, refunded.text
    assert refunded.json()["payment_status"] == "succeeded"

    order = (await client.get(f"/orders/{booking['order_id']}", headers=auth(buyer))).json()
    assert order["payment_status"] == "partially_refunded"
    assert order["refunded_cents"] == 4000 and order["status"] == "paid"
    booking_after = (await client.get(f"/bookings/{booking['id']}", headers=auth(buyer))).json()
    assert booking_after["payment_status"] == "partially_refunded"
    assert booking_after["status"] == "confirmed"  # money moved, the service is still on

    malformed, malformed_headers = sign([1, 2], secret)
    assert (await client.post("/payments/webhook", content=malformed,
                              headers=malformed_headers)).status_code == 400
    ghost = {"id": "evt_ghost", "type": "payment.succeeded",
             "payment_id": str(uuid.uuid4())}
    ghost_raw, ghost_headers = sign(ghost, secret)
    assert (await client.post("/payments/webhook", content=ghost_raw,
                              headers=ghost_headers)).status_code == 404


async def test_order_can_be_opened_from_a_hold_with_a_coupon(client, db):
    from app.models.crosscut import CouponRedemption, Promotion

    provider = await make_provider(client, "cm.coupon.prov@example.test")
    offer = await live_offer(client, provider, quantity=3)
    buyer = await register(client, "cm.coupon.buyer@example.test")
    other = await register(client, "cm.coupon.other@example.test")
    await activate_coupon(db)

    held = await hold(client, buyer, offer["id"])
    assert held["order_id"] is None

    resp = await client.post("/orders", headers={**auth(buyer), "Idempotency-Key": "ord-k1"},
                             json={"booking_id": held["id"], "coupon_code": COUPON.lower()})
    assert resp.status_code == 201, resp.text
    order = resp.json()
    assert order["subtotal_cents"] == 10000 and order["discount_cents"] == 1000
    assert order["total_cents"] == 9000 and order["payment_status"] == "unpaid"
    assert order["booking_id"] == held["id"]

    replay = await client.post("/orders", headers={**auth(buyer), "Idempotency-Key": "ord-k1"},
                               json={"booking_id": held["id"], "coupon_code": COUPON.lower()})
    assert replay.status_code == 201 and replay.json()["id"] == order["id"]

    # Same key with a changed body is a conflicting retry (§5.7).
    changed = await client.post("/orders", headers={**auth(buyer), "Idempotency-Key": "ord-k1"},
                                json={"booking_id": held["id"]})
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "conflict"

    # The order already exists, so a fresh key cannot redeem the coupon a second time.
    fresh = await client.post("/orders", headers={**auth(buyer), "Idempotency-Key": "ord-k2"},
                              json={"booking_id": held["id"], "coupon_code": COUPON})
    assert fresh.status_code == 409
    assert fresh.json()["error"]["code"] == "conflict"
    assert fresh.json()["error"]["details"]["order_id"] == order["id"]

    redemptions = (await db.execute(
        select(CouponRedemption).where(CouponRedemption.order_id == uuid.UUID(order["id"]))
    )).scalars().all()
    assert len(redemptions) == 1
    assert redemptions[0].discount_cents == 1000
    promotion = (await db.execute(select(Promotion).where(Promotion.code == COUPON)
                                  )).scalar_one()
    assert promotion.used_count == 1

    other_held = await hold(client, other, offer["id"])
    other_order = await client.post("/orders", headers=auth(other), json={
        "booking_id": other_held["id"], "coupon_code": COUPON})
    assert other_order.status_code == 201, other_order.text
    assert other_order.json()["discount_cents"] == 1000

    # The limit is per customer: buyer already burned theirs on the first order.
    second_held = await hold(client, buyer, offer["id"], start=OTHER_START, end=OTHER_END)
    capped = await client.post("/orders", headers=auth(buyer), json={
        "booking_id": second_held["id"], "coupon_code": COUPON})
    assert capped.status_code == 409
    assert capped.json()["error"]["message"] == "Coupon already used by this customer"

    unknown = await client.post("/orders", headers=auth(other), json={
        "booking_id": (await hold(client, other, offer["id"],
                                  start=OTHER_START, end=OTHER_END))["id"],
        "coupon_code": "NOPE404"})
    assert unknown.status_code == 409
    assert unknown.json()["error"]["code"] == "conflict"
    assert unknown.json()["error"]["details"]["code"] == "NOPE404"

    cancelled = await client.post(f"/bookings/{other_held['id']}/cancel", headers=auth(other),
                                  json={"reason": "changed plans"})
    assert cancelled.status_code == 200, cancelled.text
    late = await client.post("/orders", headers=auth(other), json={
        "booking_id": other_held["id"]})
    assert late.status_code == 400
    assert late.json()["error"]["code"] == "invalid_state_transition"

    stranger = await register(client, "cm.coupon.stranger@example.test")
    assert (await client.post("/orders", headers=auth(stranger), json={
        "booking_id": held["id"]})).status_code == 403
    assert (await client.post("/orders", headers=auth(buyer), json={
        "booking_id": str(uuid.uuid4())})).status_code == 404
    assert (await client.post("/orders", headers=auth(buyer), json={
        "booking_id": held["id"], "coupon_code": "ab"})).status_code == 422


async def test_fulfillment_notes_belong_to_the_provider(client):
    provider = await make_provider(client, "cm.ful.prov@example.test")
    offer = await live_offer(client, provider, quantity=2)
    buyer = await register(client, "cm.ful.buyer@example.test")

    booking = await book_offer(client, buyer, offer["id"])
    fulfillment_id = booking["fulfillment"]["id"]

    resp = await client.post(f"/fulfillments/{fulfillment_id}/notes", headers=auth(provider),
                             json={"body": "Room prepared, projector tested."})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert data["fulfillment"]["status"] == "pending"
    assert len(data["fulfillment"]["notes"]) == 2
    note = data["fulfillment"]["notes"][1]
    assert note["body"] == "Room prepared, projector tested."
    assert note["author_id"] == provider["user"]["id"]
    assert note["created_at"].endswith("Z")

    denied = await client.post(f"/fulfillments/{fulfillment_id}/notes", headers=auth(buyer),
                               json={"body": "Customer note attempt"})
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "ownership_required"
    assert (await client.post(f"/fulfillments/{uuid.uuid4()}/notes", headers=auth(provider),
                              json={"body": "ghost"})).status_code == 404
    assert (await client.post(f"/fulfillments/{fulfillment_id}/notes", headers=auth(provider),
                              json={"body": ""})).status_code == 422


async def test_platform_admin_sees_every_order(client, db):
    from app.models.commerce import ORDER_STATUSES
    from app.models.identity import UserRole

    provider = await make_provider(client, "cm.admin.prov@example.test")
    offer = await live_offer(client, provider, quantity=2)
    buyer = await register(client, "cm.admin.buyer@example.test")
    booking = await book_offer(client, buyer, offer["id"])

    admin_tokens = await register(client, "cm.admin@example.test")
    db.add(UserRole(user_id=uuid.UUID(admin_tokens["user"]["id"]), role="platform_admin"))
    await db.commit()
    admin = await login(client, "cm.admin@example.test")
    me = await client.get("/auth/me", headers=auth(admin))
    assert "platform_admin" in me.json()["roles"]

    listed = await orders_of(client, admin)
    assert [o["id"] for o in listed["items"]] == [booking["order_id"]]
    assert (await orders_of(client, admin, provider="true"))["total"] == 1
    assert (await client.get(f"/orders/{booking['order_id']}", headers=auth(admin))
            ).status_code == 200

    assert (await orders_of(client, admin, status="paid"))["items"] == []
    assert (await orders_of(client, admin, status="placed", payment_status="unpaid")
            )["total"] == 1
    bad_status = await client.get("/orders", headers=auth(admin), params={"status": "nonsense"})
    assert bad_status.status_code == 400
    assert bad_status.json()["error"]["details"]["allowed"] == list(ORDER_STATUSES)
    bad_payment = await client.get("/orders", headers=auth(admin),
                                   params={"payment_status": "nonsense"})
    assert bad_payment.status_code == 400
    assert bad_payment.json()["error"]["code"] == "validation_error"
