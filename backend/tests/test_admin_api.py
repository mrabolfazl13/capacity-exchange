"""§8 admin surface: the role gate, the operator queues, the audit trail and platform totals.

Every assertion runs through the shipped routes against the database each test starts with,
so the counts are absolute. Fixtures are the same ones the booking, commerce and review tests
already prove out, and the state changes that feed the read models are made through their own
routes rather than by writing rows.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from tests.test_auth_api import auth, register
from tests.test_booking_api import OTHER_DAY, live_offer
from tests.test_capacity_api import make_provider
from tests.test_commerce_api import OTHER_END, OTHER_START, book_offer
from tests.test_reviews_disputes_api import (
    add_review,
    open_dispute,
    pay,
    resolve,
    staff,
)

TODAY = datetime.now(timezone.utc).date()
TITLE = "Bright meeting room for workshops"
DESCRIPTION = "The projector failed for the first hour of the session and no one mentioned it."
COMMENT = "The room was exactly as described and the host was prepared."


async def listed(client, tokens, path, **params) -> dict:
    resp = await client.get(path, headers=auth(tokens), params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def analytics(client, tokens, **params) -> dict:
    return await listed(client, tokens, "/admin/analytics", **params)


async def run_to_completion(client, provider, booking: dict) -> dict:
    """Drive an existing booking through the service, the way the provider app does."""
    for action in ("start", "complete"):
        resp = await client.post(f"/bookings/{booking['id']}/{action}", headers=auth(provider))
        assert resp.status_code == 200, resp.text
        booking = resp.json()
    return booking


async def test_the_admin_surface_is_behind_a_platform_role(client, db):
    provider = await make_provider(client, "ad.prov@example.test")
    buyer = await register(client, "ad.buyer@example.test")
    support = await staff(client, db, "ad.support@example.test", "support")
    admin = await staff(client, db, "ad.admin@example.test", "platform_admin")

    assert (await client.get("/admin/users")).status_code == 401

    paths = ("/admin/users", "/admin/providers", "/admin/audit-logs", "/admin/analytics",
             "/admin/categories")
    for path in paths:
        for tokens, who in ((buyer, "customer"), (provider, "provider"),
                            (support, "support")):
            denied = await client.get(path, headers=auth(tokens))
            assert denied.status_code == 403, f"{path} as {who}"
            assert denied.json()["error"]["code"] == "role_required"
        assert await listed(client, admin, path) is not None, path

    # Support works the queue; it does not read identities, per-org money or the registry.
    assert (await listed(client, support, "/admin/disputes"))["items"] == []
    category = (await listed(client, admin, "/admin/categories"))["items"][0]
    retired = await client.patch(f"/admin/categories/{category['id']}", headers=auth(buyer),
                                 json={"is_active": False})
    assert retired.status_code == 403
    assert retired.json()["error"]["message"] == "Platform role required"


async def test_the_user_queue_searches_by_text_role_and_page(client, db):
    await make_provider(client, "ad.prov@example.test", name="Room Owner")
    buyer = await register(client, "ad.buyer@example.test")
    admin = await staff(client, db, "ad.admin@example.test", "platform_admin")

    queue = await listed(client, admin, "/admin/users")
    assert queue["total"] == 3 and len(queue["items"]) == 3
    by_email = {row["email"]: row for row in queue["items"]}
    assert set(by_email) == {"ad.prov@example.test", "ad.buyer@example.test",
                             "ad.admin@example.test"}

    # §4: `customer` is implicit, so it shows in the read model without being granted.
    assert by_email["ad.buyer@example.test"]["roles"] == ["customer"]
    assert set(by_email["ad.prov@example.test"]["roles"]) == {"customer", "org_admin", "provider"}
    assert by_email["ad.admin@example.test"]["roles"] == ["customer", "platform_admin"]
    assert by_email["ad.prov@example.test"]["org_names"] == ["Room Owner Org"]
    assert by_email["ad.buyer@example.test"]["org_names"] == []
    assert by_email["ad.admin@example.test"]["last_login_at"] is not None
    assert by_email["ad.buyer@example.test"]["last_login_at"] is None
    assert by_email["ad.prov@example.test"]["full_name"] == "Room Owner"
    assert by_email["ad.buyer@example.test"]["full_name"] == "Test User"
    for row in queue["items"]:
        assert row["is_active"] is True and row["created_at"].endswith("Z")

    assert [r["email"] for r in (await listed(client, admin, "/admin/users",
                                              q="ad.buyer"))["items"]] == ["ad.buyer@example.test"]
    assert (await listed(client, admin, "/admin/users", q="Owner"))["total"] == 1, "by full name"
    assert (await listed(client, admin, "/admin/users", q="nobody"))["total"] == 0
    assert (await listed(client, admin, "/admin/users", role="org_admin"))["total"] == 1
    assert (await listed(client, admin, "/admin/users", role="platform_admin"))["total"] == 1
    assert (await listed(client, admin, "/admin/users", active=False))["total"] == 0

    page_one = (await listed(client, admin, "/admin/users", limit=2))["items"]
    page_two = (await listed(client, admin, "/admin/users", limit=2, offset=2))["items"]
    assert len(page_one) == 2 and len(page_two) == 1
    assert not {r["id"] for r in page_one} & {r["id"] for r in page_two}

    implicit = await client.get("/admin/users", headers=auth(admin), params={"role": "customer"})
    assert implicit.status_code == 400
    assert implicit.json()["error"]["message"] == ("Every account is a customer; filter by "
                                                   "a granted role instead")
    assert implicit.json()["error"]["details"]["allowed"] == ["platform_admin", "support",
                                                              "org_admin", "provider"]
    assert (await client.get("/admin/users", headers=auth(admin),
                             params={"limit": 101})).status_code == 422
    assert (await client.get("/admin/users", headers=auth(admin),
                             params={"q": uuid.uuid4().hex * 9})).status_code == 422


async def test_the_provider_queue_carries_the_counts_an_operator_triages_on(client, db):
    provider = await make_provider(client, "ad.prov@example.test", name="North Court")
    admin = await staff(client, db, "ad.admin@example.test", "platform_admin")
    patched = await client.patch(f"/organizations/{provider['user']['active_org_id']}",
                                 headers=auth(provider),
                                 json={"name": "North Court Org", "country": "de",
                                       "currency": "EUR", "timezone": "Europe/Berlin"})
    assert patched.status_code == 200, patched.text

    offer = await live_offer(client, provider, quantity=2)
    buyer = await register(client, "ad.buyer@example.test")
    booking = await book_offer(client, buyer, offer["id"])
    await pay(client, buyer, booking)
    await run_to_completion(client, provider, booking)
    await add_review(client, buyer, booking["id"], rating=4, comment=COMMENT)

    rows = (await listed(client, admin, "/admin/providers"))["items"]
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == provider["user"]["active_org_id"]
    assert row["name"] == "North Court Org" and row["slug"] == "north-court-org"
    assert row["status"] == "active" and row["org_admin_email"] == "ad.prov@example.test"
    assert row["country"] == "DE" and row["currency"] == "EUR"
    assert row["timezone"] == "Europe/Berlin"
    assert row["offer_count"] == 1 and row["booking_count"] == 1
    assert row["gmv_cents"] == 10000
    assert row["rating_avg"] == 4.0 and row["rating_count"] == 1
    assert row["open_disputes"] == 0 and row["created_at"].endswith("Z")

    await open_dispute(client, buyer, booking["id"], description=DESCRIPTION)
    assert (await listed(client, admin, "/admin/providers"))["items"][0]["open_disputes"] == 1

    assert (await listed(client, admin, "/admin/providers", q="north"))["total"] == 1, "by slug"
    assert (await listed(client, admin, "/admin/providers",
                         q="ad.prov@"))["total"] == 1, "by the owners login"
    assert (await listed(client, admin, "/admin/providers", country="de"))["total"] == 1
    assert (await listed(client, admin, "/admin/providers", country="FR"))["total"] == 0
    assert (await listed(client, admin, "/admin/providers", status="active"))["total"] == 1
    assert (await listed(client, admin, "/admin/providers", status="suspended"))["total"] == 0
    bad = await client.get("/admin/providers", headers=auth(admin), params={"status": "paused"})
    assert bad.status_code == 400
    assert bad.json()["error"]["details"]["allowed"] == ["active", "suspended"]


async def test_the_dispute_queue_brings_the_service_money_and_age_with_it(client, db):
    provider = await make_provider(client, "ad.prov@example.test")
    admin = await staff(client, db, "ad.admin@example.test", "platform_admin")
    offer = await live_offer(client, provider, quantity=2)
    buyer = await register(client, "ad.buyer@example.test")

    paid = await book_offer(client, buyer, offer["id"])
    await pay(client, buyer, paid)
    later = await book_offer(client, buyer, offer["id"], window_start=OTHER_START,
                             window_end=OTHER_END)
    first = await open_dispute(client, buyer, paid["id"], description=DESCRIPTION)
    second = await open_dispute(client, buyer, later["id"], kind="no_show",
                                description=DESCRIPTION)

    queue = await listed(client, admin, "/admin/disputes")
    assert queue["total"] == 2 and len(queue["items"]) == 2
    rows = {row["id"]: row for row in queue["items"]}
    assert set(rows) == {first["id"], second["id"]}

    paid_row = rows[first["id"]]
    assert paid_row["status"] == "open" and paid_row["kind"] == "quality"
    assert paid_row["complainant_name"] == "Test User"
    assert paid_row["org_name"] == "Room Owner Org"
    assert paid_row["booking_status"] == "disputed"
    assert paid_row["offer_title"] == TITLE
    assert paid_row["customer_email"] == "ad.buyer@example.test"
    assert paid_row["window_start"].endswith("Z") and paid_row["window_end"].endswith("Z")
    assert paid_row["order_total_cents"] == 10000 and paid_row["order_refunded_cents"] == 0
    assert paid_row["order_payment_status"] == "paid"
    assert paid_row["refund_cents"] == 0
    assert 0 <= paid_row["age_hours"] < 1

    unpaid_row = rows[second["id"]]
    assert unpaid_row["order_payment_status"] == "unpaid", "a visit with no money still triages"
    assert unpaid_row["window_start"].startswith(OTHER_DAY)

    assert (await listed(client, admin, "/admin/disputes", kind="no_show"))["total"] == 1
    assert (await listed(client, admin, "/admin/disputes", status="open"))["total"] == 2
    assert (await listed(client, admin, "/admin/disputes",
                         status="under_review"))["total"] == 0
    assert (await listed(client, admin, "/admin/disputes",
                         org_id=provider["user"]["active_org_id"]))["total"] == 2
    assert (await listed(client, admin, "/admin/disputes",
                         org_id=str(uuid.uuid4())))["total"] == 0

    newest = [row["id"] for row in queue["items"]]
    oldest = [row["id"] for row in
              (await listed(client, admin, "/admin/disputes", oldest_first=True))["items"]]
    assert oldest == list(reversed(newest))

    bad_kind = await client.get("/admin/disputes", headers=auth(admin), params={"kind": "rude"})
    assert bad_kind.status_code == 400
    assert bad_kind.json()["error"]["details"]["allowed"][0] == "quality"


async def test_the_registry_and_its_audit_trail_stay_consistent(client, db):
    admin = await staff(client, db, "ad.admin@example.test", "platform_admin")
    buyer = await register(client, "ad.buyer@example.test")
    categories = await listed(client, admin, "/admin/categories")
    assert categories["total"] == 12
    room = next(c for c in categories["items"] if c["key"] == "meeting_room")

    noop = await client.patch(f"/admin/categories/{room['id']}", headers=auth(admin), json={})
    assert noop.status_code == 400
    assert noop.json()["error"]["message"] == "Nothing to change on this category"

    retired = await client.patch(f"/admin/categories/{room['id']}", headers=auth(admin),
                                 json={"is_active": False})
    assert retired.status_code == 200, retired.text
    assert retired.json()["is_active"] is False and retired.json()["label"] == room["label"]

    # Retiring hides the category from the wizard while admins still see it to restore it.
    public_ids = [c["id"] for c in (await listed(client, buyer, "/catalog/categories"))["items"]]
    assert room["id"] not in public_ids
    assert (await listed(client, admin, "/admin/categories", active_only=True))["total"] == 11

    relabelled = await client.patch(f"/admin/categories/{room['id']}", headers=auth(admin),
                                    json={"is_active": True, "label": "Meeting rooms"})
    assert relabelled.json()["is_active"] is True and relabelled.json()["label"] == "Meeting rooms"
    assert room["id"] in [c["id"] for c in
                          (await listed(client, buyer, "/catalog/categories"))["items"]]

    trail = await listed(client, admin, "/admin/audit-logs", action="admin.category_updated")
    assert trail["total"] == 2
    for entry in trail["items"]:
        assert entry["entity_type"] == "capacity_category"
        assert entry["entity_id"] == room["id"] and entry["actor_user_id"] == admin["user"]["id"]
        assert entry["created_at"].endswith("Z") and entry["request_id"]
    label_change = next(r for r in trail["items"] if r["after"]["label"] == "Meeting rooms")
    flag_change = next(r for r in trail["items"] if "label" not in r["after"])
    assert label_change["before"] == {"is_active": False, "label": room["label"]}
    assert label_change["after"] == {"is_active": True, "label": "Meeting rooms"}
    assert flag_change["before"] == {"is_active": True}
    assert flag_change["after"] == {"is_active": False}

    assert (await listed(client, admin, "/admin/audit-logs",
                         entity_id=room["id"]))["total"] == 2
    assert (await listed(client, admin, "/admin/audit-logs",
                         actor_user_id=admin["user"]["id"]))["total"] == 2
    assert (await listed(client, admin, "/admin/audit-logs",
                         entity_type="booking"))["total"] == 0

    half = await client.get("/admin/audit-logs", headers=auth(admin),
                            params={"from": TODAY.isoformat()})
    assert half.status_code == 400
    assert half.json()["error"]["message"] == "`from` and `to` must be provided together"
    too_far = await client.get("/admin/audit-logs", headers=auth(admin), params={
        "from": (TODAY - timedelta(days=400)).isoformat(), "to": TODAY.isoformat()})
    assert too_far.status_code == 422
    assert "92 days" in too_far.json()["error"]["message"]


async def test_platform_analytics_labels_the_clock_each_number_uses(client, db):
    provider = await make_provider(client, "ad.prov@example.test")
    buyer = await register(client, "ad.buyer@example.test")
    support = await staff(client, db, "ad.support@example.test", "support")
    admin = await staff(client, db, "ad.admin@example.test", "platform_admin")
    offer = await live_offer(client, provider, quantity=2)
    room = next(c for c in (await listed(client, admin, "/admin/categories"))["items"]
                if c["key"] == "meeting_room")

    booking = await book_offer(client, buyer, offer["id"])
    empty = await analytics(client, admin)
    assert empty["bookings_created"] == 1, "the volume clock is the day the visit was made"
    assert empty["gmv_cents"] == 0, "an unpaid order is not money yet"

    await pay(client, buyer, booking)
    await run_to_completion(client, provider, booking)
    await add_review(client, buyer, booking["id"], rating=4, comment=COMMENT)

    done = await analytics(client, admin)
    assert done["from"].endswith("Z") and done["to"].endswith("Z")
    assert {k: done[k] for k in (
        "users_created", "providers_created", "offers_created", "bookings_created",
        "bookings_completed", "bookings_cancelled", "orders_placed", "gmv_cents",
        "commission_cents", "refunded_cents", "reviews_published", "rating_avg",
        "disputes_opened", "disputes_resolved")} == {
        "users_created": 4, "providers_created": 1, "offers_created": 1,
        "bookings_created": 1, "bookings_completed": 1, "bookings_cancelled": 0,
        "orders_placed": 1, "gmv_cents": 10000, "commission_cents": 1000,
        "refunded_cents": 0, "reviews_published": 1, "rating_avg": 4.0,
        "disputes_opened": 0, "disputes_resolved": 0}
    assert done["top_categories"] == [{"category_id": room["id"], "label": room["label"],
                                       "bookings": 1, "revenue_cents": 10000}]
    assert len(done["daily"]) == 31, "the trailing window is zero-filled day by day"
    assert done["daily"][0]["date"] == (TODAY - timedelta(days=30)).isoformat()
    assert done["daily"][-1] == {"date": TODAY.isoformat(), "bookings_created": 1,
                                 "orders_placed": 1, "gmv_cents": 10000}

    dispute = await open_dispute(client, buyer, booking["id"], description=DESCRIPTION)
    code, settled = await resolve(client, support, dispute["id"], "resolved_partial",
                                  refund_cents=4000)
    assert code == 200 and settled["refunded_cents"] == 4000, settled

    refunded = await analytics(client, admin)
    assert refunded["disputes_opened"] == 1 and refunded["disputes_resolved"] == 1
    assert refunded["gmv_cents"] == 6000 and refunded["refunded_cents"] == 4000
    assert refunded["commission_cents"] == 1000, "the fee is kept on what the platform kept"
    assert refunded["bookings_completed"] == 1, "a partial refund leaves the visit delivered"
    assert refunded["top_categories"][0]["revenue_cents"] == 6000
    assert refunded["daily"][-1]["gmv_cents"] == 6000

    today = await analytics(client, admin, **{"from": TODAY.isoformat(),
                                               "to": TODAY.isoformat()})
    assert today["daily"] == [{"date": TODAY.isoformat(), "bookings_created": 1,
                               "orders_placed": 1, "gmv_cents": 6000}]
    assert {k: v for k, v in today.items() if k not in ("from", "to", "daily")} == {
        k: v for k, v in refunded.items() if k not in ("from", "to", "daily")}
