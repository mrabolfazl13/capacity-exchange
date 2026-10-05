"""§5.3 offers: draft→publish lifecycle, search read-model, tenant isolation, windows."""
from __future__ import annotations

from datetime import date, timedelta

from tests.test_auth_api import auth, register
from tests.test_capacity_api import DAY, add_rule, make_provider, make_resource

WINDOW_FROM = f"{DAY}T10:00:00Z"
WINDOW_TO = f"{DAY}T12:00:00Z"


def offer_body(definition_id: str, **changes) -> dict:
    body = {
        "definition_id": definition_id,
        "title": "Bright meeting room for workshops",
        "description": "A corner room with a projector, whiteboard and eight chairs.",
        "pricing_mode": "per_unit_time", "unit_amount_cents": 4500, "currency": "USD",
        "min_lead_time_minutes": 60, "min_duration_minutes": 60, "max_duration_minutes": 240,
        "min_quantity": 1, "max_quantity": 2, "booking_mode": "instant", "hold_minutes": 15,
    }
    return {**body, **changes}


async def make_definition(client, tokens: dict, resource_id: str, *,
                          min_quantity: int = 1, max_quantity: int = 4) -> dict:
    resp = await client.post(f"/capacities/{resource_id}/definitions", headers=auth(tokens),
                             json={"name": "Hour slot", "unit_label": "hour",
                                   "min_quantity": min_quantity, "max_quantity": max_quantity})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def make_offer(client, tokens: dict, definition_id: str, **changes) -> dict:
    resp = await client.post("/offers", headers=auth(tokens),
                             json=offer_body(definition_id, **changes))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def publish(client, tokens: dict, offer_id: str) -> dict:
    resp = await client.post(f"/offers/{offer_id}/publish", headers=auth(tokens))
    assert resp.status_code == 200, resp.text
    return resp.json()


