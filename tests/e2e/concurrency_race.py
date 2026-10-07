"""Booking concurrency race harness against a LIVE backend.

Proves the mandatory guarantee from docs/TESTING_STRATEGY.md and CONTRACTS.md §5.5:
N parallel holds against a definition with capacity < N must yield exactly
`capacity` successes and no double booking.

    python tests/e2e/concurrency_race.py --base http://127.0.0.1:8000/api/v1 --attempters 8
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

DEMO_PASSWORD = "Demo1234!"


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def register(http: httpx.AsyncClient, base: str, email: str, name: str,
                   roles: list[str], org: dict | None) -> dict[str, str]:
    body: dict[str, Any] = {"email": email, "password": DEMO_PASSWORD,
                            "full_name": name, "roles": roles}
    if org:
        body["organization"] = org
    r = await http.post(f"{base}/auth/register", json=body)
    r.raise_for_status()
    d = r.json()
    return {"access": d.get("access_token") or d["tokens"]["access_token"]}


async def setup_capacity(http: httpx.AsyncClient, base: str, tok: dict,
                         tag: str, capacity: int) -> dict[str, str]:
    cats = (await http.get(f"{base}/catalog/categories",
                           headers={"Authorization": f"Bearer {tok['access']}"})).json()
    items = cats.get("items", cats)
    room = next((c for c in items if c.get("key") == "meeting_room"), items[0])
    # Anchored inside the 09:00-18:00 rule the fixture publishes; a window taken from
    # the current hour would be unbookable overnight and prove nothing about the race.
    start = (datetime.now(timezone.utc) + timedelta(days=3)).replace(
        hour=10, minute=0, second=0, microsecond=0)
    res = (await http.post(f"{base}/capacities", json={
        "name": f"Race Room {tag}", "description": "Concurrency race fixture.",
        "category_id": room["id"], "capacity_mode": "scheduled",
        "address": {"line1": "x", "city": "Tehran", "state": "TH",
                    "postal_code": "1", "country": "IR"},
        "lat": 35.7, "lon": 51.4, "timezone": "UTC", "attributes": {},
        "photos": [],
    }, headers={"Authorization": f"Bearer {tok['access']}"})).json()
    dfn = (await http.post(f"{base}/capacities/{res['id']}/definitions", json={
        "name": "slot", "unit_label": "hour", "min_quantity": 1,
        "max_quantity": capacity, "slot_duration_minutes": 60,
        "buffer_before_minutes": 0, "buffer_after_minutes": 0, "attributes": {},
    }, headers={"Authorization": f"Bearer {tok['access']}"})).json()
    avail = await http.post(f"{base}/capacities/{res['id']}/availability", json={
        "definition_id": dfn["id"], "dow": start.weekday(),
        "start_time": "09:00:00", "end_time": "18:00:00", "quantity": capacity,
        "valid_from": iso(start)[:10],
        "valid_until": iso(start + timedelta(days=90))[:10], "is_active": True,
    }, headers={"Authorization": f"Bearer {tok['access']}"})
    if avail.status_code != 201:
        raise SystemExit(f"FAIL fixture availability rejected: {avail.status_code} {avail.text[:200]}")
    off = (await http.post(f"{base}/offers", json={
        "definition_id": dfn["id"], "title": f"Race offer {tag}",
        "description": "Concurrency race offer.", "pricing_mode": "per_unit_time",
        "unit_amount_cents": 20000, "currency": "USD", "min_lead_time_minutes": 0,
        "max_lead_time_days": 90, "min_duration_minutes": 60,
        "max_duration_minutes": 60, "min_quantity": 1, "max_quantity": capacity,
        "booking_mode": "instant", "hold_minutes": 15,
        "cancellation_policy": [{"hours_before": 0, "refund_pct": 100}],
    }, headers={"Authorization": f"Bearer {tok['access']}"})).json()
    published = await http.post(f"{base}/offers/{off['id']}/publish",
                                headers={"Authorization": f"Bearer {tok['access']}"})
    if published.status_code != 200:
        raise SystemExit(f"FAIL fixture offer not published: "
                         f"{published.status_code} {published.text[:200]}")
    return {"offer_id": off["id"], "definition_id": dfn["id"],
           "window_start": iso(start), "window_end": iso(start + timedelta(hours=1))}


async def attempt(http: httpx.AsyncClient, base: str, access: str, cap: dict[str, str],
                  idx: int, tag: str) -> tuple[int, str, str]:
    headers = {"Authorization": f"Bearer {access}",
               "Idempotency-Key": f"race-{tag}-{idx}"}
    body = {"offer_id": cap["offer_id"], "definition_id": cap["definition_id"],
            "window_start": cap["window_start"], "window_end": cap["window_end"],
            "quantity": 1, "request_fingerprint": str(uuid.uuid4())}
    r = await http.post(f"{base}/bookings/hold", json=body, headers=headers)
    code = ""
    if r.status_code >= 400 and r.content:
        try:
            code = (r.json().get("error") or {}).get("code", "")
        except json.JSONDecodeError:
            code = "unparsable"
    bid = ""
    if r.status_code < 300:
        bid = r.json().get("id", "")
    return r.status_code, code, bid


async def run_race(base: str, attempters: int, capacity: int) -> int:
    tag = uuid.uuid4().hex[:8]
    limits = httpx.Limits(max_connections=attempters + 5)
    async with httpx.AsyncClient(timeout=60.0, limits=limits) as http:
        prov = await register(http, base, f"raceprov.{tag}@e2e.test",
                              f"Race Provider {tag}", ["provider"],
                              {"name": f"Race Org {tag}", "slug": f"race-{tag}",
                               "currency": "USD", "timezone": "UTC"})
        cap = await setup_capacity(http, base, prov, tag, capacity)
        tokens = [await register(http, base, f"race{t:02d}.{tag}@e2e.test",
                                 f"Race Customer {t}", ["customer"], None)
                  for t in range(attempters)]
        print(f"capacity={capacity} units, {attempters} simultaneous holds")
        results = await asyncio.gather(*[
            attempt(http, base, tok["access"], cap, i, tag)
            for i, tok in enumerate(tokens)
        ])

    granted = [r for r in results if r[0] in (200, 201)]
    rejected = [r for r in results if r[0] >= 400]
    codes: dict[str, int] = {}
    for r in rejected:
        codes[r[1] or str(r[0])] = codes.get(r[1] or str(r[0]), 0) + 1
    print(f"granted={len(granted)} rejected={len(rejected)} codes={json.dumps(codes)}")

    ok = True
    if len(granted) != capacity:
        print(f"FAIL expected exactly {capacity} grants, got {len(granted)}")
        ok = False
    if len({r[2] for r in granted}) != len(granted):
        print("FAIL duplicate booking ids returned")
        ok = False
    if any(str(c).startswith("5") or c in ("internal_error", "") for c in codes):
        print(f"FAIL rejection not graceful: {codes}")
        ok = False

    prov_tok = prov["access"]
    async with httpx.AsyncClient(timeout=30.0) as http:
        # `provider=true` is the org workspace view; without it this lists the provider's
        # own bookings as a customer — an empty list, and a check that passes by itself.
        booked = (await http.get(
            f"{base}/bookings",
            params={"offer_id": cap["offer_id"], "provider": "true", "limit": 100},
            headers={"Authorization": f"Bearer {prov_tok}"})).json()
        active = [b for b in booked.get("items", [])
                  if b.get("status") in ("hold", "confirmed", "in_progress")]
        total_qty = sum(int(b.get("quantity", 1)) for b in active)
        print(f"server-side active bookings={len(active)} total_quantity={total_qty}")
        if len(active) != len(granted):
            print(f"FAIL server holds {len(active)} rows, but {len(granted)} holds were granted")
            ok = False
        if total_qty != capacity:
            print(f"FAIL units consumed {total_qty} != capacity {capacity}")
            ok = False

    print("RACE RESULT:", "PASS — no double booking" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000/api/v1")
    ap.add_argument("--attempters", type=int, default=8)
    ap.add_argument("--capacity", type=int, default=3)
    a = ap.parse_args()
    if a.capacity >= a.attempters:
        print("capacity must be lower than attempters to prove the race")
        return 2
    return asyncio.run(run_race(a.base, a.attempters, a.capacity))


if __name__ == "__main__":
    sys.exit(main())
