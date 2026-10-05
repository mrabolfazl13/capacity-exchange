"""§8 dashboard screens: the provider window, the customer home bundle and platform totals.

Every assertion runs through the shipped routes, with the window pinned to the seeded
availability day so `utilization_pct` has a real denominator: the rule behind `live_offer`
is 09:00–18:00 at quantity 2 (18 unit-hours), and a 10:00–12:00 single-unit booking is 2 of
them — 11.11%.
"""
from __future__ import annotations

from datetime import date, timedelta

from tests.test_auth_api import auth, register
from tests.test_booking_api import OTHER_DAY, hold, live_offer
from tests.test_capacity_api import DAY, make_provider
from tests.test_commerce_api import OTHER_END, OTHER_START, book_offer
from tests.test_reviews_disputes_api import open_dispute, pay, staff

FULL_REFUND = [{"hours_before": 24, "refund_pct": 100}]
ALL_STATUSES = ("draft", "hold", "confirmed", "in_progress", "completed", "cancelled",
                "expired", "disputed")
TITLE = "Bright meeting room for workshops"


async def provider_screen(client, tokens, **params) -> dict:
    resp = await client.get("/dashboard/provider", headers=auth(tokens),
                            params=params or {"from": DAY, "to": DAY})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def customer_screen(client, tokens) -> dict:
    resp = await client.get("/dashboard/customer", headers=auth(tokens))
    assert resp.status_code == 200, resp.text
    return resp.json()


async def admin_screen(client, tokens) -> dict:
    resp = await client.get("/dashboard/admin", headers=auth(tokens))
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_an_empty_workspace_reports_zeroes_and_guards_its_window(client):
    provider = await make_provider(client, "db.empty@example.test")
    screen = await provider_screen(client, provider)
    assert screen["utilization_pct"] == 0
    assert screen["bookings_by_status"] == {status: 0 for status in ALL_STATUSES}
    assert screen["revenue_cents"] == 0 and screen["revenue_currency"] == "USD"
    assert screen["upcoming_bookings"] == [] and screen["top_offers"] == []

    half_pair = await client.get("/dashboard/provider", headers=auth(provider),
                                 params={"from": DAY})
    assert half_pair.status_code == 400
    assert half_pair.json()["error"]["message"] == "`from` and `to` must be provided together"

    reversed_pair = await client.get("/dashboard/provider", headers=auth(provider),
                                     params={"from": OTHER_DAY, "to": DAY})
    assert reversed_pair.status_code == 422
    assert reversed_pair.json()["error"]["code"] == "range_mismatch"

    far = {"from": DAY, "to": (date.fromisoformat(DAY) + timedelta(days=200)).isoformat()}
    oversized = await client.get("/dashboard/provider", headers=auth(provider), params=far)
    assert oversized.status_code == 422
    assert "92 days" in oversized.json()["error"]["message"]

    buyer = await register(client, "db.noug@example.test")
    no_org = await client.get("/dashboard/provider", headers=auth(buyer))
    assert no_org.status_code == 400
    assert no_org.json()["error"]["message"] == "This action needs an active organization"
    assert (await client.get("/dashboard/provider")).status_code == 401


