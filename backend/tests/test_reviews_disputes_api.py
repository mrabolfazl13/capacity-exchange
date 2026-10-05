"""§5.7/§8 trust surface: reviews, provider replies, moderation and dispute remediation.

Runs through the shipped routes only. A dispute is only as real as the refund it produces,
so the money assertions read the order and the booking back over HTTP as well.
"""
from __future__ import annotations

import uuid

from tests.test_auth_api import auth, login, register
from tests.test_booking_api import hold, live_offer
from tests.test_capacity_api import make_provider
from tests.test_commerce_api import book_offer, confirm, intent

COMMENT = "Bright room, the projector worked all day and the whiteboard was freshly erased."
SHORT_COMMENT = "Great."
DESCRIPTION = "The room was locked when we arrived and the session had to be shortened."
NOTE = "Checked the timeline with both parties and settled it here."
GHOST = "11111111-1111-1111-1111-111111111111"


async def pay(client, tokens, booking: dict) -> dict:
    payment = await intent(client, tokens, booking["order_id"])
    code, settled = await confirm(client, tokens, payment["id"])
    assert code == 200, settled
    return settled


async def delivered(client, provider, buyer, offer_id: str, **changes) -> dict:
    """Book, capture the payment, run the service to completion — the reviewable state."""
    booking = await book_offer(client, buyer, offer_id, **changes)
    await pay(client, buyer, booking)
    for action in ("start", "complete"):
        resp = await client.post(f"/bookings/{booking['id']}/{action}", headers=auth(provider))
        assert resp.status_code == 200, resp.text
        booking = resp.json()
    return booking


