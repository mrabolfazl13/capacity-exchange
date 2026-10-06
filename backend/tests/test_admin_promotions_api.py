"""§5.6 promotions: a coupon the console writes is the coupon the order redeems.

The desk and the money path are tested together on purpose — a promotion row no order can read
would be decoration, and a discount the console cannot issue would be unreachable. Scope
filters, limits, windows and the redemption count are all asserted through shipped routes.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from tests.test_auth_api import auth, register
from tests.test_booking_api import live_offer
from tests.test_capacity_api import category_id_of, make_provider
from tests.test_commerce_api import book_offer, intent
from tests.test_reviews_disputes_api import pay, staff

NOW = datetime.now(timezone.utc)
TEN_PERCENT = {"type": "pct", "bp": 1000}


def stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


STARTS = stamp(NOW - timedelta(hours=1))
ENDS = stamp(NOW + timedelta(days=7))
OPEN_WINDOW = {"starts_at": STARTS, "ends_at": ENDS}


async def create(client, admin, **changes) -> tuple[int, dict]:
    body = {"name": "Launch week", "kind": "coupon", "code": "launch10",
            "discount_config": TEN_PERCENT, **OPEN_WINDOW, "status": "active", **changes}
    resp = await client.post("/admin/promotions", headers=auth(admin), json=body)
    return resp.status_code, resp.json()


async def redeem(client, buyer, booking_id: str, code: str | None = "LAUNCH10") -> tuple[int, dict]:
    body: dict = {"booking_id": booking_id}
    if code:
        body["coupon_code"] = code
    resp = await client.post("/orders", headers=auth(buyer), json=body)
    return resp.status_code, resp.json()


async def listed(client, admin, path: str, **params) -> dict:
    resp = await client.get(path, headers=auth(admin), params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def checkout(client, provider, buyer) -> dict:
    """The smallest real checkout: one live offer and one booked visit that carries an order."""
    offer = await live_offer(client, provider)
    return {"offer": offer, "booking": await book_offer(client, buyer, offer["id"])}


async def test_the_console_issues_a_coupon_the_order_path_can_see(client, db):
    admin = await staff(client, db, "pm.admin@example.test", "platform_admin")
    provider = await make_provider(client, "pm.prov@example.test")

    code, promo = await create(client, admin)
    assert code == 201, promo
    assert promo["code"] == "LAUNCH10", "the console stores the code as it is typed in"
    assert promo["status"] == "active" and promo["used_count"] == 0
    assert promo["applies_to"] == {} and promo["discount_config"] == TEN_PERCENT
    assert promo["created_by"] == admin["user"]["id"]
    assert promo["created_by_name"] == admin["user"]["full_name"]
    assert promo["starts_at"] == STARTS and promo["created_at"].endswith("Z")

    mine = await listed(client, admin, f"/admin/promotions/{promo['id']}")
    assert mine["code"] == "LAUNCH10" and mine["min_order_cents"] == 0

    queue = await listed(client, admin, "/admin/promotions")
    assert queue["total"] == 1
    assert queue["items"][0]["created_by_name"] == admin["user"]["full_name"]
    assert (await listed(client, admin, "/admin/promotions", q="lunc"))["total"] == 0
    assert (await listed(client, admin, "/admin/promotions", q="LAUNCH"))["total"] == 1
    assert (await listed(client, admin, "/admin/promotions", status="active"))["total"] == 1
    assert (await listed(client, admin, "/admin/promotions", status="draft"))["total"] == 0
    assert (await listed(client, admin, "/admin/promotions", kind="coupon"))["total"] == 1
    bad_kind = await client.get("/admin/promotions", headers=auth(admin), params={"kind": "voucher"})
    assert bad_kind.status_code == 400
    assert bad_kind.json()["error"]["message"] == "Unknown promotion kind"

    again, clash = await create(client, admin, name="Second launch")
    assert again == 409 and clash["error"]["code"] == "conflict"

    for changes, message in (
        ({"code": None}, "a coupon needs a code for the buyer to enter"),
        ({"kind": "campaign", "code": "SPRING"}, "a campaign is not redeemable"),
        ({"ends_at": STARTS}, "ends_at must be after starts_at"),
        ({"discount_config": {"type": "pct", "bp": 20000}}, "bp must be within 0..10000"),
        ({"discount_config": {"type": "fixed", "cents": -1}}, "cents must be >= 0"),
        ({"code": "ab"}, "at least 3 characters"),
    ):
        bad, body = await create(client, admin, **{"name": "Rejected", **changes})
        assert bad == 422, (changes, body)
        assert message in str(body["error"]["details"]), body

    forbidden = await client.post("/admin/promotions", headers=auth(provider), json={
        "name": "Provider own", "code": "OWN10", "discount_config": TEN_PERCENT, **OPEN_WINDOW})
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "role_required"


async def test_a_coupon_comes_off_the_order_before_the_buyer_pays(client, db):
    admin = await staff(client, db, "pm.money.admin@example.test", "platform_admin")
    provider = await make_provider(client, "pm.money.prov@example.test")
    buyer = await register(client, "pm.money.buyer@example.test")
    code, promo = await create(client, admin, discount_config={"type": "pct", "bp": 2000})
    assert code == 201

    visit = await checkout(client, provider, buyer)
    booking_id = visit["booking"]["id"]
    unknown, ghost = await redeem(client, buyer, booking_id, code="NOPE10")
    assert unknown == 409 and ghost["error"]["message"] == "Coupon code is not valid"

    status, order = await redeem(client, buyer, booking_id)
    assert status == 201, order
    assert order["subtotal_cents"] == 10000
    assert order["discount_cents"] == 2000, "20% off the visit the buyer is checking out"
    assert order["total_cents"] == 8000 and order["payment_status"] == "unpaid"
    assert order["promotion_id"] == promo["id"]

    counted = await listed(client, admin, f"/admin/promotions/{promo['id']}")
    assert counted["used_count"] == 1
    redemptions = await listed(client, admin, f"/admin/promotions/{promo['id']}/redemptions")
    assert redemptions["total"] == 1
    row = redemptions["items"][0]
    assert row["user_email"] == "pm.money.buyer@example.test"
    assert row["user_name"] == buyer["user"]["full_name"]
    assert row["discount_cents"] == 2000 and row["order_total_cents"] == 8000
    assert row["order_number"] == order["number"] and row["redeemed_at"].endswith("Z")

    settled = await pay(client, buyer, visit["booking"])
    assert settled["amount_cents"] == 8000, "the buyer is charged what the coupon left"
    paid = await client.get(f"/orders/{order['id']}", headers=auth(buyer))
    assert paid.status_code == 200
    assert paid.json()["payment_status"] == "paid" and paid.json()["total_cents"] == 8000
    assert paid.json()["commission_cents"] == 800, "the fee follows the money actually taken"

    late, refused = await redeem(client, buyer, booking_id)
    assert late == 409
    assert refused["error"]["message"] == "Order cannot be repriced once its money has moved"

    same, untouched = await redeem(client, buyer, booking_id, code=None)
    assert same == 201 and untouched["id"] == order["id"]
    assert untouched["discount_cents"] == 2000, "a plain re-open leaves the money alone"


async def test_the_scope_filter_decides_which_money_a_code_can_touch(client, db):
    admin = await staff(client, db, "pm.scope.admin@example.test", "platform_admin")
    provider = await make_provider(client, "pm.scope.prov@example.test")
    other = await make_provider(client, "pm.scope.other@example.test", name="Kitchen Owner")
    buyer = await register(client, "pm.scope.buyer@example.test")
    room_id = await category_id_of(client, provider, "meeting_room")
    kitchen_id = await category_id_of(client, provider, "commercial_kitchen")

    room = await live_offer(client, provider)
    kitchen = await live_offer(client, other, name="Baking Kitchen",
                               category_key="commercial_kitchen")
    in_room = await book_offer(client, buyer, room["id"])
    in_kitchen = await book_offer(client, buyer, kitchen["id"])

    for code, scope in (
        ("ONLYOTHER", {"offer_id": kitchen["id"]}),
        ("OTHERORG", {"org_id": other["user"]["active_org_id"]}),
        ("ONLYROOM", {"category_id": room_id}),
    ):
        made, promo = await create(client, admin, code=code, applies_to=scope)
        assert made == 201, promo
        booking = in_kitchen if code == "ONLYROOM" else in_room
        status, refused = await redeem(client, buyer, booking["id"], code=code)
        assert status == 409, refused
        assert refused["error"]["message"] == "Coupon code does not apply to this service"
        assert refused["error"]["details"]["applies_to"] == scope

    for code, scope, message in (
        ("BOGUSKEY", {"shop_id": str(uuid.uuid4())}, "Unknown coupon scope filter"),
        ("BOGUSID", {"org_id": "not-an-id"}, "org_id must be an id"),
    ):
        bad, body = await create(client, admin, code=code, applies_to=scope)
        assert bad == 400 and body["error"]["code"] == "validation_error", body
        assert body["error"]["message"] == message

    made, scoped = await create(client, admin, code="KITCHEN10",
                                applies_to={"category_id": kitchen_id})
    assert made == 201 and scoped["applies_to"] == {"category_id": kitchen_id}
    status, order = await redeem(client, buyer, in_kitchen["id"], code="KITCHEN10")
    assert status == 201 and order["discount_cents"] == 1000
    status, refused = await redeem(client, buyer, in_room["id"], code="KITCHEN10")
    assert status == 409 and refused["error"]["message"] == "Coupon code does not apply to this service"

    rejected = await listed(client, admin, "/admin/promotions", q="ONLYOTHER")
    assert rejected["items"][0]["used_count"] == 0, "a refused code costs the operator nothing"


async def test_a_code_only_redeems_inside_its_own_terms(client, db):
    admin = await staff(client, db, "pm.terms.admin@example.test", "platform_admin")
    provider = await make_provider(client, "pm.terms.prov@example.test")
    buyer = await register(client, "pm.terms.buyer@example.test")
    visit = await checkout(client, provider, buyer)
    booking_id = visit["booking"]["id"]

    for state in ("draft", "disabled"):
        made, _ = await create(client, admin, code=f"{state.upper()}10", status=state)
        assert made == 201
        code, refused = await redeem(client, buyer, booking_id, code=f"{state.upper()}10")
        assert code == 409 and refused["error"]["message"] == "Coupon code is not valid"

    for code, window in (
        ("CLOSED10", {"starts_at": stamp(NOW - timedelta(days=9)),
                      "ends_at": stamp(NOW - timedelta(days=2))}),
        ("LATER10", {"starts_at": stamp(NOW + timedelta(days=1)),
                     "ends_at": stamp(NOW + timedelta(days=8))}),
    ):
        made, _ = await create(client, admin, code=code, **window)
        assert made == 201
        status, refused = await redeem(client, buyer, booking_id, code=code)
        assert status == 409
        assert refused["error"]["message"] == "Coupon code is outside its validity window"

    _, steep = await create(client, admin, code="BIGSPEND", min_order_cents=20000)
    status, refused = await redeem(client, buyer, booking_id, code="BIGSPEND")
    assert status == 409 and refused["error"]["details"] == {"min_order_cents": 20000}

    _, euros = await create(client, admin, code="EUROFIVE",
                            discount_config={"type": "fixed", "cents": 500, "currency": "EUR"})
    assert euros["discount_config"] == {"type": "fixed", "cents": 500, "currency": "EUR"}
    status, refused = await redeem(client, buyer, booking_id, code="EUROFIVE")
    assert status == 409
    assert refused["error"]["details"] == {"coupon_currency": "EUR", "currency": "USD"}

    _, covering = await create(client, admin, code="FREEALL",
                               discount_config={"type": "fixed", "cents": 99000})
    assert covering["status"] == "active"
    status, order = await redeem(client, buyer, booking_id, code="FREEALL")
    assert status == 201 and order["discount_cents"] == 10000
    assert order["total_cents"] == 0 and order["payment_status"] == "not_required"


async def test_the_redemption_limits_hold_across_buyers(client, db):
    admin = await staff(client, db, "pm.limit.admin@example.test", "platform_admin")
    provider = await make_provider(client, "pm.limit.prov@example.test")
    first = await register(client, "pm.limit.one@example.test")
    second = await register(client, "pm.limit.two@example.test")
    offer = await live_offer(client, provider, quantity=4)

    made, once_each = await create(client, admin, code="ONCE10", per_user_limit=1)
    assert made == 201 and once_each["per_user_limit"] == 1
    own = await book_offer(client, first, offer["id"])
    status, order = await redeem(client, first, own["id"], code="ONCE10")
    assert status == 201 and order["discount_cents"] == 1000

    again, refused = await redeem(client, first, own["id"], code="ONCE10")
    assert again == 409 and refused["error"]["message"] == "Order already carries a discount"

    other_visit = await book_offer(client, first, offer["id"])
    again, refused = await redeem(client, first, other_visit["id"], code="ONCE10")
    assert again == 409 and refused["error"]["message"] == "Coupon already used by this customer"

    made, once_only = await create(client, admin, code="ONEUSE", usage_limit=1)
    assert made == 201
    visitor = await book_offer(client, second, offer["id"])
    status, _ = await redeem(client, second, visitor["id"], code="ONEUSE")
    assert status == 201
    crowded, refused = await redeem(client, first, other_visit["id"], code="ONEUSE")
    assert crowded == 409 and refused["error"]["message"] == "Coupon usage limit reached"
    counted = await listed(client, admin, "/admin/promotions", q="ONEUSE")
    assert counted["items"][0]["used_count"] == 1

    made, in_flight = await create(client, admin, code="INFLIGHT")
    assert made == 201 and in_flight["used_count"] == 0
    held = await book_offer(client, second, offer["id"])
    plain, order = await redeem(client, second, held["id"], code=None)
    assert plain == 201
    await intent(client, second, order["id"])
    status, refused = await redeem(client, second, held["id"], code="INFLIGHT")
    assert status == 409
    assert refused["error"]["message"] == "A payment is already in flight for this order"

    stranger, refused = await redeem(client, second, own["id"], code="INFLIGHT")
    assert stranger == 403 and refused["error"]["code"] == "ownership_required"


async def test_the_console_moves_the_terms_never_the_count(client, db):
    admin = await staff(client, db, "pm.patch.admin@example.test", "platform_admin")
    provider = await make_provider(client, "pm.patch.prov@example.test")
    buyer = await register(client, "pm.patch.buyer@example.test")
    code, promo = await create(client, admin, usage_limit=4)
    assert code == 201
    offer = await live_offer(client, provider, quantity=2)
    first = await book_offer(client, buyer, offer["id"])
    second = await book_offer(client, buyer, offer["id"])
    assert (await redeem(client, buyer, first["id"]))[0] == 201
    assert (await redeem(client, buyer, second["id"]))[0] == 201

    async def patch(**changes) -> tuple[int, dict]:
        resp = await client.patch(f"/admin/promotions/{promo['id']}", headers=auth(admin),
                                  json=changes)
        return resp.status_code, resp.json()

    later = stamp(NOW + timedelta(days=30))
    code, renamed = await patch(name="Launch week, longer", ends_at=later)
    assert code == 200 and renamed["name"] == "Launch week, longer"
    assert renamed["used_count"] == 2, "redemptions belong to the orders, not to this desk"
    assert renamed["ends_at"] == later

    for changes, status_code, message in (
        ({}, 422, "send at least one field to change"),
        ({"used_count": 99}, 422, "send at least one field to change"),
        ({"status": "expired"}, 422, "Input should be 'draft'"),
        ({"usage_limit": 0}, 422, "greater than or equal to 1"),
        ({"ends_at": STARTS}, 400, "ends_at must be after starts_at"),
        ({"usage_limit": 1}, 400, "usage_limit is below the coupons already redeemed"),
    ):
        code, body = await patch(**changes)
        assert code == status_code, (changes, body)
        assert message in str(body["error"]), body

    code, closed = await patch(status="active", starts_at=stamp(NOW - timedelta(days=9)),
                               ends_at=stamp(NOW - timedelta(days=2)))
    assert code == 409
    assert closed["error"]["message"] == "This window has already closed, so nothing could redeem it"

    trail = await listed(client, admin, "/admin/audit-logs", entity_id=promo["id"])
    actions = [row["action"] for row in trail["items"]]
    assert "admin.promotion_created" in actions and "admin.promotion_updated" in actions
    rename = next(r for r in trail["items"] if r["after"].get("name") == "Launch week, longer")
    assert rename["before"]["name"] == "Launch week"
    assert rename["actor_user_id"] == admin["user"]["id"]
    assert rename["after"]["ends_at"] == later