async def test_the_provider_screen_adds_up_the_window_it_is_given(client):
    provider = await make_provider(client, "db.prov@example.test")
    offer = await live_offer(client, provider, quantity=2, cancellation_policy=FULL_REFUND)
    buyer = await register(client, "db.buyer@example.test")
    booking = await book_offer(client, buyer, offer["id"])

    screen = await provider_screen(client, provider)
    assert screen["utilization_pct"] == 11.11
    assert screen["bookings_by_status"]["confirmed"] == 1
    assert sum(screen["bookings_by_status"].values()) == 1
    assert screen["revenue_cents"] == 0, "an unpaid order is not revenue"
    assert screen["top_offers"] == [{"offer_id": offer["id"], "title": TITLE,
                                     "bookings": 1, "revenue_cents": 0}]

    card = screen["upcoming_bookings"][0]
    assert card["id"] == booking["id"] and card["status"] == "confirmed"
    assert card["offer_title"] == TITLE and card["resource_name"] == "Bookable Room"
    assert card["customer_name"] == "Test User" and card["review_submitted"] is False
    assert card["order"]["payment_status"] == "unpaid"

    await pay(client, buyer, booking)
    screen = await provider_screen(client, provider)
    assert screen["revenue_cents"] == 10000
    assert screen["top_offers"][0]["revenue_cents"] == 10000
    assert screen["utilization_pct"] == 11.11

    cancelled = await client.post(f"/bookings/{booking['id']}/cancel", headers=auth(buyer),
                                  json={"reason": "The team changed plans"})
    assert cancelled.status_code == 200, cancelled.text
    refund = cancelled.json()["refund_cents"]
    assert refund == 10000

    screen = await provider_screen(client, provider)
    assert screen["revenue_cents"] == 10000 - refund
    assert screen["utilization_pct"] == 0.0, "a cancelled booking consumed nothing"
    assert screen["bookings_by_status"]["cancelled"] == 1
    assert screen["bookings_by_status"]["confirmed"] == 0
    assert screen["top_offers"] == [] and screen["upcoming_bookings"] == []

    elsewhere = {"from": OTHER_DAY, "to": OTHER_DAY}
    other = await provider_screen(client, provider, **elsewhere)
    assert other["utilization_pct"] == 0.0
    assert sum(other["bookings_by_status"].values()) == 0, "the window must not leak days"


async def test_the_customer_screen_bundles_visits_spend_and_the_badge(client):
    provider = await make_provider(client, "db.prov2@example.test", name="North Court")
    offer = await live_offer(client, provider, quantity=2)
    buyer = await register(client, "db.buyer2@example.test")

    assert await customer_screen(client, buyer) == {
        "active_bookings": [], "spend_cents": 0, "spend_currency": "USD",
        "unread_notifications": 0, "recent_orders": []}

    booking = await book_offer(client, buyer, offer["id"])
    await pay(client, buyer, booking)
    held = await hold(client, buyer, offer["id"], start=OTHER_START, end=OTHER_END)

    badge = await client.get("/notifications", headers=auth(buyer), params={"unread": True})
    assert badge.status_code == 200
    screen = await customer_screen(client, buyer)
    assert screen["unread_notifications"] == badge.json()["total"] > 0
    assert screen["spend_cents"] == 10000 and screen["spend_currency"] == "USD"

    statuses = [row["status"] for row in screen["active_bookings"]]
    assert statuses == ["confirmed", "hold"], "ordered by the window the customer still has"
    first = screen["active_bookings"][0]
    assert first["id"] == booking["id"] and first["org_name"] == "North Court Org"
    assert screen["active_bookings"][1]["id"] == held["id"]
    assert screen["active_bookings"][1]["order"] is None, "a hold carries no order"

    assert [row["id"] for row in screen["recent_orders"]] == [booking["order_id"]]
    assert screen["recent_orders"][0]["provider_org_name"] == "North Court Org"
    assert screen["recent_orders"][0]["payment_status"] == "paid"

    read = await client.post("/notifications/read-all", headers=auth(buyer))
    assert read.status_code == 200 and read.json()["updated"] > 0
    assert (await customer_screen(client, buyer))["unread_notifications"] == 0


async def test_only_a_platform_role_opens_the_admin_totals(client, db):
    provider = await make_provider(client, "db.prov3@example.test")
    offer = await live_offer(client, provider, quantity=2)
    buyer = await register(client, "db.buyer3@example.test")
    booking = await book_offer(client, buyer, offer["id"])
    await pay(client, buyer, booking)

    anonymous = await client.get("/dashboard/admin")
    assert anonymous.status_code == 401
    for tokens, who in ((buyer, "customer"), (provider, "provider")):
        denied = await client.get("/dashboard/admin", headers=auth(tokens))
        assert denied.status_code == 403, who

    admin = await staff(client, db, "db.admin@example.test", "platform_admin")
    assert await admin_screen(client, admin) == {
        "users_total": 3, "orgs_total": 1, "offers_total": 1, "bookings_total": 1,
        "gmv_cents": 10000, "currency": "USD", "commission_cents": 1000,
        "open_disputes": 0}

    await open_dispute(client, buyer, booking["id"])
    assert (await admin_screen(client, admin))["open_disputes"] == 1