async def add_review(client, tokens, booking_id: str, *, rating: int = 5, **changes) -> dict:
    resp = await client.post("/reviews", headers=auth(tokens), json={
        "booking_id": booking_id, "rating": rating, "comment": COMMENT, **changes})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def reviews_of(client, tokens, **params) -> dict:
    resp = await client.get("/reviews", headers=auth(tokens), params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def open_dispute(client, tokens, booking_id: str, **changes) -> dict:
    body = {"booking_id": booking_id, "kind": "quality", "description": DESCRIPTION}
    body.update(changes)
    resp = await client.post("/disputes", headers=auth(tokens), json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def disputes_of(client, tokens, **params) -> dict:
    resp = await client.get("/disputes", headers=auth(tokens), params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def resolve(client, tokens, dispute_id: str, status: str, **changes) -> tuple[int, dict]:
    resp = await client.post(f"/disputes/{dispute_id}/resolve", headers=auth(tokens),
                             json={"status": status, "resolution_note": NOTE, **changes})
    return resp.status_code, resp.json()


async def staff(client, db, email: str, role: str) -> dict:
    """Grant a platform role the admin console would, then hand back fresh tokens (§4)."""
    from app.models.identity import UserRole

    created = await register(client, email)
    db.add(UserRole(user_id=uuid.UUID(created["user"]["id"]), role=role))
    await db.commit()
    tokens = await login(client, email)
    tokens["user"] = created["user"]
    return tokens


async def test_only_the_customer_of_a_delivered_booking_can_rate_it(client):
    provider = await make_provider(client, "rv.prov@example.test")
    offer = await live_offer(client, provider, quantity=2)
    buyer = await register(client, "rv.buyer@example.test")
    stranger = await register(client, "rv.stranger@example.test")

    booked = await book_offer(client, buyer, offer["id"])
    too_early = await client.post("/reviews", headers=auth(buyer), json={
        "booking_id": booked["id"], "rating": 4, "comment": COMMENT})
    assert too_early.status_code == 400
    assert too_early.json()["error"]["code"] == "invalid_state_transition"
    assert "confirmed" in too_early.json()["error"]["message"]

    provider_voice = await client.post("/reviews", headers=auth(provider), json={
        "booking_id": booked["id"], "rating": 5})
    assert provider_voice.status_code == 403
    assert provider_voice.json()["error"]["message"] == (
        "Only the customer who used the service can review it")
    assert (await client.post("/reviews", headers=auth(stranger), json={
        "booking_id": booked["id"], "rating": 5})).status_code == 403
    assert (await client.post("/reviews", headers=auth(buyer), json={
        "booking_id": str(uuid.uuid4()), "rating": 5})).status_code == 404
    assert (await client.post("/reviews", json={"booking_id": booked["id"],
                                                "rating": 5})).status_code == 401

    for bad in (0, 6, "four"):
        rejected = await client.post("/reviews", headers=auth(buyer), json={
            "booking_id": booked["id"], "rating": bad})
        assert rejected.status_code == 422

    done = await delivered(client, provider, buyer, offer["id"])
    assert done["status"] == "completed"
    assert done["fulfillment"]["status"] == "completed"

    review = await add_review(client, buyer, done["id"])
    assert review["rating"] == 5 and review["status"] == "published"
    assert review["booking_id"] == done["id"]
    assert review["fulfillment_id"] == done["fulfillment"]["id"]
    assert review["offer_id"] == offer["id"]
    assert review["org_id"] == provider["user"]["active_org_id"]
    assert review["reviewer_id"] == buyer["user"]["id"]
    assert review["reviewer_name"] == "Test User"
    assert review["offer_title"] == "Bright meeting room for workshops"
    assert review["comment"] == COMMENT and review["provider_reply"] is None
    assert review["replied_at"] is None
    assert review["created_at"].endswith("Z")

    listed = await client.get(f"/offers/{offer['id']}", headers=auth(buyer))
    assert listed.json()["rating_avg"] == 5.0 and listed.json()["rating_count"] == 1

    twice = await client.post("/reviews", headers=auth(buyer), json={
        "booking_id": done["id"], "rating": 1})
    assert twice.status_code == 409
    assert twice.json()["error"]["code"] == "already_rated"
    assert twice.json()["error"]["details"]["review_id"] == review["id"]

    mismatch = await client.post("/reviews", headers=auth(buyer), json={
        "booking_id": done["id"], "rating": 4, "fulfillment_id": GHOST})
    assert mismatch.status_code == 400
    assert mismatch.json()["error"]["message"] == "fulfillment_id does not belong to this booking"
    assert (await client.post("/reviews", headers=auth(buyer), json={
        "booking_id": done["id"], "rating": 4,
        "fulfillment_id": done["fulfillment"]["id"]})).status_code == 409


async def test_review_visibility_lists_by_writer_and_by_provider(client):
    provider = await make_provider(client, "rv2.prov@example.test")
    offer = await live_offer(client, provider, quantity=2)
    buyer = await register(client, "rv2.buyer@example.test")
    stranger = await register(client, "rv2.stranger@example.test")

    assert (await reviews_of(client, buyer))["items"] == []
    assert (await reviews_of(client, provider, provider="true"))["items"] == []

    first = await delivered(client, provider, buyer, offer["id"])
    review = await add_review(client, buyer, first["id"], rating=4, comment=SHORT_COMMENT)

    mine = await reviews_of(client, buyer)
    assert mine["total"] == 1 and mine["items"][0]["id"] == review["id"]
    as_provider = await reviews_of(client, provider, provider="true")
    assert [r["id"] for r in as_provider["items"]] == [review["id"]]
    assert (await reviews_of(client, stranger))["items"] == []
    assert (await reviews_of(client, stranger, provider="true"))["items"] == []
    assert (await client.get(f"/reviews/{review['id']}", headers=auth(stranger))
            ).status_code == 403
    assert (await client.get(f"/reviews/{review['id']}", headers=auth(buyer))
            ).json()["id"] == review["id"]

    filters = await reviews_of(client, buyer, offer_id=offer["id"], rating=4,
                               status="published")
    assert filters["total"] == 1
    for params in ({"rating": 1}, {"status": "removed"}, {"offer_id": GHOST}):
        assert (await reviews_of(client, buyer, **params))["items"] == []
    bad_status = await client.get("/reviews", headers=auth(buyer),
                                  params={"status": "hidden"})
    assert bad_status.status_code == 400
    assert bad_status.json()["error"]["details"]["allowed"] == ["published",
                                                                "pending_moderation", "removed"]
    assert (await client.get("/reviews", headers=auth(buyer),
                             params={"rating": 9})).status_code == 422

    other = await delivered(client, provider, buyer, offer["id"])
    second = await add_review(client, buyer, other["id"], rating=2)
    page = await reviews_of(client, buyer, limit=1)
    assert page["total"] == 2 and page["limit"] == 1 and len(page["items"]) == 1
    rest = await reviews_of(client, buyer, limit=1, offset=1)
    assert {page["items"][0]["id"], rest["items"][0]["id"]} == {review["id"], second["id"]}
    assert page["items"][0]["created_at"] >= rest["items"][0]["created_at"]

    wall = await client.get(f"/offers/{offer['id']}/reviews", headers=auth(stranger))
    assert wall.status_code == 200, wall.text
    assert wall.json()["total"] == 2
    assert (await client.get(f"/offers/{GHOST}/reviews", headers=auth(stranger))
            ).status_code == 404
    assert (await client.get(f"/offers/{offer['id']}/reviews")).status_code == 401


async def test_provider_answers_once_and_moderation_rewrites_the_average(client, db):
    provider = await make_provider(client, "rv3.prov@example.test")
    offer = await live_offer(client, provider, quantity=3)
    buyer = await register(client, "rv3.buyer@example.test")
    other = await register(client, "rv3.other@example.test")

    first = await delivered(client, provider, buyer, offer["id"])
    review = await add_review(client, buyer, first["id"], rating=5)
    second = await add_review(client, other,
                              (await delivered(client, provider, other, offer["id"]))["id"],
                              rating=1)

    short = await client.post(f"/reviews/{review['id']}/reply", headers=auth(provider),
                              json={"provider_reply": "x"})
    assert short.status_code == 422

    answered = await client.post(f"/reviews/{review['id']}/reply", headers=auth(provider),
                                 json={"provider_reply": "Thank you, the projector is new."})
    assert answered.status_code == 200, answered.text
    body = answered.json()
    assert body["provider_reply"] == "Thank you, the projector is new."
    assert body["replied_at"].endswith("Z")

    again = await client.post(f"/reviews/{review['id']}/reply", headers=auth(provider),
                              json={"provider_reply": "Second thought."})
    assert again.status_code == 409
    customer_reply = await client.post(f"/reviews/{review['id']}/reply", headers=auth(buyer),
                                       json={"provider_reply": "Replying to myself."})
    assert customer_reply.status_code == 403
    assert (await client.post(f"/reviews/{GHOST}/reply", headers=auth(provider),
                              json={"provider_reply": "Ghost thread"})).status_code == 404

    blocked = await client.post(f"/reviews/{review['id']}/remove", headers=auth(provider))
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "ownership_required"

    admin = await staff(client, db, "rv3.admin@example.test", "platform_admin")
    removed = await client.post(f"/reviews/{review['id']}/remove", headers=auth(admin))
    assert removed.status_code == 200, removed.text
    assert removed.json()["status"] == "removed"
    double = await client.post(f"/reviews/{review['id']}/remove", headers=auth(admin))
    assert double.status_code == 409

    listed = await client.get(f"/offers/{offer['id']}", headers=auth(buyer))
    assert listed.json()["rating_avg"] == 1.0 and listed.json()["rating_count"] == 1
    wall = await client.get(f"/offers/{offer['id']}/reviews", headers=auth(buyer))
    assert [r["id"] for r in wall.json()["items"]] == [second["id"]]

    restored = await client.post(f"/reviews/{review['id']}/restore", headers=auth(admin))
    assert restored.status_code == 200 and restored.json()["status"] == "published"
    averaged = await client.get(f"/offers/{offer['id']}", headers=auth(buyer))
    assert averaged.json()["rating_avg"] == 3.0 and averaged.json()["rating_count"] == 2
    assert (await client.post(f"/reviews/{GHOST}/restore", headers=auth(admin))
            ).status_code == 404


async def test_a_live_booking_dispute_moves_the_booking_and_the_queue(client):
    provider = await make_provider(client, "ds.prov@example.test")
    offer = await live_offer(client, provider, quantity=3)
    buyer = await register(client, "ds.buyer@example.test")
    stranger = await register(client, "ds.stranger@example.test")

    booked = await book_offer(client, buyer, offer["id"])
    held = await hold(client, buyer, offer["id"],
                      start=f"{booked['window_start'][:11]}14:00:00Z",
                      end=f"{booked['window_start'][:11]}16:00:00Z")
    assert (await client.post("/disputes", headers=auth(buyer), json={
        "booking_id": held["id"], "kind": "quality", "description": DESCRIPTION}
    )).status_code == 400

    ghost = await client.post("/disputes", headers=auth(buyer), json={
        "booking_id": GHOST, "kind": "quality", "description": DESCRIPTION})
    assert ghost.status_code == 404
    assert (await client.post("/disputes", headers=auth(stranger), json={
        "booking_id": booked["id"], "kind": "quality", "description": DESCRIPTION}
    )).status_code == 403
    assert (await client.post("/disputes", headers=auth(buyer), json={
        "booking_id": booked["id"], "kind": "broken", "description": DESCRIPTION}
    )).status_code == 422
    assert (await client.post("/disputes", headers=auth(buyer), json={
        "booking_id": booked["id"], "kind": "quality", "description": "too short"}
    )).status_code == 422

    opened = await open_dispute(client, buyer, booked["id"])
    assert opened["status"] == "open" and opened["refund_cents"] == 0
    assert opened["booking_id"] == booked["id"]
    assert opened["org_id"] == provider["user"]["active_org_id"]
    assert opened["complainant_id"] == buyer["user"]["id"]
    assert opened["complainant_name"] == "Test User" and opened["org_name"] == "Room Owner Org"
    assert opened["description"] == DESCRIPTION
    assert opened["resolution_note"] is None and opened["resolved_by"] is None
    assert opened["resolved_at"] is None and opened["created_at"].endswith("Z")

    after = await client.get(f"/bookings/{booked['id']}", headers=auth(buyer))
    assert after.json()["status"] == "disputed"

    duplicate = await client.post("/disputes", headers=auth(buyer), json={
        "booking_id": booked["id"], "kind": "payment", "description": DESCRIPTION})
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["details"]["dispute_id"] == opened["id"]

    provider_side = await open_dispute(client, provider,
                                       (await book_offer(client, buyer, offer["id"]))["id"],
                                       kind="payment")
    assert provider_side["complainant_id"] == provider["user"]["id"]

    mine = await disputes_of(client, buyer)
    assert mine["total"] == 2
    assert {d["id"] for d in mine["items"]} == {opened["id"], provider_side["id"]}
    as_provider = await disputes_of(client, provider, provider="true")
    assert as_provider["total"] == 2
    assert (await disputes_of(client, stranger))["items"] == []
    assert (await disputes_of(client, stranger, provider="true"))["items"] == []
    assert (await client.get(f"/disputes/{opened['id']}", headers=auth(stranger))
            ).status_code == 403
    assert (await client.get(f"/disputes/{opened['id']}", headers=auth(provider))
            ).json()["id"] == opened["id"]
    assert (await client.get(f"/disputes/{GHOST}", headers=auth(buyer))).status_code == 404

    filtered = await disputes_of(client, buyer, kind="payment", status="open")
    assert [d["id"] for d in filtered["items"]] == [provider_side["id"]]
    bad_kind = await client.get("/disputes", headers=auth(buyer), params={"kind": "legal"})
    assert bad_kind.status_code == 400
    assert bad_kind.json()["error"]["details"]["allowed"] == ["quality", "no_show", "payment",
                                                              "damage", "other"]
    bad_status = await client.get("/disputes", headers=auth(buyer), params={"status": "won"})
    assert bad_status.status_code == 400
    assert bad_status.json()["error"]["code"] == "validation_error"


async def test_support_resolution_moves_the_money_the_decision_names(client, db):
    provider = await make_provider(client, "ds2.prov@example.test")
    offer = await live_offer(client, provider, quantity=4)
    buyer = await register(client, "ds2.buyer@example.test")
    support = await staff(client, db, "ds2.support@example.test", "support")
    assert "support" in (await client.get("/auth/me", headers=auth(support))).json()["roles"]

    partial = await book_offer(client, buyer, offer["id"])
    await pay(client, buyer, partial)
    dispute = await open_dispute(client, buyer, partial["id"])

    not_support = await resolve(client, provider, dispute["id"], "resolved_partial",
                               refund_cents=1000)
    assert not_support[0] == 403
    assert not_support[1]["error"]["code"] == "ownership_required"

    review_state = await resolve(client, support, dispute["id"], "under_review")
    assert review_state[0] == 200 and review_state[1]["status"] == "under_review"
    assert review_state[1]["resolution_note"] == NOTE
    assert review_state[1]["resolved_at"] is None

    missing_amount = await resolve(client, support, dispute["id"], "resolved_partial")
    assert missing_amount[0] == 400
    assert missing_amount[1]["error"]["message"] == "resolved_partial needs refund_cents"

    code, settled = await resolve(client, support, dispute["id"], "resolved_partial",
                                  refund_cents=4000)
    assert code == 200, settled
    assert settled["status"] == "resolved_partial" and settled["refund_cents"] == 4000
    assert settled["refunded_cents"] == 4000
    assert settled["resolved_by"] == support["user"]["id"]
    assert settled["resolved_at"].endswith("Z")

    order = (await client.get(f"/orders/{partial['order_id']}", headers=auth(buyer))).json()
    assert order["payment_status"] == "partially_refunded" and order["refunded_cents"] == 4000
    booking = (await client.get(f"/bookings/{partial['id']}", headers=auth(buyer))).json()
    assert booking["payment_status"] == "partially_refunded"
    # A partial refund keeps the service delivered: disputed -> completed, never cancelled.
    assert booking["status"] == "completed"
    assert booking["fulfillment"]["status"] == "completed"

    again = await resolve(client, support, dispute["id"], "closed")
    assert again[0] == 409
    assert again[1]["error"]["code"] == "conflict"
    assert (await resolve(client, support, GHOST, "closed"))[0] == 404

    full = await book_offer(client, buyer, offer["id"])
    await pay(client, buyer, full)
    full_dispute = await open_dispute(client, buyer, full["id"])
    code, refunded_all = await resolve(client, support, full_dispute["id"], "resolved_refund")
    assert code == 200, refunded_all
    assert refunded_all["refund_cents"] == 10000 and refunded_all["refunded_cents"] == 10000
    assert refunded_all["status"] == "resolved_refund"
    after_order = (await client.get(f"/orders/{full['order_id']}", headers=auth(buyer))).json()
    assert after_order["payment_status"] == "refunded" and after_order["status"] == "refunded"
    after_booking = (await client.get(f"/bookings/{full['id']}", headers=auth(buyer))).json()
    assert after_booking["status"] == "cancelled"
    assert after_booking["payment_status"] == "refunded"
    assert after_booking["fulfillment"]["status"] == "failed"

    blameless = await open_dispute(client, buyer,
                                   (await delivered(client, provider, buyer, offer["id"]))["id"])
    assert (await client.get(f"/bookings/{blameless['booking_id']}", headers=auth(buyer))
            ).json()["status"] == "completed"
    paid_order = (await client.get(f"/disputes/{blameless['id']}", headers=auth(buyer))).json()
    assert paid_order["status"] == "open"
    with_money = await resolve(client, support, blameless["id"], "resolved_no_fault",
                               refund_cents=2000)
    assert with_money[0] == 400
    assert with_money[1]["error"]["code"] == "validation_error"
    code, clean = await resolve(client, support, blameless["id"], "resolved_no_fault")
    assert code == 200 and clean["refund_cents"] == 0
    assert clean["status"] == "resolved_no_fault"
    # The booking was already delivered, so a no-fault finding leaves it completed.
    stayed = (await client.get(f"/bookings/{blameless['booking_id']}", headers=auth(buyer))
              ).json()
    assert stayed["status"] == "completed"


async def test_admin_queue_sees_every_dispute_across_tenants(client, db):
    first_provider = await make_provider(client, "ds3.p1@example.test", name="First")
    first_offer = await live_offer(client, first_provider, quantity=2)
    second_provider = await make_provider(client, "ds3.p2@example.test", name="Second")
    second_offer = await live_offer(client, second_provider, quantity=2)
    buyer = await register(client, "ds3.buyer@example.test")
    other = await register(client, "ds3.other@example.test")
    admin = await staff(client, db, "ds3.admin@example.test", "platform_admin")

    one = await open_dispute(client, buyer, (await book_offer(client, buyer, first_offer["id"]))["id"])
    two = await open_dispute(client, other,
                             (await book_offer(client, other, second_offer["id"]))["id"])

    assert (await disputes_of(client, buyer))["total"] == 1
    assert (await disputes_of(client, first_provider, provider="true"))["total"] == 1
    queue = await disputes_of(client, admin)
    assert queue["total"] == 2
    assert {d["id"] for d in queue["items"]} == {one["id"], two["id"]}
    assert (await disputes_of(client, admin, org_id=one["org_id"]))["total"] == 1
    assert (await disputes_of(client, admin, status="open"))["total"] == 2
    assert (await disputes_of(client, admin, status="closed"))["items"] == []
    assert (await client.get(f"/disputes/{two['id']}", headers=auth(admin))).status_code == 200

    code, settled = await resolve(client, admin, two["id"], "closed")
    assert code == 200 and settled["status"] == "closed"
    assert (await disputes_of(client, admin, status="closed"))["total"] == 1
    # An unpaid booking has no money to give back, so a refund decision still closes cleanly.
    assert settled["refund_cents"] == 0
