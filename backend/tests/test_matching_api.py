"""§5.3/§8 reverse marketplace: demand scoring, provider answers, tenancy, expiry."""
from __future__ import annotations

from tests.test_auth_api import auth, register
from tests.test_booking_api import END, START, bookings_of, free_quantity, hold, live_offer
from tests.test_capacity_api import DAY, add_rule, category_id_of, make_provider, make_resource
from tests.test_marketplace_api import make_definition, make_offer, publish

DEMAND_TEXT = ("Looking for a bright workshop room with a projector and a whiteboard "
               "for a training session.")
GHOST = "11111111-1111-1111-1111-111111111111"


async def post_demand(client, tokens, **changes) -> dict:
    body = {"description": DEMAND_TEXT, "desired_start": START, "desired_end": END,
            "quantity": 1, "budget_min_cents": 1000, "budget_max_cents": 6000,
            "lat": 35.70, "lon": 51.40}
    body.update(changes)
    resp = await client.post("/demands", json=body, headers=auth(tokens))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def demands_of(client, tokens, **params) -> dict:
    resp = await client.get("/demands", headers=auth(tokens), params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def demand_of(client, tokens, demand_id: str) -> dict:
    resp = await client.get(f"/demands/{demand_id}", headers=auth(tokens))
    assert resp.status_code == 200, resp.text
    return resp.json()


async def matches_of(client, tokens, demand_id: str, **params) -> dict:
    resp = await client.get(f"/demands/{demand_id}/matches", headers=auth(tokens), params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def all_matches(client, tokens, **params) -> dict:
    resp = await client.get("/matches", headers=auth(tokens), params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def listed_offer(client, tokens, *, name: str, title: str, description: str,
                       unit_amount_cents: int, lat: float | None = None,
                       lon: float | None = None, quantity: int = 2) -> dict:
    """A published offer on a geotagged resource — the smallest matchable listing."""
    resource = await make_resource(client, tokens, name=name, lat=lat, lon=lon)
    definition = await make_definition(client, tokens, resource["id"], max_quantity=4)
    await add_rule(client, tokens, resource["id"], definition["id"], quantity=quantity)
    offer = await make_offer(client, tokens, definition["id"], title=title,
                             description=description, unit_amount_cents=unit_amount_cents,
                             max_quantity=quantity)
    return {**(await publish(client, tokens, offer["id"])),
            "resource_id": resource["id"], "definition_id": definition["id"]}


async def test_scorer_ranks_by_capacity_price_distance_and_text(client):
    near = await make_provider(client, "mt.near@example.test", name="Near Rooms")
    far = await make_provider(client, "mt.far@example.test", name="Far Rooms")
    category = await category_id_of(client, near)

    good = await listed_offer(
        client, near, name="Workshop Loft", lat=35.705, lon=51.405, unit_amount_cents=2000,
        title="Bright workshop room with projector and whiteboard",
        description="A training space with a projector, whiteboard and chairs for a session.")
    pricey = await listed_offer(
        client, near, name="Over Budget Loft", lat=35.705, lon=51.405, unit_amount_cents=9000,
        title="Bright workshop room with projector and whiteboard",
        description="A training space with a projector, whiteboard and chairs for a session.")
    distant = await listed_offer(
        client, far, name="Corner Desk", lat=35.900, lon=51.700, unit_amount_cents=5500,
        title="Overflow coworking corner",
        description="A quiet corner far from the centre with fast internet and a coffee machine.")

    customer = await register(client, "mt.scorer@example.test")
    demand = await post_demand(client, customer, category_id=category)
    assert demand["status"] == "open" and demand["match_count"] == 0
    assert demand["desired_start"] == START and demand["created_at"].endswith("Z")

    body = await matches_of(client, customer, demand["id"])
    assert [m["offer"]["id"] for m in body["items"]] == [good["id"], distant["id"]]
    assert body["total"] == 2
    assert pricey["id"] not in [m["offer"]["id"] for m in body["items"]]

    first, second = body["items"]
    assert first["score"] > second["score"]
    assert 0 <= first["score"] <= 1000
    assert first["status"] == "suggested" and first["booking_id"] is None
    assert first["offer"]["city"] == "Tehran" and first["offer"]["status"] == "published"
    assert first["offer"]["resource_name"] == "Workshop Loft"
    assert first["offer"]["rating_count"] == 0

    near_text = " | ".join(first["reasons"])
    assert "exact category: Meeting room" in near_text
    assert "2 units available, 1 needed" in near_text
    assert "km from the requested place" in near_text
    assert "no published reviews yet" in near_text
    assert "listing text matches" in near_text
    assert "inside your 6000 ceiling" in near_text
    far_text = " | ".join(second["reasons"])
    assert "no keyword overlap with your description" in far_text

    # The demand read-model now carries the proposal count without a second request.
    refreshed = await demand_of(client, customer, demand["id"])
    assert refreshed["match_count"] == 2 and refreshed["category_label"] == "Meeting room"

    # Re-scoring is idempotent: the same rows, no duplicates from the ON CONFLICT upsert.
    again = await matches_of(client, customer, demand["id"])
    assert [m["id"] for m in again["items"]] == [m["id"] for m in body["items"]]

    # A demand the listings cannot serve produces nothing at all.
    needs_five = await post_demand(client, customer, category_id=category, quantity=5)
    assert (await matches_of(client, customer, needs_five["id"]))["items"] == []


async def test_provider_acceptance_produces_a_draft_the_customer_confirms(client):
    provider = await make_provider(client, "mt.accept@example.test")
    offer = await live_offer(client, provider, quantity=2, name="Matched Room")
    customer = await register(client, "mt.acceptcust@example.test")
    demand = await post_demand(
        client, customer, category_id=await category_id_of(client, customer), lat=None, lon=None)

    match = (await matches_of(client, customer, demand["id"]))["items"][0]
    assert match["offer"]["id"] == offer["id"]

    # The provider works an inbox of suggestions, with the demand attached.
    inbox = await all_matches(client, provider, provider="true", status="suggested")
    assert [m["id"] for m in inbox["items"]] == [match["id"]]
    assert inbox["items"][0]["demand"]["id"] == demand["id"]
    inbox_demands = await demands_of(client, provider, provider="true")
    assert [d["id"] for d in inbox_demands["items"]] == [demand["id"]]

    accepted = await client.post(f"/matches/{match['id']}/accept", headers=auth(provider))
    assert accepted.status_code == 200, accepted.text
    row = accepted.json()
    assert row["status"] == "accepted" and row["provider_action_at"].endswith("Z")
    booking_id = row["booking_id"]
    assert booking_id is not None

    assert (await demand_of(client, customer, demand["id"]))["status"] == "matched"
    # A draft commits no capacity (§5.5), so the units are still on the market.
    assert await free_quantity(client, provider, offer["definition_id"]) == 2

    rival = await register(client, "mt.rival@example.test")
    rival_hold = await hold(client, rival, offer["id"], quantity=2)
    # No free window is left at all, so the availability read-model is empty.
    assert await free_quantity(client, provider, offer["definition_id"]) is None

    refused = await client.post(f"/bookings/{booking_id}/confirm", headers=auth(customer))
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "no_availability"
    assert (await bookings_of(client, customer))["items"][0]["status"] == "draft"

    freed = await client.post(f"/bookings/{rival_hold['id']}/cancel", headers=auth(rival),
                              json={"reason": "changed plans"})
    assert freed.status_code == 200, freed.text

    confirmed = await client.post(f"/bookings/{booking_id}/confirm", headers=auth(customer))
    assert confirmed.status_code == 200, confirmed.text
    booking = confirmed.json()
    assert booking["status"] == "confirmed" and booking["payment_status"] == "unpaid"
    assert booking["quantity"] == 1 and booking["total_cents"] == 10000
    assert booking["order_id"] == booking["order"]["id"]

    # The match points at the booking it produced, for both sides.
    seen = await client.get(f"/matches/{match['id']}", headers=auth(customer))
    assert seen.json()["booking_id"] == booking_id
    assert seen.json()["demand"]["id"] == demand["id"]

    as_provider = await bookings_of(client, provider, provider="true", status="confirmed")
    assert [b["id"] for b in as_provider["items"]] == [booking_id]


async def test_only_the_providing_organization_answers_a_match(client):
    provider = await make_provider(client, "mt.tenancy@example.test")
    category = await category_id_of(client, provider)
    first = await listed_offer(client, provider, name="Room One", lat=35.71, lon=51.41,
                               unit_amount_cents=2000, title="Bright workshop room",
                               description="A training room with a projector and a whiteboard.")
    second = await listed_offer(client, provider, name="Room Two", lat=35.72, lon=51.42,
                                unit_amount_cents=2500, title="Bright workshop room two",
                                description="A second training room with a projector and whiteboard.")

    customer = await register(client, "mt.tenancycust@example.test")
    demand = await post_demand(client, customer, category_id=category)
    matches = (await matches_of(client, customer, demand["id"]))["items"]
    assert len(matches) == 2

    customer_try = await client.post(f"/matches/{matches[0]['id']}/accept", headers=auth(customer))
    assert customer_try.status_code == 403
    assert customer_try.json()["error"]["code"] == "ownership_required"

    stranger = await register(client, "mt.tenancynosey@example.test")
    for verb in ("accept", "reject"):
        denied = await client.post(f"/matches/{matches[0]['id']}/{verb}", headers=auth(stranger))
        assert denied.status_code == 403
    assert (await all_matches(client, stranger))["items"] == []
    assert (await demands_of(client, stranger))["items"] == []

    declined = await client.post(f"/matches/{matches[1]['id']}/reject", headers=auth(provider))
    assert declined.status_code == 200, declined.text
    assert declined.json()["status"] == "declined"

    accepted = await client.post(f"/matches/{matches[0]['id']}/accept", headers=auth(provider))
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["offer"]["id"] == first["id"]

    twice = await client.post(f"/matches/{matches[0]['id']}/accept", headers=auth(provider))
    assert twice.status_code == 400
    assert twice.json()["error"]["code"] == "invalid_state_transition"
    late = await client.post(f"/matches/{matches[1]['id']}/accept", headers=auth(provider))
    assert late.status_code == 400
    assert "declined" in late.json()["error"]["message"]
    assert (await client.post(f"/matches/{matches[1]['id']}/reject",
                              headers=auth(provider))).status_code == 400

    # Provider decisions survive rescoring, and the declined offer never comes back.
    after = await matches_of(client, customer, demand["id"])
    assert {m["status"]: m["offer"]["id"] for m in after["items"]} == {
        "accepted": first["id"], "declined": second["id"]}
    assert (await demand_of(client, customer, demand["id"]))["match_count"] == 1

    peer = await make_provider(client, "mt.peer@example.test")
    outside = await listed_offer(client, peer, name="Room Three", lat=35.715, lon=51.415,
                                unit_amount_cents=2100, title="Bright workshop room three",
                                description="Another training room with a projector, whiteboard.")
    frozen = await matches_of(client, customer, demand["id"])
    assert outside["id"] not in [m["offer"]["id"] for m in frozen["items"]]
    cannot = await client.post(f"/matches/{after['items'][0]['id']}/accept", headers=auth(peer))
    assert cannot.status_code == 403


async def test_closing_a_demand_freezes_proposals_and_edits(client):
    provider = await make_provider(client, "mt.close@example.test")
    offer = await listed_offer(client, provider, name="Closing Room", lat=35.71, lon=51.41,
                               unit_amount_cents=2000, title="Bright workshop room",
                               description="A training room with a projector and a whiteboard.")
    customer = await register(client, "mt.closecust@example.test")
    demand = await post_demand(client, customer,
                               category_id=await category_id_of(client, customer))
    match = (await matches_of(client, customer, demand["id"]))["items"][0]

    edited = await client.patch(f"/demands/{demand['id']}", headers=auth(customer),
                                json={"quantity": 2, "budget_max_cents": 4000})
    assert edited.status_code == 200, edited.text
    assert edited.json()["quantity"] == 2 and edited.json()["budget_max_cents"] == 4000

    earlier = await client.patch(f"/demands/{demand['id']}", headers=auth(customer),
                                 json={"desired_end": f"{DAY}T09:00:00Z"})
    assert earlier.status_code == 400
    assert earlier.json()["error"]["code"] == "validation_error"

    closed = await client.post(f"/demands/{demand['id']}/close", headers=auth(customer))
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "closed"

    stale_edit = await client.patch(f"/demands/{demand['id']}", headers=auth(customer),
                                    json={"quantity": 1})
    assert stale_edit.status_code == 400
    assert stale_edit.json()["error"]["code"] == "invalid_state_transition"

    cancelled = await client.post(f"/demands/{demand['id']}/cancel", headers=auth(customer))
    assert cancelled.status_code == 409
    assert cancelled.json()["error"]["code"] == "conflict"

    # A closed demand stops proposing new offers and stops accepting the old ones.
    await listed_offer(client, provider, name="Late Room", lat=35.712, lon=51.412,
                       unit_amount_cents=1500, title="Bright workshop room late",
                       description="A late training room with a projector and whiteboard.")
    still = await matches_of(client, customer, demand["id"])
    assert [m["id"] for m in still["items"]] == [match["id"]]
    refused = await client.post(f"/matches/{match['id']}/accept", headers=auth(provider))
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "conflict"
    assert offer["status"] == "published"


async def test_pausing_an_offer_expires_its_suggestion_and_republishing_revives_it(client):
    provider = await make_provider(client, "mt.expire@example.test")
    offer = await listed_offer(client, provider, name="Pausing Room", lat=35.71, lon=51.41,
                               unit_amount_cents=2000, title="Bright workshop room",
                               description="A training room with a projector and a whiteboard.")
    customer = await register(client, "mt.expirecust@example.test")
    demand = await post_demand(client, customer,
                               category_id=await category_id_of(client, customer))
    match = (await matches_of(client, customer, demand["id"]))["items"][0]

    paused = await client.post(f"/offers/{offer['id']}/pause", headers=auth(provider))
    assert paused.status_code == 200, paused.text

    gone = await matches_of(client, customer, demand["id"])
    assert gone["items"] == [] and gone["total"] == 0
    history = await all_matches(client, customer, demand_id=demand["id"], status="expired")
    assert [m["id"] for m in history["items"]] == [match["id"]]

    back = await client.post(f"/offers/{offer['id']}/publish", headers=auth(provider))
    assert back.status_code == 200, back.text
    revived = await matches_of(client, customer, demand["id"])
    assert [m["id"] for m in revived["items"]] == [match["id"]]  # reused row, not a duplicate
    assert revived["items"][0]["status"] == "suggested"
    assert (await all_matches(client, customer, demand_id=demand["id"],
                              status="expired"))["items"] == []


async def test_demand_and_match_validation_and_visibility(client):
    provider = await make_provider(client, "mt.bad@example.test")
    await listed_offer(client, provider, name="Bad Room", lat=35.71, lon=51.41,
                       unit_amount_cents=2000, title="Bright workshop room",
                       description="A training room with a projector and a whiteboard.")
    customer = await register(client, "mt.badcust@example.test")
    demand = await post_demand(client, customer,
                               category_id=await category_id_of(client, customer))

    half = await client.post("/demands", headers=auth(customer),
                             json={"description": DEMAND_TEXT, "desired_start": START})
    assert half.status_code == 400
    assert half.json()["error"]["code"] == "validation_error"

    flipped = await client.post("/demands", headers=auth(customer),
                                json={"description": DEMAND_TEXT, "desired_start": END,
                                      "desired_end": START})
    assert flipped.status_code == 400

    inversion = await client.post("/demands", headers=auth(customer),
                                  json={"description": DEMAND_TEXT, "budget_min_cents": 9000,
                                        "budget_max_cents": 1000})
    assert inversion.status_code == 422
    short = await client.post("/demands", headers=auth(customer), json={"description": "tiny"})
    assert short.status_code == 422
    zero = await client.post("/demands", headers=auth(customer),
                             json={"description": DEMAND_TEXT, "quantity": 0})
    assert zero.status_code == 422
    offworld = await client.post("/demands", headers=auth(customer),
                                 json={"description": DEMAND_TEXT, "lat": 200.0})
    assert offworld.status_code == 422

    bad_quantity = await client.patch(f"/demands/{demand['id']}", headers=auth(customer),
                                      json={"quantity": 0})
    assert bad_quantity.status_code == 422

    assert (await client.get("/demands")).status_code == 401
    assert (await client.get("/matches")).status_code == 401
    for path in (f"/demands/{GHOST}", f"/matches/{GHOST}", f"/demands/{GHOST}/matches"):
        assert (await client.get(path, headers=auth(customer))).status_code == 404
    ghost_close = await client.post(f"/demands/{GHOST}/close", headers=auth(customer))
    assert ghost_close.status_code == 404

    stranger = await register(client, "mt.badnosey@example.test")
    nosy = await client.get(f"/demands/{demand['id']}", headers=auth(stranger))
    assert nosy.status_code == 403
    assert nosy.json()["error"]["code"] == "ownership_required"
    assert (await client.patch(f"/demands/{demand['id']}", headers=auth(stranger),
                               json={"quantity": 3})).status_code == 403
    assert (await client.post(f"/demands/{demand['id']}/close",
                              headers=auth(stranger))).status_code == 403

    bad_status = await client.get("/demands", headers=auth(customer), params={"status": "nope"})
    assert bad_status.status_code == 400
    assert "open" in bad_status.json()["error"]["details"]["allowed"]
    bad_match = await client.get("/matches", headers=auth(customer), params={"status": "nope"})
    assert bad_match.status_code == 400

    # Pagination holds on both collections (§1 list envelope).
    page = await matches_of(client, customer, demand["id"], limit=1)
    assert len(page["items"]) == 1 and page["total"] == 1 and page["limit"] == 1
