"""Performance gate: the four measurements docs/TESTING_STRATEGY.md names, over HTTP.

Run against a live API pointed at the volume database (tests/perf/volume.py), or let the wrapper
do all of it:

    bash infrastructure/scripts/perf_gate.sh                # rebuild volume db, boot api, measure
    python tests/perf/bench.py --base http://127.0.0.1:8010/api/v1 \\
        --dsn postgresql://capacity:capacity@127.0.0.1:5544/capacity_perf

The four named measurements, and why each is the shape that actually costs something:

1. **Marketplace search** (`GET /offers`) in both shapes the service has: the filter-only path is
   one SQL statement plus a count; the date-window path pulls a candidate page and then expands
   §5.2 availability *per candidate in Python*, so its cost grows with the candidate cap rather
   than with the page size. Those are different numbers, and reporting one as the other would be
   a lie.
2. **Availability queries** (`GET /availability/free`) — the slot picker, expanding recurring
   rules into concrete free windows over 7 and 30 days.
3. **Booking confirmation under contention** — 12 customers hold one window whose free capacity
   is read from the database, every granted hold is then converted in parallel (the §5.5 advisory
   lock serialises those writes on one definition), and a follow-up wave must be refused. This
   measures that serialisation *and* asserts the capacity math is still exact with 40,000 bookings
   in the table.
4. **Dashboard aggregation** (`GET /dashboard/provider` on the deliberately heavy provider, and
   `GET /dashboard/admin`) — utilization needs a denominator, so this is the read that expands
   the most calendar per request.

Every number is a wall-clock HTTP round trip on one box with one Postgres, so it is a p95 of
*this machine*, not a production capacity promise. Each phase also prints `EXPLAIN (ANALYZE,
BUFFERS)` for the statement the service compiles, because a latency number without a plan is a
number nobody can act on.

Budgets live in `BUDGETS_MS`; a breached p95 exits non-zero. Raising a budget is a documented
decision with the measured value printed next to it, never a way to make a red run look green.
"""
from __future__ import annotations

import argparse
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"

PASSWORD = "Perf1234!"
PROVIDER_EMAIL = "perf-provider@perf.test"
CUSTOMER_EMAIL = "perf-customer@perf.test"
ADMIN_EMAIL = "perf-admin@perf.test"
HEAVY_SLUG = "perf-heavy-org"

#: Round trips per read measurement, after warmup. 60 gives a stable p95 without the measurement
#: loop itself becoming the load.
SAMPLES = 60
WARMUP = 5
ATTEMPTERS = 12

#: p95 budgets in milliseconds, each a round number at or above the measurement it was set from.
BUDGETS_MS = {
    "search (filters only)": 150,
    "search (date window)": 1000,
    "availability (7 days)": 400,
    "availability (30 days)": 1500,
    "booking hold under contention": 1500,
    "booking confirm under contention": 2000,
    "dashboard provider (30 days)": 1500,
    "dashboard admin": 600,
}


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def day_start(day: date, hour: int = 0) -> datetime:
    return datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=hour)


def next_monday(after: date) -> date:
    return after + timedelta(days=(7 - after.weekday()) % 7 or 7)


@dataclass
class Sample:
    name: str
    ms: list[float] = field(default_factory=list)
    note: str = ""

    def percentile(self, pct: float) -> float:
        ordered = sorted(self.ms)
        index = min(len(ordered) - 1, max(0, int(round(pct / 100.0 * (len(ordered) - 1)))))
        return ordered[index]

    @property
    def p50(self) -> float:
        return self.percentile(50)

    @property
    def p95(self) -> float:
        return self.percentile(95)

    @property
    def worst(self) -> float:
        return max(self.ms)


