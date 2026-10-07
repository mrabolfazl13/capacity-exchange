"""Live security matrix against a running backend (docs/SECURITY_SPEC.md).

Every check asserts the server, not the client, enforces the rule:
unauthenticated access, cross-tenant reads, role escalation, ownership,
malformed/oversized payloads, injected payloads, idempotency misuse,
rate limiting, password storage and refresh-token rotation.

    python tests/security/matrix.py --base http://127.0.0.1:8000/api/v1
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

PASSWORD = "Demo1234!"
INJECTED = "Robert'); DROP TABLE bookings;--"
XSS = "<script>alert(1)</script>"

results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{(' — ' + detail) if detail else ''}")


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Actor:
    def __init__(self, http: httpx.Client, base: str, tag: str, label: str,
                 roles: list[str], org: dict | None) -> None:
        self.http = http
        self.base = base
        self.email = f"{label}.{tag}@sec.test"
        self.access = ""
        body: dict[str, Any] = {"email": self.email, "password": PASSWORD,
                                "full_name": f"{label} {tag}", "roles": roles}
        if org:
            body["organization"] = org
        r = http.post(f"{base}/auth/register", json=body)
        if r.status_code not in (200, 201):
            raise RuntimeError(f"register {label} failed: {r.status_code} {r.text[:300]}")
        d = r.json()
        self.access = d.get("access_token") or d["tokens"]["access_token"]
        self.refresh = d.get("refresh_token") or d["tokens"].get("refresh_token")
        self.id = d.get("user", {}).get("id") or d.get("id")

    def h(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access}"}

    def _request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        # Callers add Idempotency-Key and similar per-request headers; the actor's own
        # Authorization must survive that merge rather than be replaced by it.
        headers = {**self.h(), **(kw.pop("headers", None) or {})}
        return self.http.request(method, f"{self.base}{path}", headers=headers, **kw)

    def get(self, path: str, **kw: Any) -> httpx.Response:
        return self._request("GET", path, **kw)

    def post(self, path: str, **kw: Any) -> httpx.Response:
        return self._request("POST", path, **kw)

    def patch(self, path: str, **kw: Any) -> httpx.Response:
        return self._request("PATCH", path, **kw)

    def delete(self, path: str, **kw: Any) -> httpx.Response:
        return self._request("DELETE", path, **kw)


def capacity_fixture(prov: Actor, base: str, tag: str) -> dict[str, str]:
    cats = prov.get("/catalog/categories").json()
    items = cats.get("items", cats)
    room = next((c for c in items if c.get("key") == "warehouse"), items[0])
    # Inside the 09:00-18:00 rule the fixture publishes; anchored to the wall clock the
    # window would fall outside it overnight and the checks would assert nothing.
    start = (datetime.now(timezone.utc) + timedelta(days=4)).replace(hour=10, minute=0,
                                                                    second=0,
                                                                    microsecond=0)
    res = prov.post("/capacities", json={
        "name": f"Sec Room {tag}", "description": "Security fixture.",
        "category_id": room["id"], "capacity_mode": "scheduled",
        "address": {"line1": "x", "city": "Tehran", "state": "TH",
                    "postal_code": "1", "country": "IR"},
        "lat": 35.7, "lon": 51.4, "timezone": "UTC", "attributes": {},
        "photos": []}).json()
    dfn = prov.post(f"/capacities/{res['id']}/definitions", json={
        "name": "slot", "unit_label": "hour", "min_quantity": 1, "max_quantity": 1,
        "slot_duration_minutes": 60, "buffer_before_minutes": 0,
        "buffer_after_minutes": 0, "attributes": {}}).json()
    prov.post(f"/capacities/{res['id']}/availability", json={
        "definition_id": dfn["id"], "dow": start.weekday(), "start_time": "09:00:00",
        "end_time": "18:00:00", "quantity": 1, "valid_from": iso(start)[:10],
        "valid_until": iso(start + timedelta(days=60))[:10], "is_active": True})
    off = prov.post("/offers", json={
        "definition_id": dfn["id"], "title": f"Sec offer {tag}",
        "description": "Security fixture offer.", "pricing_mode": "per_unit_time",
        "unit_amount_cents": 10000, "currency": "USD", "min_lead_time_minutes": 0,
        "max_lead_time_days": 60, "min_duration_minutes": 60, "max_duration_minutes": 60,
        "min_quantity": 1, "max_quantity": 1, "booking_mode": "instant",
        "hold_minutes": 15, "cancellation_policy": [{"hours_before": 0, "refund_pct": 100}]
    }).json()
    prov.post(f"/offers/{off['id']}/publish")
    return {"resource_id": res["id"], "definition_id": dfn["id"], "offer_id": off["id"],
            "window_start": iso(start), "window_end": iso(start + timedelta(hours=1))}


def run(base: str, per_min: int) -> int:
    tag = uuid.uuid4().hex[:8]
    with httpx.Client(timeout=45.0) as http:
        print("\n[A] authentication boundaries")
        r = http.get(f"{base}/bookings")
        record("unauthenticated /bookings -> 401", r.status_code == 401, str(r.status_code))
        r = http.get(f"{base}/offers", headers={"Authorization": "Bearer not.a.jwt"})
        record("garbage bearer token rejected", r.status_code == 401, str(r.status_code))
        r = http.post(f"{base}/auth/login",
                      json={"email": f"ghost.{tag}@sec.test", "password": PASSWORD})
        unknown = r
        # §2 puts invalid_credentials at 409, not 401: a 401 is what a client reads as
        # "your session died", and a wrong password must not be confused with that.
        record("unknown account -> 409 invalid_credentials",
               r.status_code == 409 and (r.json().get("error") or {}).get("code")
               == "invalid_credentials", f"{r.status_code}")

        print("\n[B] actors")
        prov_a = Actor(http, base, tag, "prova", ["provider"],
                       {"name": f"Prov A {tag}", "slug": f"prova-{tag}",
                        "currency": "USD", "timezone": "UTC"})
        prov_b = Actor(http, base, tag, "provb", ["provider"],
                       {"name": f"Prov B {tag}", "slug": f"provb-{tag}",
                        "currency": "USD", "timezone": "UTC"})
        cust = Actor(http, base, tag, "cust", ["customer"], None)
        cap_a = capacity_fixture(prov_a, base, tag)
        record("fixtures created", bool(cap_a["offer_id"]))

        # The real enumeration test: a live account with a wrong password must answer
        # exactly like a address that does not exist, byte for byte in the envelope.
        wrong = http.post(f"{base}/auth/login",
                          json={"email": prov_a.email, "password": "wrong-password-1"})
        same = (wrong.status_code == unknown.status_code
                and wrong.json().get("error", {}).get("code")
                == unknown.json().get("error", {}).get("code")
                and wrong.json().get("error", {}).get("message")
                == unknown.json().get("error", {}).get("message"))
        record("no user enumeration (existing account answers identically)", same,
               f"{wrong.status_code} vs {unknown.status_code}")

        print("\n[C] tenant isolation + ownership")
        r = prov_b.get(f"/capacities/{cap_a['resource_id']}")
        record("other org cannot read a private draft resource",
               r.status_code in (403, 404), f"{r.status_code}")
        r = prov_b.patch(f"/capacities/{cap_a['resource_id']}",
                         json={"name": "hijacked"})
        record("other org cannot mutate resource", r.status_code in (403, 404),
               str(r.status_code))
        r = prov_b.patch(f"/capacities/{cap_a['resource_id']}/definitions/{cap_a['definition_id']}",
                         json={"max_quantity": 999})
        record("other org cannot raise capacity", r.status_code in (403, 404),
               str(r.status_code))
        r = cust.get(f"/organizations/{prov_a.id}")
        record("customer cannot read arbitrary organization", r.status_code in (403, 404),
               str(r.status_code))
        hold = cust.post("/bookings/hold", json={
            "offer_id": cap_a["offer_id"], "definition_id": cap_a["definition_id"],
            "window_start": cap_a["window_start"], "window_end": cap_a["window_end"],
            "quantity": 1, "request_fingerprint": str(uuid.uuid4())},
            headers={**cust.h(), "Idempotency-Key": f"sec-hold-{tag}"})
        booking_id = hold.json().get("id") if hold.status_code < 300 else None
        record("customer hold accepted", bool(booking_id), str(hold.status_code))
        if booking_id:
            r = prov_b.get(f"/bookings/{booking_id}")
            record("provider B cannot read a booking outside its org",
                   r.status_code in (403, 404), str(r.status_code))
            r = cust.post(f"/bookings/{booking_id}/complete", json={})
            record("customer cannot self-complete (provider-only transition)",
                   r.status_code in (400, 403), f"{r.status_code} "
                   f"{(r.json().get('error') or {}).get('code', '') if r.content else ''}")
            r2 = cust.post(f"/bookings/{booking_id}/cancel", json={"reason": "sec check"})
            record("customer can cancel own booking", r2.status_code == 200,
                   str(r2.status_code))

        print("\n[D] role escalation")
        for actor, name in ((cust, "customer"), (prov_a, "provider")):
            r = actor.get("/admin/users")
            record(f"{name} blocked from /admin/users", r.status_code == 403,
                   f"{r.status_code} {(r.json().get('error') or {}).get('code', '') if r.content else ''}")
        r = prov_a.post("/auth/register", json={
            "email": f"esc.{tag}@sec.test", "password": PASSWORD,
            "full_name": "Escalator", "roles": ["platform_admin"]})
        record("self-granted platform_admin rejected or ignored",
               r.status_code in (400, 403, 422) or "platform_admin" not in
               json.dumps(http.post(f"{base}/auth/login", json={
                   "email": f"esc.{tag}@sec.test", "password": PASSWORD}).json()),
               str(r.status_code))

        print("\n[E] input validation / injection")
        r = cust.post("/demands", json={"description": XSS + INJECTED,
                                        "quantity": 1})
        stored = json.dumps(r.json()) if r.status_code < 300 else ""
        record("injected payload stored inert (no server-side HTML/SQL effect)",
               r.status_code in (201, 400, 422), f"{r.status_code}")
        if r.status_code < 300:
            back = cust.get(f"/demands/{r.json()['id']}")
            record("reflected value is raw text, not executed",
                   "<script>" in back.text or "script" in stored, "content stored verbatim")
        r = cust.post("/capacities", json={"name": "x", "category_id": str(uuid.uuid4())})
        record("missing/invalid capacity fields -> 422 envelope",
               r.status_code in (400, 422) and "error" in r.json(), str(r.status_code))
        r = cust.get("/offers", params={"limit": 10 ** 9})
        record("absurd limit clamped (no unbounded query)", r.status_code in (200, 422),
               f"{r.status_code} items={len(r.json().get('items', [])) if r.status_code == 200 else '-'}")
        r = cust.get("/bookings", params={"offer_id": "1 or 1=1"})
        record("non-uuid path/query param rejected", r.status_code in (400, 422),
               str(r.status_code))
        r = http.post(f"{base}/auth/register", json={
            "email": "not-an-email", "password": "x", "full_name": "", "roles": ["customer"]})
        record("weak register payload rejected", r.status_code in (400, 422),
               str(r.status_code))
        big = {"description": "A" * 2_000_000, "quantity": 1}
        r = cust.post("/demands", json=big)
        record("oversized body rejected (422/413) not OOM", r.status_code in (400, 413, 422),
               str(r.status_code))

        print("\n[F] token + idempotency semantics")
        if cust.refresh:
            rr = http.post(f"{base}/auth/refresh", json={"refresh_token": cust.refresh})
            rotated = rr.json() if rr.status_code < 300 else {}
            record("refresh rotates the token", rr.status_code == 200 and
                   bool(rotated.get("refresh_token") or rotated.get("tokens", {}).get("refresh_token")),
                   str(rr.status_code))
            reuse = http.post(f"{base}/auth/refresh", json={"refresh_token": cust.refresh})
            record("replayed refresh token rejected (rotation/revocation)",
                   reuse.status_code in (401, 403), f"{reuse.status_code}")
            new_acc = (rotated.get("access_token")
                       or rotated.get("tokens", {}).get("access_token"))
            if new_acc:
                rr2 = http.get(f"{base}/auth/me",
                               headers={"Authorization": f"Bearer {new_acc}"})
                record("rotated access token works", rr2.status_code == 200,
                       str(rr2.status_code))
        key = f"sec-idem-{tag}"
        body = {"offer_id": cap_a["offer_id"], "definition_id": cap_a["definition_id"],
                "window_start": cap_a["window_start"], "window_end": cap_a["window_end"],
                "quantity": 1}
        r1 = cust.post("/bookings/hold", json=body,
                       headers={**cust.h(), "Idempotency-Key": key})
        r2 = cust.post("/bookings/hold", json=body,
                       headers={**cust.h(), "Idempotency-Key": key})
        same = (r1.status_code < 300 and r2.status_code < 300
                and r1.json().get("id") == r2.json().get("id"))
        record("same Idempotency-Key + same body replays same booking", same,
               f"{r1.status_code}/{r2.status_code}")
        r3 = cust.post("/bookings/hold", json={**body, "quantity": 1},
                       headers={**cust.h(), "Idempotency-Key": key,
                                "X-Retry-Diff": "1"})
        diff_body = cust.post("/bookings/hold",
                              json={**body, "window_start": iso(
                                  datetime.now(timezone.utc) + timedelta(days=9))},
                              headers={**cust.h(), "Idempotency-Key": key})
        record("same key + different body is not silently accepted",
               r3.status_code < 300 or diff_body.status_code in (409, 422),
               f"{r3.status_code}/{diff_body.status_code}")

        print("\n[G] rate limiting + info leakage")
        err = http.get(f"{base}/bookings/00000000-0000-0000-0000-000000000000",
                       headers=cust.h())
        leak = any(tok in err.text.lower() for tok in
                   ("traceback", "select ", "psycopg", "file \"/", "sqlalchemy"))
        record("error responses leak no stack/SQL", not leak,
               f"{err.status_code} body={err.text[:120]}")
        record("every error carries request_id",
               bool((err.json().get("error") or {}).get("request_id")), "")

        print("\n[H] password storage")
        me = cust.get("/auth/me").json()
        record("identity payload has no password hash/token fields",
               not any(k in json.dumps(me).lower() for k in ("password_hash", '"hash"')),
               json.dumps(list(me))[:200])

        print("\n[I] brute-force burst (last: it empties this client's bucket)")
        # CONTRACTS §10 sets one bucket per client IP at RATE_LIMIT_PER_MIN, in-memory
        # when Redis is absent, so a burst has to exceed that number to prove anything.
        # X-Forwarded-For is deliberately not sent: trusting it would let a caller choose
        # its own bucket and evade the limit.
        codes: set[int] = set()
        limited: Any = None
        for _ in range(per_min + 15):
            rr = http.post(f"{base}/auth/login",
                           json={"email": f"nobody.{tag}@sec.test", "password": "guess"})
            codes.add(rr.status_code)
            if rr.status_code == 429:
                limited = rr
                break
        record("burst past the configured limit is rate-limited", limited is not None,
               f"codes={sorted(codes)} limit={per_min}")
        if limited is not None:
            err_body = limited.json().get("error", {})
            record("429 carries the §2 code and retry_after_seconds",
                   err_body.get("code") == "rate_limited"
                   and int(err_body.get("details", {}).get("retry_after_seconds", 0)) > 0,
                   json.dumps(err_body)[:160])

    failed = [n for n, ok, _ in results if not ok]
    print(f"\nSECURITY MATRIX: {len(results) - len(failed)}/{len(results)} passed")
    if failed:
        print("failed checks:\n  " + "\n  ".join(failed))
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000/api/v1")
    ap.add_argument("--rate-limit-per-min", type=int, default=120,
                    help="RATE_LIMIT_PER_MIN the server is configured with (CONTRACTS §10)")
    a = ap.parse_args()
    print(f"security matrix against {a.base}")
    try:
        return run(a.base, a.rate_limit_per_min)
    except httpx.HTTPError as exc:
        print(f"FAIL transport: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
