"""§5.4/§5.5 booking surface: hold, conversion, state machine, tenancy, capacity accounting."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tests.test_auth_api import auth, register
from tests.test_capacity_api import DAY, add_rule, make_provider, make_resource

START = f"{DAY}T10:00:00Z"
END = f"{DAY}T12:00:00Z"
OTHER_DAY = (datetime.fromisoformat(DAY).date() + timedelta(days=7)).isoformat()


async def live_offer(client, tokens, *, quantity: int = 2, name: str = "Bookable Room",
                     booking_mode: str = "instant", category_key: str = "meeting_room",
                     **offer_changes) -> dict:
    """A published offer backed by an availability rule — the smallest bookable thing."""
    resource = await make_resource(client, tokens, name=name, category_key=category_key,
                                   definitions=[{"name": "Hour slot", "unit_label": "hour",
                                                 "min_quantity": 1, "max_quantity": quantity}])
    definition_id = resource["definitions"][0]["id"]
    await add_rule(client, tokens, resource["id"], definition_id, quantity=quantity)
    offer = await client.post("/offers", headers=auth(tokens), json={
        "definition_id": definition_id,
        "title": "Bright meeting room for workshops",
        "description": "A corner room with a projector, whiteboard and eight chairs.",
        "pricing_mode": "per_unit_time", "unit_amount_cents": 5000, "currency": "USD",
        "min_lead_time_minutes": 30, "min_duration_minutes": 60, "max_duration_minutes": 240,
        "min_quantity": 1, "max_quantity": quantity, "booking_mode": booking_mode,
        "hold_minutes": 15, **offer_changes})
    assert offer.status_code == 201, offer.text
    offer_id = offer.json()["id"]
    pub = await client.post(f"/offers/{offer_id}/publish", headers=auth(tokens))
    assert pub.status_code == 200, pub.text
    assert await free_quantity(client, tokens, definition_id) == quantity, "offer is not bookable"
    return {**pub.json(), "resource_id": resource["id"], "definition_id": definition_id}


async def hold(client, tokens, offer_id: str, *, start: str = START, end: str = END,
               quantity: int = 1, **changes) -> dict:
    resp = await client.post("/bookings/hold", headers=auth(tokens), json={
        "offer_id": offer_id, "window_start": start, "window_end": end,
        "quantity": quantity, **changes})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def book(client, tokens, hold_id: str, **changes) -> dict:
    resp = await client.post("/bookings", headers=auth(tokens),
                             json={"hold_id": hold_id, **changes})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def bookings_of(client, tokens, **params) -> dict:
    resp = await client.get("/bookings", headers=auth(tokens), params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def free_quantity(client, tokens, definition_id: str) -> int | None:
    resp = await client.get("/availability/free", headers=auth(tokens), params={
        "definition_id": definition_id, "from": START, "to": END})
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    return items[0]["free_quantity"] if items else None


async def test_hold_reserves_capacity_and_is_visible_to_both_parties(client):
    provider = await make_provider(client, "bk.prov@example.test")
    offer = await live_offer(client, provider, quantity=2)
    buyer = await register(client, "bk.buyer@example.test")

    assert await free_quantity(client, buyer, offer["definition_id"]) == 2

    held = await hold(client, buyer, offer["id"])
    assert held["status"] == "hold"
    assert held["payment_status"] == "unpaid"
    assert held["unit_amount_cents"] == 10000  # per_unit_time folds the 2h window into the unit
    assert held["total_cents"] == 10000
    assert held["hold_expires_at"].endswith("Z")
    assert held["offer_title"] == "Bright meeting room for workshops"
    assert held["resource_name"] == "Bookable Room"
    assert held["org_name"] == "Room Owner Org"
    assert held["customer_name"] == "Test User"
    assert held["order_id"] is None  # money state starts with the booking, not the hold
    assert held["definition_id"] == offer["definition_id"]

    assert await free_quantity(client, buyer, offer["definition_id"]) == 1

    mine = await bookings_of(client, buyer)
    assert [b["id"] for b in mine["items"]] == [held["id"]]
    assert mine["total"] == 1

    as_provider = await client.get(f"/bookings/{held['id']}", headers=auth(provider))
    assert as_provider.status_code == 200
    workspace = await bookings_of(client, provider, provider="true")
    assert [b["id"] for b in workspace["items"]] == [held["id"]]


async def test_hold_is_replayed_by_fingerprint_and_idempotency_key(client):
    provider = await make_provider(client, "bk.replay@example.test")
    offer = await live_offer(client, provider, quantity=3)
    buyer = await register(client, "bk.replaybuyer@example.test")
    fingerprint = "5f0f0f0f-5f0f-4f0f-8f0f-5f0f0f0f0f0f"

    body = {"offer_id": offer["id"], "window_start": START, "window_end": END,
            "quantity": 1, "request_fingerprint": fingerprint}
    headers = {**auth(buyer), "Idempotency-Key": "hold-k1"}
    first = await client.post("/bookings/hold", json=body, headers=headers)
    replay = await client.post("/bookings/hold", json=body, headers=headers)
    assert first.status_code == replay.status_code == 201
    assert first.json()["id"] == replay.json()["id"]

    # A fresh key but the same client fingerprint still reuses the live hold (§5.4).
    retried = await client.post("/bookings/hold", json=body, headers=auth(buyer))
    assert retried.status_code == 201 and retried.json()["id"] == first.json()["id"]
    assert len((await bookings_of(client, buyer))["items"]) == 1
    assert await free_quantity(client, buyer, offer["definition_id"]) == 2


async def test_instant_booking_from_hold_opens_the_order_and_fulfilment(client):
    provider = await make_provider(client, "bk.instant@example.test")
    offer = await live_offer(client, provider, quantity=2)
    buyer = await register(client, "bk.instantbuyer@example.test")
    held = await hold(client, buyer, offer["id"])

    body = await book(client, buyer, held["id"])
    assert body["status"] == "confirmed"
    assert body["payment_status"] == "unpaid"
    assert body["hold_expires_at"] is None
    assert body["confirmed_at"].endswith("Z")
    assert body["order_id"] == body["order"]["id"]
    assert body["order"]["booking_id"] == body["id"]
    assert body["order"]["total_cents"] == 10000
    assert body["order"]["payment_status"] == "unpaid"
    assert body["order"]["line_items"][0]["qty"] == 1
    assert body["fulfillment"]["status"] == "pending"

    used = await client.get(f"/bookings/{held['id']}", headers=auth(buyer))
    assert used.json()["status"] == "cancelled"  # the hold is consumed, capacity lives on the booking
    assert await free_quantity(client, buyer, offer["definition_id"]) == 1

    hold_events = (await client.get(f"/bookings/{held['id']}/timeline",
                                    headers=auth(buyer))).json()["items"]
    assert [e["to_status"] for e in hold_events] == ["hold", "cancelled"]
    events = (await client.get(f"/bookings/{body['id']}/timeline",
                               headers=auth(buyer))).json()["items"]
    assert [e["to_status"] for e in events] == ["confirmed"]
    assert events[0]["from_status"] is None


async def test_direct_booking_without_a_hold(client):
    provider = await make_provider(client, "bk.direct@example.test")
    offer = await live_offer(client, provider, quantity=3)
    buyer = await register(client, "bk.directbuyer@example.test")

    resp = await client.post("/bookings", headers=auth(buyer), json={
        "offer_id": offer["id"], "window_start": START, "window_end": END, "quantity": 2})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "confirmed" and body["quantity"] == 2
    assert body["total_cents"] == 20000
    assert await free_quantity(client, buyer, offer["definition_id"]) == 1

    incomplete = await client.post("/bookings", headers=auth(buyer), json={"quantity": 1})
    assert incomplete.status_code == 400
    assert incomplete.json()["error"]["code"] == "validation_error"

    closed_offer = await client.post(f"/offers/{offer['id']}/close", headers=auth(provider))
    assert closed_offer.status_code == 200
    refused = await client.post("/bookings", headers=auth(buyer), json={
        "offer_id": offer["id"], "window_start": START, "window_end": END, "quantity": 1})
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "conflict"


async def test_request_confirm_offer_waits_for_the_provider(client):
    provider = await make_provider(client, "bk.req@example.test")
    offer = await live_offer(client, provider, quantity=1, booking_mode="request_confirm",
                             name="Request Room")
    buyer = await register(client, "bk.reqbuyer@example.test")

    held = await hold(client, buyer, offer["id"])
    body = await book(client, buyer, held["id"])
    assert body["status"] == "draft"
    assert body["order_id"] == body["order"]["id"]  # money is quoted before acceptance

    too_early = await client.post(f"/bookings/{body['id']}/start", headers=auth(provider))
    assert too_early.status_code == 400
    assert too_early.json()["error"]["code"] == "invalid_state_transition"

    customer_may_not_accept = await client.post(f"/bookings/{body['id']}/confirm",
                                                headers=auth(buyer))
    assert customer_may_not_accept.status_code == 200  # §8: the pay-path confirms too

    pending = await bookings_of(client, provider, provider="true", status="confirmed")
    assert [b["id"] for b in pending["items"]] == [body["id"]]


async def test_over_subscribed_drafts_are_refused_at_confirmation(client):
    """A `draft` holds no capacity units, so acceptance is where over-subscription is caught."""
    provider = await make_provider(client, "bk.over@example.test")
    offer = await live_offer(client, provider, quantity=1, booking_mode="request_confirm",
                             name="One Slot Room")
    first = await register(client, "bk.over1@example.test")
    second = await register(client, "bk.over2@example.test")

    a = await book(client, first, (await hold(client, first, offer["id"]))["id"])
    # A's conversion frees the hold's units again, because a draft is not committed capacity.
    b = await book(client, second, (await hold(client, second, offer["id"]))["id"])
    assert a["status"] == b["status"] == "draft"

    accepted = await client.post(f"/bookings/{a['id']}/confirm", headers=auth(provider))
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "confirmed"

    refused = await client.post(f"/bookings/{b['id']}/confirm", headers=auth(provider))
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "no_availability"
    assert refused.json()["error"]["details"]["available"] == []
    assert (await client.get(f"/bookings/{b['id']}", headers=auth(second))).json()[
        "status"] == "draft"  # the refusal leaves the draft decidable (cancel is still possible)

    freed = await client.post(f"/bookings/{a['id']}/cancel", headers=auth(provider),
                              json={"reason": "giving the slot away"})
    assert freed.status_code == 200
    again = await client.post(f"/bookings/{b['id']}/confirm", headers=auth(provider))
    assert again.status_code == 200, again.text
    assert again.json()["status"] == "confirmed"


async def test_provider_runs_the_service_and_a_customer_cannot(client):
    provider = await make_provider(client, "bk.flow@example.test")
    offer = await live_offer(client, provider, quantity=2)
    buyer = await register(client, "bk.flowbuyer@example.test")
    body = await book(client, buyer, (await hold(client, buyer, offer["id"]))["id"])
    booking_id = body["id"]

    wrong_role = await client.post(f"/bookings/{booking_id}/start", headers=auth(buyer))
    assert wrong_role.status_code == 403
    assert wrong_role.json()["error"]["code"] == "ownership_required"

    started = await client.post(f"/bookings/{booking_id}/start", headers=auth(provider))
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "in_progress"
    assert started.json()["started_at"].endswith("Z")
    assert started.json()["fulfillment"]["status"] == "in_progress"

    mid_service_cancel = await client.post(f"/bookings/{booking_id}/cancel",
                                           headers=auth(provider),
                                           json={"reason": "trying an illegal edge"})
    assert mid_service_cancel.status_code == 400
    assert mid_service_cancel.json()["error"]["code"] == "invalid_state_transition"

    done = await client.post(f"/bookings/{booking_id}/complete", headers=auth(provider))
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "completed"
    assert done.json()["completed_at"].endswith("Z")
    assert done.json()["fulfillment"]["status"] == "completed"
    assert done.json()["review_submitted"] is False

    too_late = await client.post(f"/bookings/{booking_id}/cancel", headers=auth(buyer),
                                 json={"reason": "too late to cancel"})
    assert too_late.status_code == 400

    events = (await client.get(f"/bookings/{booking_id}/timeline",
                               headers=auth(buyer))).json()["items"]
    assert {e["to_status"] for e in events} == {"confirmed", "in_progress", "completed"}
    assert await free_quantity(client, buyer, offer["definition_id"]) == 2


async def test_capacity_ceiling_and_window_rules_are_enforced(client):
    provider = await make_provider(client, "bk.full@example.test")
    offer = await live_offer(client, provider, quantity=1, name="Single Room")
    buyer = await register(client, "bk.fullbuyer@example.test")
    first = await hold(client, buyer, offer["id"])

    double_booked = await client.post("/bookings/hold", headers=auth(buyer), json={
        "offer_id": offer["id"], "window_start": START, "window_end": END, "quantity": 1})
    assert double_booked.status_code == 409
    assert double_booked.json()["error"]["code"] == "no_availability"
    detail = double_booked.json()["error"]["details"]
    assert detail["window_start"] == START  # §1 Z-shape even inside error payloads
    assert detail["available"] == []

    outside = await client.post("/bookings/hold", headers=auth(buyer), json={
        "offer_id": offer["id"], "window_start": f"{DAY}T19:00:00Z",
        "window_end": f"{DAY}T20:00:00Z", "quantity": 1})
    assert outside.status_code == 409
    assert outside.json()["error"]["code"] == "no_availability"

    too_long = await client.post("/bookings/hold", headers=auth(buyer), json={
        "offer_id": offer["id"], "window_start": START, "window_end": f"{DAY}T23:30:00Z",
        "quantity": 1})
    assert too_long.status_code == 400
    assert too_long.json()["error"]["details"]["max_duration_minutes"] == 240

    too_many = await client.post("/bookings/hold", headers=auth(buyer), json={
        "offer_id": offer["id"], "window_start": START, "window_end": END, "quantity": 4})
    assert too_many.status_code == 400
    assert too_many.json()["error"]["details"]["max_quantity"] == 1

    now = datetime.now(timezone.utc)
    stale = (now - timedelta(hours=3)).isoformat(timespec="seconds").replace("+00:00", "Z")
    in_the_past = await client.post("/bookings/hold", headers=auth(buyer), json={
        "offer_id": offer["id"], "window_start": stale, "window_end": START, "quantity": 1})
    assert in_the_past.status_code == 400
    assert in_the_past.json()["error"]["code"] == "validation_error"

    unknown = await client.post("/bookings/hold", headers=auth(buyer), json={
        "offer_id": "11111111-1111-1111-1111-111111111111", "window_start": START,
        "window_end": END, "quantity": 1})
    assert unknown.status_code == 404

    cancelled = await client.post(f"/bookings/{first['id']}/cancel", headers=auth(buyer),
                                 json={"reason": "changed plans"})
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["refund_cents"] == 0  # nothing was paid
    assert await free_quantity(client, buyer, offer["definition_id"]) == 1


async def test_expired_hold_cannot_be_booked(client, db):
    from sqlalchemy import update

    from app.models.booking import Booking

    provider = await make_provider(client, "bk.exp@example.test")
    offer = await live_offer(client, provider, quantity=2)
    buyer = await register(client, "bk.expbuyer@example.test")
    held = await hold(client, buyer, offer["id"])

    await db.execute(update(Booking).where(Booking.id == held["id"]).values(
        hold_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
    await db.commit()

    resp = await client.post("/bookings", headers=auth(buyer), json={"hold_id": held["id"]})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "hold_expired"

    # §5.5 assigns the flip itself to the sweeper, so the expired row lands after a sweep.
    from app.core.jobs import expire_holds

    assert (await expire_holds(db))["expired"] == 1
    await db.commit()
    assert (await client.get(f"/bookings/{held['id']}", headers=auth(buyer))).json()[
        "status"] == "expired"
    assert await free_quantity(client, buyer, offer["definition_id"]) == 2

    foreign_hold = await hold(client, provider, offer["id"])
    steal = await client.post("/bookings", headers=auth(buyer), json={"hold_id": foreign_hold["id"]})
    assert steal.status_code == 403


async def test_bookings_are_not_readable_across_tenants(client):
    owner = await make_provider(client, "bk.own@example.test")
    offer = await live_offer(client, owner, quantity=2)
    buyer = await register(client, "bk.ownbuyer@example.test")
    held = await hold(client, buyer, offer["id"])

    stranger = await register(client, "bk.stranger@example.test")
    got = await client.get(f"/bookings/{held['id']}", headers=auth(stranger))
    assert got.status_code == 403
    assert got.json()["error"]["code"] == "ownership_required"
    assert (await bookings_of(client, stranger))["items"] == []

    nosy_provider = await make_provider(client, "bk.nosy@example.test")
    denied = await client.get(f"/bookings/{held['id']}", headers=auth(nosy_provider))
    assert denied.status_code == 403
    assert (await bookings_of(client, nosy_provider, provider="true"))["items"] == []
    cannot_start = await client.post(f"/bookings/{held['id']}/start", headers=auth(nosy_provider))
    assert cannot_start.status_code == 403

    anonymous = await client.get("/bookings")
    assert anonymous.status_code == 401
    missing = await client.get("/bookings/11111111-1111-1111-1111-111111111111",
                               headers=auth(buyer))
    assert missing.status_code == 404


async def test_booking_list_filters_by_status_offer_and_window(client):
    provider = await make_provider(client, "bk.filter@example.test")
    offer = await live_offer(client, provider, quantity=4)
    buyer = await register(client, "bk.filterbuyer@example.test")

    near = await hold(client, buyer, offer["id"])
    far = await hold(client, buyer, offer["id"], start=f"{OTHER_DAY}T10:00:00Z",
                     end=f"{OTHER_DAY}T12:00:00Z")

    only_holds = await bookings_of(client, buyer, status="hold")
    assert {b["id"] for b in only_holds["items"]} == {near["id"], far["id"]}

    bad_status = await client.get("/bookings", headers=auth(buyer), params={"status": "nope"})
    assert bad_status.status_code == 400
    assert bad_status.json()["error"]["code"] == "validation_error"

    ranged = await bookings_of(client, buyer, **{"from": DAY, "to": DAY})
    assert [b["id"] for b in ranged["items"]] == [near["id"]]

    half_range = await client.get("/bookings", headers=auth(buyer), params={"from": DAY})
    assert half_range.status_code == 400

    by_offer = await bookings_of(client, buyer, offer_id=offer["id"], limit=1)
    assert len(by_offer["items"]) == 1 and by_offer["total"] == 2
    assert by_offer["limit"] == 1