class Api:
    """One httpx client, the fixture identities, and the timing helpers."""

    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.http = httpx.Client(timeout=180.0)
        self.tokens: dict[str, str] = {}

    def login(self, label: str, email: str) -> str:
        response = self.http.post(f"{self.base}/auth/login",
                                  json={"email": email, "password": PASSWORD})
        if response.status_code >= 400:
            raise RuntimeError(f"login {email} -> {response.status_code} {response.text[:200]}")
        body = response.json()
        token = body.get("access_token") or body["tokens"]["access_token"]
        self.tokens[label] = token
        return token

    def get(self, path: str, *, as_: str = "customer", token: str | None = None,
            **params) -> httpx.Response:
        return self.http.get(f"{self.base}{path}", params=params,
                             headers={"Authorization": f"Bearer {token or self.tokens[as_]}"})

    def post(self, path: str, *, as_: str = "customer", token: str | None = None,
             idem: str | None = None, body: dict | None = None) -> httpx.Response:
        headers = {"Authorization": f"Bearer {token or self.tokens[as_]}"}
        if idem:
            headers["Idempotency-Key"] = idem
        return self.http.post(f"{self.base}{path}", json=body or {}, headers=headers)

    def timed(self, name: str, call, *, note: str = "") -> Sample:
        sample = Sample(name=name, note=note)
        for _ in range(WARMUP):
            response = call()
            if response.status_code >= 400:
                raise RuntimeError(f"{name}: warm-up got {response.status_code} "
                                   f"{response.text[:200]}")
        for _ in range(SAMPLES):
            started = time.perf_counter()
            response = call()
            sample.ms.append((time.perf_counter() - started) * 1000.0)
            if response.status_code >= 400:
                raise RuntimeError(f"{name}: {response.status_code} {response.text[:200]}")
        return sample


# --------------------------------------------------------------------------- fixtures


def connect(dsn: str):
    import psycopg

    return psycopg.connect(dsn, autocommit=True)


def heavy_org_id(dsn: str) -> uuid.UUID:
    with connect(dsn) as conn:
        return conn.execute("SELECT id FROM organizations WHERE slug = %s",
                            (HEAVY_SLUG,)).fetchone()[0]


def pick_availability_target(dsn: str) -> dict:
    """The busiest definition of the heavy provider: the slot picker at its worst realistic shape."""
    org = heavy_org_id(dsn)
    with connect(dsn) as conn:
        row = conn.execute(
            """
            SELECT d.id, count(b.id) AS bookings
            FROM capacity_definitions d
            JOIN capacity_resources r ON r.id = d.resource_id
            LEFT JOIN bookings b ON b.definition_id = d.id
            WHERE r.org_id = %s
            GROUP BY d.id ORDER BY count(b.id) DESC, d.id LIMIT 1
            """, (org,)).fetchone()
    return {"definition_id": str(row[0]), "bookings": int(row[1])}


def pick_contention_target(dsn: str) -> dict:
    """A future 1-hour window on the heavy provider with 1..6 free units, computed server-side.

    `free` uses the same predicate as §5.5 (active overlapping bookings against the definition's
    ceiling), so the counts asserted afterwards are the invariant the booking engine maintains.
    """
    query = """
        WITH windows AS (
            SELECT d.id AS definition_id, o.id AS offer_id, d.max_quantity,
                   (current_date + ((n || ' days')::interval))::date AS day, h AS hour
            FROM offers o
            JOIN capacity_definitions d ON d.id = o.definition_id
            JOIN capacity_resources r ON r.id = d.resource_id
            JOIN organizations org ON org.id = r.org_id
            CROSS JOIN LATERAL (SELECT generate_series(8, 25) AS n) s
            CROSS JOIN LATERAL (SELECT generate_series(10, 17) AS h) t
            WHERE org.slug = %(slug)s AND o.status = 'published'
              AND o.booking_mode = 'instant' AND o.requires_payment
        ), counted AS (
            SELECT w.*,
                   w.max_quantity - coalesce((
                       SELECT sum(b.quantity) FROM bookings b
                       WHERE b.definition_id = w.definition_id
                         AND b.status IN ('hold','confirmed','in_progress')
                         AND b.window_start < make_timestamptz(extract(year from w.day)::int,
                               extract(month from w.day)::int, extract(day from w.day)::int,
                               w.hour + 1, 0, 0)
                         AND b.window_end > make_timestamptz(extract(year from w.day)::int,
                               extract(month from w.day)::int, extract(day from w.day)::int,
                               w.hour, 0, 0)
                   ), 0) AS free
            FROM windows w
            WHERE extract(dow FROM w.day) BETWEEN 1 AND 6
        )
        SELECT offer_id, definition_id, day, hour, free, max_quantity
        FROM counted WHERE free BETWEEN 1 AND 6
        ORDER BY definition_id, day, hour LIMIT 1
        """
    with connect(dsn) as conn:
        row = conn.execute(query, {"slug": HEAVY_SLUG}).fetchone()
    if row is None:
        raise SystemExit("no future window on the heavy provider has 1..6 free units, so the "
                         "contention fixture would prove nothing — widen the range in "
                         "pick_contention_target or rebuild the volume dataset")
    offer_id, definition_id, day, hour, free, capacity = row
    start = day_start(day, int(hour))
    return {"offer_id": str(offer_id), "definition_id": str(definition_id), "start": start,
            "end": start + timedelta(hours=1), "free": int(free), "capacity": int(capacity)}


