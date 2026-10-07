"""End-to-end marketplace journey against a LIVE backend.

Covers the mandatory E2E path from docs/TESTING_STRATEGY.md:
register -> provider setup -> publish capacity -> customer search -> book (hold)
-> confirm -> provider fulfillment -> review.

Run with the backend reachable (see infrastructure/scripts/dev_backend.sh):

    python tests/e2e/journey.py --base http://127.0.0.1:8000/api/v1

Exits non-zero on the first unsatisfied step; prints a per-step report.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

DEMO_PASSWORD = "Demo1234!"
PASSED: list[str] = []


def _ok(name: str, detail: str = "") -> None:
    PASSED.append(name)
    print(f"  PASS  {name}{(' — ' + detail) if detail else ''}")


class StepError(RuntimeError):
    pass


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        _ok(name, detail)
    else:
        raise StepError(f"{name} failed — {detail or 'no detail'}")


class Client:
    """Thin REST wrapper honoring the CONTRACTS.md envelope."""

    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.http = httpx.Client(timeout=30.0)
        self.access: str | None = None
        self.refresh: str | None = None

    def request(self, method: str, path: str, *, json_body: Any = None,
                params: dict | None = None, auth: bool = True,
                idem: str | None = None) -> tuple[int, Any]:
        headers: dict[str, str] = {}
        if auth and self.access:
            headers["Authorization"] = f"Bearer {self.access}"
        if idem:
            headers["Idempotency-Key"] = idem
        resp = self.http.request(method, f"{self.base}{path}", json=json_body,
                                 params=params, headers=headers)
        body: Any = None
        if resp.content:
            try:
                body = resp.json()
            except json.JSONDecodeError:
                body = resp.text
        return resp.status_code, body

    def expect(self, method: str, path: str, status: int, **kw: Any) -> Any:
        code, body = self.request(method, path, **kw)
        if code != status:
            raise StepError(f"{method} {path} -> {code} (wanted {status}): "
                            f"{json.dumps(body, default=str)[:400]}")
        return body

    def error_code(self, method: str, path: str, **kw: Any) -> tuple[int, str]:
        code, body = self.request(method, path, **kw)
        ec = ""
        if isinstance(body, dict):
            ec = (body.get("error") or {}).get("code", "")
        return code, ec


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def journey(base: str) -> int:
    stamp = uuid.uuid4().hex[:8]
    tag = f"e2e{stamp}"
    provider = Client(base)
    customer = Client(base)

    print("\n[1] health + provider onboarding")
    hc = httpx.get(base.rsplit("/api", 1)[0] + "/health", timeout=15.0)
    check("GET /health reachable", hc.status_code == 200, f"{hc.status_code} {hc.text[:120]}")

    reg = provider.expect("POST", "/auth/register", 201, json_body={
        "email": f"owner.{tag}@e2e.test",
        "password": DEMO_PASSWORD,
        "full_name": f"E2E Provider {tag}",
        "roles": ["provider"],
        "organization": {"name": f"E2E Org {tag}", "slug": f"e2e-org-{tag}",
                         "currency": "USD", "timezone": "UTC"},
    }, auth=False)
    check("provider registered with org", bool(reg.get("access_token")) or bool(reg.get("tokens")),
          json.dumps(list(reg))[:160])
    provider.access = reg.get("access_token") or (reg.get("tokens") or {}).get("access_token")
    provider.refresh = reg.get("refresh_token") or (reg.get("tokens") or {}).get("refresh_token")
    check("provider refresh token issued", bool(provider.refresh))

    me = provider.expect("GET", "/auth/me", 200)
    check("provider identity + org + roles", "provider" in json.dumps(me),
          f"org={bool(me.get('organization_id') or me.get('org'))}")

    print("\n[2] capacity creation (wizard equivalent: resource -> definition -> availability -> offer -> publish)")
    cats = provider.expect("GET", "/catalog/categories", 200)
    items = cats.get("items", cats) if isinstance(cats, dict) else cats
    check("category catalog non-empty", len(items) >= 8, f"{len(items)} categories")
    room = next((c for c in items if c.get("key") == "meeting_room"), items[0])
    category_id = room["id"]

    resource = provider.expect("POST", "/capacities", 201, json_body={
        "name": f"E2E Room {tag}",
        "description": "Verification room created by the E2E journey.",
        "category_id": category_id,
        "capacity_mode": "scheduled",
        "address": {"line1": "1 Test St", "city": "Tehran", "state": "TH",
                    "postal_code": "11111", "country": "IR"},
        "lat": 35.7000, "lon": 51.4000, "timezone": "UTC",
        "attributes": {"capacity_persons": 8, "has_projector": True},
        "photos": [{"url": "https://example.test/room.jpg", "caption": "room", "sort_order": 0}],
    })
    resource_id = resource["id"]
    check("capacity resource created", bool(resource_id), f"status={resource.get('status')}")

    definition = provider.expect("POST", f"/capacities/{resource_id}/definitions", 201, json_body={
        "name": "Hour slot", "unit_label": "hour", "min_quantity": 1, "max_quantity": 2,
        "slot_duration_minutes": 60, "buffer_before_minutes": 0, "buffer_after_minutes": 0,
        "attributes": {},
    })
    definition_id = definition["id"]
    check("capacity definition created", bool(definition_id))

    # Anchored to an hour inside the 09:00-18:00 rule below, not to the wall clock:
    # a fixture that opens at the current hour is unbookable at 03:00 UTC, and the
    # journey would then be reporting on its own timing instead than the service.
    start = (utcnow() + timedelta(days=2)).replace(hour=10, minute=0, second=0,
                                                   microsecond=0)
    dow = start.weekday()
    provider.expect("POST", f"/capacities/{resource_id}/availability", 201, json_body={
        "definition_id": definition_id, "dow": dow,
        "start_time": "09:00:00", "end_time": "18:00:00", "quantity": 2,
        "valid_from": iso(start)[:10], "valid_until": (start + timedelta(days=90)).strftime("%Y-%m-%d"),
        "is_active": True,
    })
    check("recurring availability created", True, f"dow={dow}")

    free = provider.expect("GET", "/availability/free", 200, params={
        "definition_id": definition_id,
        "from": iso(start - timedelta(hours=1)),
        "to": iso(start + timedelta(hours=6)),
    })
    free_items = free.get("items", [])
    check("availability query returns windows", len(free_items) >= 1,
          f"{len(free_items)} window(s), first={json.dumps(free_items[0], default=str)[:140] if free_items else None}")

    offer = provider.expect("POST", "/offers", 201, json_body={
        "definition_id": definition_id,
        "title": f"E2E Meeting Room — {tag}",
        "description": "Bookable verification room with projector and whiteboard.",
        "pricing_mode": "per_unit_time", "unit_amount_cents": 45000, "currency": "USD",
        "min_lead_time_minutes": 60, "max_lead_time_days": 120,
        "min_duration_minutes": 60, "max_duration_minutes": 240,
        "min_quantity": 1, "max_quantity": 2,
        "booking_mode": "instant", "hold_minutes": 15,
        "cancellation_policy": [{"hours_before": 24, "refund_pct": 100},
                                {"hours_before": 0, "refund_pct": 50}],
    })
    offer_id = offer["id"]
    check("offer created as draft", offer.get("status") == "draft", f"status={offer.get('status')}")

    pub = provider.expect("POST", f"/offers/{offer_id}/publish", 200)
    check("offer published", pub.get("status") == "published", f"status={pub.get('status')}")

    print("\n[3] customer onboarding + marketplace discovery")
    cust_reg = customer.expect("POST", "/auth/register", 201, json_body={
        "email": f"buyer.{tag}@e2e.test", "password": DEMO_PASSWORD,
        "full_name": f"E2E Customer {tag}", "roles": ["customer"],
    }, auth=False)
    customer.access = cust_reg.get("access_token") or (cust_reg.get("tokens") or {}).get("access_token")
    customer.refresh = cust_reg.get("refresh_token") or (cust_reg.get("tokens") or {}).get("refresh_token")
    check("customer registered", bool(customer.access))

    search = customer.expect("GET", "/offers", 200, params={
        "q": "meeting room", "city": "Tehran", "country": "IR",
        "from": iso(start - timedelta(hours=1)), "to": iso(start + timedelta(hours=6)),
        "min_quantity": 1, "sort": "relevance", "limit": 20,
    })
    results = search.get("items", [])
    found = next((o for o in results if o.get("id") == offer_id), None)
    check("published offer discoverable via search+filters", found is not None,
          f"{len(results)} result(s); total={search.get('total')}")

    detail = customer.expect("GET", f"/offers/{offer_id}", 200)
    check("offer detail exposes pricing + capacity", detail.get("unit_amount_cents") == 45000,
          f"unit={detail.get('unit_amount_cents')} qty_max={detail.get('max_quantity')}")

    print("\n[4] hold -> book -> pay -> confirm")
    window_start = start
    window_end = start + timedelta(hours=2)
    fingerprint = str(uuid.uuid4())
    idem = f"hold-{tag}"
    hold_body = {
        "offer_id": offer_id, "definition_id": definition_id,
        "window_start": iso(window_start), "window_end": iso(window_end),
        "quantity": 1, "request_fingerprint": fingerprint,
    }
    hold = customer.expect("POST", "/bookings/hold", 201, json_body=hold_body, idem=idem)
    booking_id = hold["id"]
    check("hold created", hold.get("status") == "hold" and bool(hold.get("hold_expires_at")),
          f"expires={hold.get('hold_expires_at')}")

    replay = customer.expect("POST", "/bookings/hold", 201, json_body=hold_body, idem=idem)
    check("idempotent hold replay returns same booking", replay.get("id") == booking_id,
          f"{replay.get('id')} == {booking_id}")

    booking = customer.expect("POST", "/bookings", 201, json_body={
        "hold_id": booking_id,
    }, idem=f"book-{tag}")
    # Conversion mints a booking and retires the hold it came from (§5.5): the ids are
    # different rows, so every step below has to follow the booking, not the hold.
    hold_id, booking_id = booking_id, booking["id"]
    check("booking created from hold", booking.get("status") == "confirmed",
          f"status={booking.get('status')} payment={booking.get('payment_status')}")
    check("hold and booking are separate rows", hold_id != booking_id,
          f"hold={hold_id[:8]} booking={booking_id[:8]}")

    orders = customer.expect("GET", "/orders", 200)
    order_items = orders.get("items", [])
    order = next((o for o in order_items if o.get("booking_id") == booking_id), None)
    if order is None and booking.get("order_id"):
        order = customer.expect("GET", f"/orders/{booking['order_id']}", 200)
    check("order exists for booking", order is not None,
          f"orders={len(order_items)} total_cents={order.get('total_cents') if order else None}")

    if order and order.get("payment_status") in ("unpaid", "draft", None) and order.get("total_cents"):
        intent = customer.expect("POST", "/payments/intents", 201,
                                 json_body={"order_id": order["id"], "provider_key": "mock"},
                                 idem=f"intent-{tag}")
        payment_id = intent["id"]
        check("payment intent created", intent.get("status") in ("created", "pending"),
              f"status={intent.get('status')}")
        confirmed_pay = customer.expect("POST", f"/payments/{payment_id}/confirm", 200,
                                        json_body={}, idem=f"confirm-{tag}")
        check("payment confirmed", confirmed_pay.get("status") == "succeeded",
              f"status={confirmed_pay.get('status')}")
        booking = customer.expect("GET", f"/bookings/{booking_id}", 200)
        check("booking reflects payment", booking.get("payment_status") == "paid"
              or booking.get("status") == "confirmed",
              f"status={booking.get('status')} payment={booking.get('payment_status')}")
    else:
        _ok("payment path", "order already paid / free — intent skipped")

    print("\n[5] provider fulfillment")
    prov_booking = provider.expect("GET", f"/bookings/{booking_id}", 200)
    check("provider sees the booking (org scope)", prov_booking.get("id") == booking_id)

    if prov_booking.get("status") == "confirmed":
        provider.expect("POST", f"/bookings/{booking_id}/start", 200)
    state = provider.expect("GET", f"/bookings/{booking_id}", 200)
    check("booking in progress", state.get("status") == "in_progress", f"status={state.get('status')}")
    provider.expect("POST", f"/bookings/{booking_id}/complete", 200)
    done = provider.expect("GET", f"/bookings/{booking_id}", 200)
    check("booking completed", done.get("status") == "completed", f"status={done.get('status')}")

    timeline = customer.expect("GET", f"/bookings/{booking_id}/timeline", 200)
    events = timeline.get("items", [])
    seen = {e.get("to_status") for e in events}
    check("status timeline recorded", {"confirmed", "in_progress", "completed"} <= seen,
          f"events={sorted(x for x in seen if x)}")

    hold_tl = customer.expect("GET", f"/bookings/{hold_id}/timeline", 200)
    seen_hold = {e.get("to_status") for e in hold_tl.get("items", [])}
    check("hold retired on conversion", {"hold", "cancelled"} <= seen_hold,
          f"events={sorted(x for x in seen_hold if x)}")

    print("\n[6] review, dashboards, notifications, RBAC")
    review = customer.expect("POST", "/reviews", 201, json_body={
        "booking_id": booking_id, "rating": 5,
        "comment": "Exactly as described; verification review from the E2E journey.",
    })
    check("review created", review.get("rating") == 5, f"status={review.get('status')}")
    offer_reviews = customer.expect("GET", f"/offers/{offer_id}/reviews", 200)
    check("review visible on offer", len(offer_reviews.get("items", [])) >= 1)

    dup_code = customer.error_code("POST", "/reviews", json_body={
        "booking_id": booking_id, "rating": 3, "comment": "duplicate attempt"})
    check("duplicate review rejected", dup_code[0] == 409 and dup_code[1] == "already_rated",
          f"{dup_code}")

    dash_p = provider.expect("GET", "/dashboard/provider", 200, params={
        "from": iso(utcnow() - timedelta(days=7)), "to": iso(utcnow() + timedelta(days=30))})
    check("provider dashboard aggregates", "bookings_by_status" in json.dumps(dash_p),
          json.dumps(list(dash_p))[:160])
    dash_c = customer.expect("GET", "/dashboard/customer", 200)
    check("customer dashboard aggregates", bool(dash_c), json.dumps(list(dash_c))[:160])

    notes = customer.expect("GET", "/notifications", 200, params={"limit": 50})
    check("notifications delivered to customer", len(notes.get("items", [])) >= 1,
          f"{len(notes.get('items', []))} notification(s)")

    anon = Client(base)
    code, _ = anon.request("GET", "/bookings", auth=False)
    check("unauthenticated booking list -> 401", code == 401, f"{code}")
    other = Client(base)
    # Signed in, not anonymous: an unauthenticated request answers 401 and would let the
    # tenant boundary pass without ever being tested.
    other_reg = other.expect("POST", "/auth/register", 201, json_body={
        "email": f"nosy.{tag}@e2e.test", "password": DEMO_PASSWORD,
        "full_name": "Nosy", "roles": ["customer"]}, auth=False)
    other.access = other_reg.get("access_token") or (other_reg.get("tokens") or {}).get("access_token")
    check("stranger is authenticated", bool(other.access))
    code_cross, ec = other.error_code("GET", f"/bookings/{booking_id}")
    check("cross-user booking read denied", code_cross in (403, 404), f"{code_cross} {ec}")
    code_prov, ec_prov = provider.error_code("GET", "/admin/users")
    check("role escalation denied (provider -> admin)", code_prov == 403, f"{code_prov} {ec_prov}")

    print("\n[7] cancellation policy path (second hold, then cancel)")
    cancel_hold = customer.expect("POST", "/bookings/hold", 201, json_body={
        "offer_id": offer_id, "definition_id": definition_id,
        "window_start": iso(window_start + timedelta(days=7)),
        "window_end": iso(window_start + timedelta(days=7, hours=1)),
        "quantity": 1, "request_fingerprint": str(uuid.uuid4()),
    }, idem=f"hold2-{tag}")
    cancelled = customer.expect("POST", f"/bookings/{cancel_hold['id']}/cancel", 200,
                                json_body={"reason": "E2E cancel check"})
    check("hold cancellable", cancelled.get("status") == "cancelled", f"status={cancelled.get('status')}")
    freed = customer.expect("GET", "/availability/free", 200, params={
        "definition_id": definition_id,
        "from": iso(window_start + timedelta(days=7, hours=-1)),
        "to": iso(window_start + timedelta(days=7, hours=3))})
    check("freed capacity visible after cancel", len(freed.get("items", [])) >= 1)

    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000/api/v1")
    args = ap.parse_args()
    print(f"E2E journey against {args.base}")
    try:
        rc = journey(args.base)
    except StepError as exc:
        print(f"\nFAIL {exc}")
        print(f"\npassed {len(PASSED)} step(s) before the failure")
        return 1
    except httpx.HTTPError as exc:
        print(f"\nFAIL transport error: {exc}")
        return 1
    print(f"\nDONE — {len(PASSED)} checks passed")
    return rc


if __name__ == "__main__":
    sys.exit(main())