async def listed(client, tokens: dict, **params) -> dict:
    resp = await client.get("/offers", headers=auth(tokens), params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_offer_is_created_as_draft_and_narrows_the_definition(client):
    tokens = await make_provider(client, "off.draft@example.test")
    resource = await make_resource(client, tokens, name="Draft Room")
    definition = await make_definition(client, tokens, resource["id"], max_quantity=3)

    beyond = await client.post("/offers", headers=auth(tokens),
                              json=offer_body(definition["id"], max_quantity=9))
    assert beyond.status_code == 400
    assert beyond.json()["error"]["code"] == "unprocessable"
    assert "max_quantity" in beyond.json()["error"]["message"]

    offer = await make_offer(client, tokens, definition["id"], max_quantity=2)
    assert offer["status"] == "draft"
    assert offer["definition_id"] == definition["id"]
    assert offer["org_id"] == tokens["user"]["active_org_id"]
    assert offer["resource_id"] == resource["id"]
    assert offer["published_at"] is None
    assert offer["created_at"].endswith("Z")
    # §5.3 server default applies when the client sends no policy.
    assert offer["cancellation_policy"][0] == {"hours_before": 24, "refund_pct": 100}

    assert (await listed(client, tokens))["items"] == []
    mine = await listed(client, tokens, mine="true")
    assert [o["id"] for o in mine["items"]] == [offer["id"]]


async def test_publishing_needs_availability_and_then_the_offer_is_public(client):
    tokens = await make_provider(client, "off.pub@example.test")
    resource = await make_resource(client, tokens, name="Publish Room")
    definition = await make_definition(client, tokens, resource["id"], max_quantity=4)
    offer = await make_offer(client, tokens, definition["id"])

    early = await client.post(f"/offers/{offer['id']}/publish", headers=auth(tokens))
    assert early.status_code == 400
    assert early.json()["error"]["code"] == "unprocessable"

    await add_rule(client, tokens, resource["id"], definition["id"], quantity=4)
    pub = await client.post(f"/offers/{offer['id']}/publish", headers=auth(tokens))
    assert pub.status_code == 200, pub.text
    body = pub.json()
    assert body["status"] == "published"
    assert body["published_at"].endswith("Z")

    buyer = await register(client, "off.buyer@example.test")
    found = await listed(client, buyer)
    assert [o["id"] for o in found["items"]] == [offer["id"]]
    assert found["total"] == 1 and found["limit"] == 20 and found["offset"] == 0


async def test_search_carries_the_read_model_and_honours_filters(client):
    owner = await make_provider(client, "off.loft@example.test", name="Sun Loft")
    r1 = await make_resource(client, owner, name="Sunrise Studio")
    d1 = await make_definition(client, owner, r1["id"], max_quantity=6)
    await add_rule(client, owner, r1["id"], d1["id"], quantity=6)
    loft = await make_offer(client, owner, d1["id"], max_quantity=6,
                            title="Sunrise coworking loft with roof access",
                            unit_amount_cents=9000)
    await publish(client, owner, loft["id"])

    peer = await make_provider(client, "off.bay@example.test", name="Sea Depot")
    r2 = await make_resource(client, peer, name="Harbor Bay", category_key="warehouse",
                             city="Karaj")
    d2 = await make_definition(client, peer, r2["id"], max_quantity=20)
    await add_rule(client, peer, r2["id"], d2["id"], quantity=20)
    bay = await make_offer(client, peer, d2["id"], max_quantity=20,
                           title="Pallet storage bay", pricing_mode="per_quantity",
                           unit_amount_cents=2000, booking_mode="request_confirm")
    await publish(client, peer, bay["id"])

    buyer = await register(client, "off.searcher@example.test")
    items = {o["id"]: o for o in (await listed(client, buyer))["items"]}
    assert set(items) == {loft["id"], bay["id"]}
    first = items[loft["id"]]
    assert first["org_name"] == "Sun Loft Org"
    assert first["resource_name"] == "Sunrise Studio"
    assert first["category_label"] == "Meeting room"
    assert first["definition_name"] == "Hour slot" and first["unit_label"] == "hour"
    assert first["max_quantity_definition"] == 6
    assert first["city"] == "Tehran" and first["lat"] is None
    assert first["rating_avg"] is None and first["rating_count"] == 0
    assert first["free_quantity"] is None  # only set when a window is asked for

    assert [o["id"] for o in (await listed(client, buyer, q="sunrise"))["items"]] == [loft["id"]]
    assert [o["id"] for o in (await listed(client, buyer, q="pallet storage"))["items"]] == [bay["id"]]
    assert [o["id"] for o in (await listed(client, buyer, city="Karaj"))["items"]] == [bay["id"]]
    assert [o["id"] for o in (await listed(client, buyer, city="tehran"))["items"]] == [loft["id"]]
    assert (await listed(client, buyer, city="Rasht"))["items"] == []
    assert (await listed(client, buyer, country="DE"))["total"] == 0

    by_category = await listed(client, buyer, category_id=first["category_id"])
    assert [o["id"] for o in by_category["items"]] == [loft["id"]]
    assert [o["id"] for o in (await listed(client, buyer, min_quantity=7))["items"]] == [bay["id"]]
    assert (await listed(client, buyer, min_quantity=25))["items"] == []
    assert [o["id"] for o in (await listed(client, buyer, max_unit_cents=5000))["items"]] == [bay["id"]]
    assert [o["id"] for o in (await listed(client, buyer,
                                            booking_mode="request_confirm"))["items"]] == [bay["id"]]

    asc = [o["unit_amount_cents"] for o in (await listed(client, buyer, sort="price_asc"))["items"]]
    desc = [o["unit_amount_cents"] for o in (await listed(client, buyer, sort="price_desc"))["items"]]
    assert asc == sorted(asc) and desc == sorted(desc, reverse=True)

    page = await listed(client, buyer, limit=1)
    assert len(page["items"]) == 1 and page["total"] == 2 and page["limit"] == 1
    second = await listed(client, buyer, limit=1, offset=1)
    assert second["items"][0]["id"] != page["items"][0]["id"]


async def test_window_search_reports_free_quantity_and_drops_the_infeasible(client):
    owner = await make_provider(client, "off.free@example.test")
    resource = await make_resource(client, owner, name="Free Room")
    definition = await make_definition(client, owner, resource["id"], max_quantity=5)
    await add_rule(client, owner, resource["id"], definition["id"], quantity=5)
    capped = await make_offer(client, owner, definition["id"], max_quantity=2)
    await publish(client, owner, capped["id"])

    whole_day = (date.fromisoformat(DAY) + timedelta(days=7)).isoformat()
    buyer = await register(client, "off.windowbuyer@example.test")
    window = {"from": WINDOW_FROM, "to": WINDOW_TO}
    body = await listed(client, buyer, **window)
    assert body["items"][0]["free_quantity"] == 2  # min(offer 2, definition 5)
    assert body["total"] == 1

    needs_three = await listed(client, buyer, **window, min_quantity=3)
    assert needs_three["items"] == []

    closed = await client.post(f"/capacities/{resource['id']}/availability/overrides",
                               headers=auth(owner), json={
                                   "definition_id": definition["id"], "override_date": DAY,
                                   "kind": "closed", "reason": "Power outage"})
    assert closed.status_code == 201, closed.text
    assert (await listed(client, buyer, **window))["items"] == []

    windows = await client.get(f"/offers/{capped['id']}/availability", headers=auth(buyer),
                               params={"from": f"{whole_day}T00:00:00Z",
                                       "to": f"{whole_day}T23:00:00Z"})
    assert windows.status_code == 200, windows.text
    items = windows.json()["items"]
    assert [(w["window_start"][11:16], w["window_end"][11:16], w["free_quantity"])
            for w in items] == [("09:00", "18:00", 2)]


async def test_unpublished_offers_are_hidden_from_other_tenants(client):
    owner = await make_provider(client, "off.hide@example.test")
    resource = await make_resource(client, owner, name="Hidden Room")
    definition = await make_definition(client, owner, resource["id"])
    offer = await make_offer(client, owner, definition["id"])

    intruder = await make_provider(client, "off.peek@example.test")
    got = await client.get(f"/offers/{offer['id']}", headers=auth(intruder))
    assert got.status_code == 403
    assert got.json()["error"]["code"] == "ownership_required"

    patched = await client.patch(f"/offers/{offer['id']}", headers=auth(intruder),
                                json={"title": "Not mine anymore"})
    assert patched.status_code == 403
    for verb in ("publish", "pause", "close"):
        denied = await client.post(f"/offers/{offer['id']}/{verb}", headers=auth(intruder))
        assert denied.status_code == 403

    mine = await listed(client, intruder, mine="true")
    assert mine["items"] == []

    missing = await client.get("/offers/11111111-1111-1111-1111-111111111111",
                               headers=auth(owner))
    assert missing.status_code == 404
    unknown_offer = await client.post("/offers", headers=auth(owner),
                                     json=offer_body("11111111-1111-1111-1111-111111111111"))
    assert unknown_offer.status_code == 404


async def test_owner_can_edit_and_the_lifecycle_is_one_way_to_closed(client):
    tokens = await make_provider(client, "off.life@example.test")
    resource = await make_resource(client, tokens, name="Lifecycle Room")
    definition = await make_definition(client, tokens, resource["id"], max_quantity=4)
    offer = await make_offer(client, tokens, definition["id"], max_quantity=2)

    edited = await client.patch(f"/offers/{offer['id']}", headers=auth(tokens),
                                json={"title": "Renamed meeting room", "unit_amount_cents": 5000,
                                      "min_duration_minutes": 30})
    assert edited.status_code == 200, edited.text
    assert edited.json()["title"] == "Renamed meeting room"
    assert edited.json()["unit_amount_cents"] == 5000

    widened = await client.patch(f"/offers/{offer['id']}", headers=auth(tokens),
                                json={"max_quantity": 12})
    assert widened.status_code == 400
    assert widened.json()["error"]["code"] == "unprocessable"

    await add_rule(client, tokens, resource["id"], definition["id"], quantity=4)
    await publish(client, tokens, offer["id"])
    paused = await client.post(f"/offers/{offer['id']}/pause", headers=auth(tokens))
    assert paused.status_code == 200 and paused.json()["status"] == "paused"
    assert (await listed(client, tokens))["items"] == []  # paused leaves the public index

    back = await client.post(f"/offers/{offer['id']}/publish", headers=auth(tokens))
    assert back.status_code == 200 and back.json()["status"] == "published"

    closed = await client.post(f"/offers/{offer['id']}/close", headers=auth(tokens))
    assert closed.status_code == 200 and closed.json()["status"] == "closed"

    reopen = await client.post(f"/offers/{offer['id']}/publish", headers=auth(tokens))
    assert reopen.status_code == 409
    assert reopen.json()["error"]["code"] == "conflict"

    assert (await listed(client, tokens, mine="true"))["items"] == []
    admin_view = await listed(client, tokens, mine="true", status="closed")
    assert [o["id"] for o in admin_view["items"]] == [offer["id"]]


async def test_offer_creation_is_idempotent_per_key(client):
    tokens = await make_provider(client, "off.idem@example.test")
    resource = await make_resource(client, tokens, name="Idempotent Listing Room")
    definition = await make_definition(client, tokens, resource["id"])
    body = offer_body(definition["id"])
    headers = {**auth(tokens), "Idempotency-Key": "offer-1"}

    first = await client.post("/offers", json=body, headers=headers)
    second = await client.post("/offers", json=body, headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]

    changed = await client.post("/offers", json={**body, "title": "Different title here"},
                                headers=headers)
    assert changed.status_code == 409


async def test_offer_search_input_validation(client):
    tokens = await make_provider(client, "off.bad@example.test")
    buyer = await register(client, "off.badbuyer@example.test")

    short = await client.post("/offers", headers=auth(tokens),
                              json={**offer_body("11111111-1111-1111-1111-111111111111"),
                                    "title": "x"})
    assert short.status_code == 422
    assert short.json()["error"]["code"] == "validation_error"

    half_window = await client.get("/offers", headers=auth(buyer), params={"from": WINDOW_FROM})
    assert half_window.status_code == 400

    bad_dates = await client.get("/offers", headers=auth(buyer),
                                 params={"from": "next tuesday", "to": WINDOW_TO})
    assert bad_dates.status_code == 422
    assert bad_dates.json()["error"]["code"] == "range_mismatch"

    flipped = await client.get("/offers", headers=auth(buyer),
                               params={"from": WINDOW_TO, "to": WINDOW_FROM})
    assert flipped.status_code == 422

    over_limit = await client.get("/offers", headers=auth(buyer), params={"limit": 500})
    assert over_limit.status_code == 422

    bad_bbox = await client.get("/offers", headers=auth(buyer), params={"bbox": "1,2"})
    assert bad_bbox.status_code == 400
    inverted_bbox = await client.get("/offers", headers=auth(buyer),
                                     params={"bbox": "50,50,10,10"})
    assert inverted_bbox.status_code == 422

    closed_as_stranger = await client.get("/offers", headers=auth(buyer), params={"status": "closed"})
    assert closed_as_stranger.status_code == 400

    anonymous = await client.get("/offers")
    assert anonymous.status_code == 401