def committed_units(dsn: str, definition_id: str, start: datetime, end: datetime) -> int:
    with connect(dsn) as conn:
        return int(conn.execute(
            """
            SELECT coalesce(sum(quantity), 0) FROM bookings
            WHERE definition_id = %s AND status IN ('hold','confirmed','in_progress')
              AND window_start < %s AND window_end > %s
            """, (uuid.UUID(definition_id), end, start)).fetchone()[0])


# --------------------------------------------------------------------------- EXPLAIN


def explain(conn, label: str, sql: str, *, lines: int = 6) -> list[str]:
    """EXPLAIN (ANALYZE, BUFFERS) and print the plan's top rows; returns them for the summary."""
    plan = [row[0] for row in conn.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) "
                                           + sql).fetchall()]
    print(f"\n  -- {label}")
    for row in plan[:lines]:
        print(f"     {row}")
    timing = next((row for row in plan if "Execution Time" in row), "")
    if timing:
        print(f"     {timing.strip()}")
    return plan


def search_sql(**params) -> str:
    """Compile the statement `marketplace.search()` would run, so the plan is the shipped one."""
    sys.path.insert(0, str(BACKEND_ROOT))
    from sqlalchemy import func, select

    from app.services import marketplace as svc

    p = svc.SearchParams(**params)
    stmt = svc._apply_sort(svc._apply_filters(svc._base_select(), p), p).limit(p.limit)
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


def count_sql(inner: str) -> str:
    return f"SELECT count(*) FROM ({inner}) AS sub"


# --------------------------------------------------------------------------- phases


def phase_search(api: Api, dsn: str) -> list[Sample]:
    print("\n[1/4] marketplace search")
    today = date.today()
    window_from = day_start(today + timedelta(days=3), 10)
    window_to = day_start(today + timedelta(days=4), 18)
    samples = [
        api.timed("search (filters only)",
                  lambda: api.get("/offers", q="hall", city="Tehran", limit=20),
                  note="full text + city, ranked"),
        api.timed("search (date window)",
                  lambda: api.get("/offers", city="Tehran", min_quantity=2,
                                  **{"from": iso(window_from), "to": iso(window_to)}),
                  note="candidate page + per-candidate availability"),
    ]
    with connect(dsn) as conn:
        inner = search_sql(q="hall", city="Tehran", limit=20)
        explain(conn, "GET /offers?q=hall&city=Tehran — the page statement", inner)
        explain(conn, "GET /offers?q=hall&city=Tehran — the total count over the same filters",
                count_sql(inner))
        date_inner = search_sql(city="Tehran", min_quantity=2, start=window_from, end=window_to,
                                limit=200)
        explain(conn, "GET /offers?from&to — the candidate page the Python loop walks", date_inner)
    return samples


def phase_availability(api: Api, dsn: str, target: dict) -> list[Sample]:
    print("\n[2/4] availability queries")
    start = next_monday(date.today())
    samples = []
    for label, days in (("availability (7 days)", 7), ("availability (30 days)", 30)):
        samples.append(api.timed(
            label,
            lambda d=days: api.get("/availability/free", definition_id=target["definition_id"],
                                   **{"from": iso(day_start(start)),
                                      "to": iso(day_start(start + timedelta(days=d)))}),
            note=f"busiest definition ({target['bookings']} bookings)"))
    with connect(dsn) as conn:
        explain(conn, "recurring rules of one definition (build_day_plan, per day range)",
                "SELECT * FROM recurring_availabilities WHERE definition_id = "
                f"'{target['definition_id']}' AND is_active")
        explain(conn, "committed_quantity over a 7-day range (the per-window aggregate)",
                f"""SELECT coalesce(sum(quantity), 0) FROM bookings
                   WHERE definition_id = '{target['definition_id']}'
                     AND status = ANY('{{hold,confirmed,in_progress}}')
                     AND window_start < make_timestamptz(extract(year from current_date)::int,
                           extract(month from current_date)::int,
                           (extract(day from current_date) + 7)::int, 0, 0, 0)
                     AND window_end > current_date""")
    return samples


def phase_contention(api: Api, dsn: str, target: dict, results: list[str]) -> list[Sample]:
    print("\n[3/4] booking confirmation under contention")
    tag = uuid.uuid4().hex[:8]
    bodies = [{"offer_id": target["offer_id"], "definition_id": target["definition_id"],
               "window_start": iso(target["start"]), "window_end": iso(target["end"]),
               "quantity": 1, "request_fingerprint": str(uuid.uuid4())}
              for _ in range(ATTEMPTERS)]
    tokens = [api.login(f"attempter{i}", f"perf-customer-{i}@perf.test")
              for i in range(1, ATTEMPTERS + 1)]
    print(f"  capacity={target['capacity']} free-before={target['free']} "
          f"window={target['start']:%a %d %b %H:%M}Z attempters={ATTEMPTERS}")

    def hold(i: int) -> tuple[float, httpx.Response]:
        started = time.perf_counter()
        response = api.post("/bookings/hold", token=tokens[i], idem=f"perf-hold-{tag}-{i}",
                            body=bodies[i])
        return (time.perf_counter() - started) * 1000.0, response

    with ThreadPoolExecutor(max_workers=ATTEMPTERS) as pool:
        held = list(pool.map(hold, range(ATTEMPTERS)))
    hold_sample = Sample(name="booking hold under contention", ms=[m for m, _ in held])
    granted = [(i, r.json()["id"]) for i, (_, r) in enumerate(held) if r.status_code == 201]
    refused = [(r.status_code, (r.json().get("error") or {}).get("code", ""))
               for _, r in held if r.status_code >= 400]
    print(f"  holds: granted={len(granted)} refused={len(refused)} "
          f"codes={sorted(set(c for _, c in refused))}")
    if len(granted) != target["free"]:
        results.append(f"FAIL holds granted {len(granted)}, database said {target['free']} "
                       "units were free")
    if any(str(code).startswith("5") for code, _ in refused):
        results.append(f"FAIL holds rejected with a server error: {refused}")

    def confirm(i: int, hold_id: str) -> tuple[float, httpx.Response]:
        started = time.perf_counter()
        # Conversion of the hold is the §5.5 write: it takes the definition lock, re-reads
        # committed quantity in-lock, and lands the booking row.
        response = api.post("/bookings", token=tokens[i], idem=f"perf-book-{tag}-{i}",
                            body={"hold_id": hold_id})
        return (time.perf_counter() - started) * 1000.0, response

    with ThreadPoolExecutor(max_workers=max(1, len(granted))) as pool:
        confirmed = list(pool.map(lambda pair: confirm(*pair), granted))
    confirm_sample = Sample(name="booking confirm under contention",
                            ms=[m for m, _ in confirmed],
                            note=f"{len(granted)} conversions serialised on one definition lock")
    paid = sum(1 for _, r in confirmed if r.status_code == 201)
    print(f"  confirms: {paid}/{len(granted)} converted, "
          f"p95={confirm_sample.p95:.0f} ms")
    if paid != len(granted):
        results.append(f"FAIL {len(granted) - paid} hold conversions did not land")

    after = committed_units(dsn, target["definition_id"], target["start"], target["end"])
    if after != target["capacity"]:
        results.append(f"FAIL window is {after}/{target['capacity']} units committed after the "
                       f"race, expected the ceiling")
    late = api.post("/bookings/hold", idem=f"perf-late-{tag}",
                    body={**bodies[0], "request_fingerprint": str(uuid.uuid4())})
    if late.status_code not in (404, 409):
        results.append(f"FAIL a hold past capacity returned {late.status_code}: "
                       f"{late.text[:160]}")
    with connect(dsn) as conn:
        explain(conn, "the in-lock aggregate every hold/confirm runs (committed_quantity)",
                f"""SELECT coalesce(sum(quantity), 0) FROM bookings
                   WHERE definition_id = '{target['definition_id']}'
                     AND status = ANY('{{hold,confirmed,in_progress}}')
                     AND window_start < '{iso(target['end'])}' AND window_end > '{iso(target['start'])}'""")
    return [hold_sample, confirm_sample]


def phase_dashboards(api: Api, dsn: str) -> list[Sample]:
    print("\n[4/4] dashboard aggregation")
    samples = [
        api.timed("dashboard provider (30 days)",
                  lambda: api.get("/dashboard/provider", as_="provider"),
                  note="24 resources, 48 definitions, ~2,400 bookings in the trailing window"),
        api.timed("dashboard admin", lambda: api.get("/dashboard/admin", as_="admin"),
                  note="unscoped platform totals"),
    ]
    org = heavy_org_id(dsn)
    with connect(dsn) as conn:
        explain(conn, "provider bookings_by_status over 30 days",
                f"""SELECT status, count(*) FROM bookings
                   WHERE org_id = '{org}' AND window_start >= current_date - 30
                     AND window_start < current_date
                   GROUP BY status""")
        explain(conn, "provider revenue over 30 days (orders joined to bookings)",
                f"""SELECT coalesce(sum(o.total_cents - o.refunded_cents), 0)
                   FROM orders o JOIN bookings b ON b.id = o.booking_id
                   WHERE o.provider_org_id = '{org}'
                     AND o.payment_status IN ('paid','partially_refunded')
                     AND b.window_start >= current_date - 30 AND b.window_start < current_date""")
        explain(conn, "supply denominator: the definitions a provider dashboard expands",
                f"""SELECT d.id, r.timezone FROM capacity_definitions d
                   JOIN capacity_resources r ON r.id = d.resource_id
                   WHERE r.org_id = '{org}' AND r.status <> 'archived' AND d.is_active
                   ORDER BY d.id""")
    return samples


# --------------------------------------------------------------------------- report


def report(samples: list[Sample], results: list[str]) -> int:
    print("\n=== PERFORMANCE LAYER (p50 / p95 / max, ms; one client, one box) ===")
    print(f"{'measurement':<34}{'p50':>8}{'p95':>8}{'max':>9}{'budget':>9}  verdict")
    breached = []
    for sample in samples:
        budget = BUDGETS_MS.get(sample.name)
        verdict = "—" if budget is None else ("PASS" if sample.p95 <= budget else "BREACH")
        if verdict == "BREACH":
            breached.append(sample.name)
        print(f"{sample.name:<34}{sample.p50:>8.0f}{sample.p95:>8.0f}{sample.worst:>9.0f}"
              f"{budget if budget is not None else '-':>9}  {verdict}"
              + (f"   ({sample.note})" if sample.note else ""))
    for line in results:
        print(f"  {line}")
    if breached:
        print(f"\nRESULT: FAIL — p95 over budget: {', '.join(breached)}")
        return 1
    if results:
        print("\nRESULT: FAIL — correctness assertions above")
        return 1
    print("\nRESULT: PASS — every measured p95 is inside its budget")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="http://127.0.0.1:8010/api/v1")
    ap.add_argument("--dsn", default="postgresql://capacity:capacity@127.0.0.1:5544/capacity_perf")
    ap.add_argument("--samples", type=int, default=SAMPLES)
    args = ap.parse_args()
    globals()["SAMPLES"] = args.samples

    api = Api(args.base)
    health = httpx.get(args.base.rsplit("/api", 1)[0] + "/health/live", timeout=30.0)
    if health.status_code != 200:
        raise SystemExit(f"backend /health/live -> {health.status_code}; is the perf API running?")
    print(f"[perf-bench] {args.base} against {urlparse(args.dsn).path.lstrip('/')} "
          f"({SAMPLES} samples per measurement)")
    api.login("customer", CUSTOMER_EMAIL)
    api.login("provider", PROVIDER_EMAIL)
    api.login("admin", ADMIN_EMAIL)

    results: list[str] = []
    availability = pick_availability_target(args.dsn)
    contention = pick_contention_target(args.dsn)
    print(f"  availability target: definition={availability['definition_id'][:8]} "
          f"({availability['bookings']} bookings)")
    print(f"  contention target: definition={contention['definition_id'][:8]} "
          f"capacity={contention['capacity']} free={contention['free']} "
          f"window={contention['start']:%a %d %b %H:%M}Z")

    samples = []
    samples += phase_search(api, args.dsn)
    samples += phase_availability(api, args.dsn, availability)
    samples += phase_contention(api, args.dsn, contention, results)
    samples += phase_dashboards(api, args.dsn)
    return report(samples, results)


if __name__ == "__main__":
    sys.exit(main())
