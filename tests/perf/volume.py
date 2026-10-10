"""Volume dataset for the performance layer (docs/TESTING_STRATEGY.md, layer 5).

The four measurements the strategy names — marketplace search, availability queries, booking
confirmation under contention, dashboard aggregation — are worthless on the demo seed: it has a
few dozen offers, so every plan is a scan of a page and every aggregate is trivial. This script
loads a dataset big enough that the *shape* of the queries decides the answer, and writes it with
`COPY` because the layer under test is the read path, not the write path.

What it builds at `--scale 1.0`:

| Table | Rows | Shape |
| --- | --- | --- |
| organizations | 400 | one deliberately heavy provider (24 resources, 48 definitions) |
| capacity_definitions | ~2,500 | 2 per resource, quantity 4..14 |
| recurring_availabilities | ~15,000 | Mon..Sat 09:00-21:00, one row per definition per weekday |
| offers | ~2,500 | published, 12 category keys, 8 cities, 1_500..90_000 cents |
| bookings | 40,000 | -120..+60 days, capacity-accurate per definition and hour |
| orders / payments / commissions | ~34,000 | one per priced booking |
| fulfillments / reviews / disputes | ~20,000 / ~7,000 / ~200 | hung off the bookings that earned them |

Honesty rules the generator keeps, because a fixture that lies makes every number after it a lie:

- **Capacity is never exceeded.** Each booking checks the committed units already on that
  definition-hour, so `committed_quantity` sums rows the shipped booking path could have written.
- **The real schema validates the load.** Rows go through `COPY` into the migrated database, so
  an enum or CHECK violation aborts the load instead of being skipped.
- **One argon2 hash shared by every fixture account.** Hashing 3,400 passwords at
  memory_cost=65536 would exhaust this machine; the bench still logs in through the shipped
  verifier, so auth stays on the measured path. The database is a perf database and nothing else.

    python tests/perf/volume.py --database capacity_perf --scale 1.0
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.eventloop import use_selector_event_loop  # noqa: E402

use_selector_event_loop()

DEV_HOST, DEV_PORT = "127.0.0.1", 5544
PERF_ROLE = os.environ.get("PERF_DB_USER", "capacity")
PERF_PASSWORD = os.environ.get("PERF_DB_PASSWORD", "capacity")

#: Login for the three fixture identities the bench authenticates as.
PERF_PLAIN_PASSWORD = "Perf1234!"
PROVIDER_EMAIL = "perf-provider@perf.test"
CUSTOMER_EMAIL = "perf-customer@perf.test"
ADMIN_EMAIL = "perf-admin@perf.test"

NOW = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
TENANT_ID = uuid.UUID(int=0xF1E1)

CITIES = {
    "Tehran": (35.70000, 51.40000),
    "Mashhad": (36.30000, 59.60000),
    "Isfahan": (32.65000, 51.67000),
    "Shiraz": (29.60000, 52.53000),
    "Tabriz": (38.08000, 46.29000),
    "Karaj": (35.83000, 50.99000),
    "Ahvaz": (31.32000, 48.67000),
    "Rasht": (37.28000, 49.58000),
}
NOUNS = ["studio", "hall", "bay", "suite", "deck", "yard", "loft", "room"]
ADJECTIVES = ["quiet", "central", "bright", "loaded", "covered", "shared", "private", "corner"]

#: The provider the dashboard benchmark reads. A utilization figure over three definitions says
#: nothing; this org owns enough published capacity that the supply expansion dominates the call.
HEAVY_ORG_INDEX = 0
HEAVY_RESOURCES = 24
HEAVY_BOOKING_SHARE = 0.06

SUPPLY_TABLES = ("tenants", "capacity_categories", "users", "user_roles", "organizations",
                 "org_staff", "capacity_resources", "capacity_definitions",
                 "recurring_availabilities", "offers")
DEMAND_TABLES = ("bookings", "booking_status_events", "orders", "payments", "commissions",
                 "fulfillments", "reviews", "disputes")


def sid(*parts: object) -> uuid.UUID:
    """Stable uuid5, so a re-run rebuilds the same graph and every FK lines up by construction."""
    return uuid.uuid5(uuid.NAMESPACE_DNS, "capacity-perf:" + "|".join(str(p) for p in parts))


def ts(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00")


def sync_dsn(database: str) -> str:
    return f"postgresql://{PERF_ROLE}:{PERF_PASSWORD}@{DEV_HOST}:{DEV_PORT}/{database}"


def ensure_database(database: str) -> None:
    """Rebuild the perf database from empty: a measured dataset must never be an accreted one."""
    import psycopg

    with psycopg.connect(sync_dsn("postgres"), autocommit=True) as conn:
        conn.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                     "WHERE datname = %s AND pid <> pg_backend_pid()", (database,))
        conn.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{database}" OWNER {PERF_ROLE}')
    with psycopg.connect(sync_dsn(database), autocommit=True) as conn:
        conn.execute("GRANT CREATE, USAGE ON SCHEMA public TO PUBLIC")


def migrate(database: str) -> None:
    """`alembic upgrade head` in a subprocess — the same path dev_migrate.sh uses."""
    env = {**os.environ,
           "DATABASE_URL": f"postgresql+psycopg://{PERF_ROLE}:{PERF_PASSWORD}"
                           f"@{DEV_HOST}:{DEV_PORT}/{database}",
           "APP_ENV": "dev"}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                   cwd=str(BACKEND_ROOT), env=env, check=True)


def copy_rows(cur, table: str, rows: list[dict]) -> None:
    """COPY the dicts in key order; a bad enum or CHECK aborts the load loudly."""
    if not rows:
        return
    columns = list(rows[0])
    with cur.copy(f"COPY {table} ({', '.join(columns)}) FROM STDIN") as cp:
        for row in rows:
            cp.write_row(tuple(row[c] for c in columns))


# --------------------------------------------------------------------------- supply


def build_supply(rng: random.Random, scale: float) -> dict[str, list[dict]]:
    """Every read model the bench needs: one tenant, 400 providers, ~2,500 published offers."""
    from app.services.catalog import CATEGORIES

    org_count = max(8, int(400 * scale))
    customer_count = max(50, int(3000 * scale))
    city_names = list(CITIES)

    out: dict[str, list[dict]] = {
        "tenants": [{"id": str(TENANT_ID), "key": "perf", "name": "Performance fixture tenant",
                     "status": "active", "settings": "{}", "created_at": ts(NOW),
                     "updated_at": ts(NOW)}],
        "capacity_categories": [
            {"id": str(sid("category", c["key"])), "key": c["key"], "label": c["label"],
             "parent_id": None, "is_active": "true",
             "attributes_schema": json.dumps(c.get("attributes_schema") or {}),
             "created_at": ts(NOW), "updated_at": ts(NOW)} for c in CATEGORIES],
        "users": [], "user_roles": [], "organizations": [], "org_staff": [],
        "capacity_resources": [], "capacity_definitions": [], "recurring_availabilities": [],
        "offers": [],
    }
    category_ids = [c["id"] for c in out["capacity_categories"]]

    def add_user(user_id: uuid.UUID, email: str, name: str, roles_for: list[str]) -> None:
        out["users"].append({
            "id": str(user_id), "tenant_id": str(TENANT_ID), "email": email, "phone": None,
            "password_hash": "", "full_name": name, "preferred_locale": "en",
            "is_active": "true", "last_login_at": None, "created_by": None,
            "created_at": ts(NOW), "updated_at": ts(NOW)})
        for role in roles_for:
            out["user_roles"].append({"user_id": str(user_id), "role": role,
                                      "granted_by": None, "created_at": ts(NOW)})

    add_user(sid("user", "platform-admin"), ADMIN_EMAIL, "Perf Platform Admin",
             ["platform_admin", "support"])
    heavy_owner = sid("user", "heavy-provider")
    add_user(heavy_owner, PROVIDER_EMAIL, "Perf Heavy Provider", ["provider", "org_admin"])

    defs: list[dict] = []
    for org_index in range(org_count):
        org_id = sid("org", org_index)
        heavy = org_index == HEAVY_ORG_INDEX
        owner_id = heavy_owner if heavy else sid("org-owner", org_index)
        if not heavy:
            add_user(owner_id, f"provider{org_index}@perf.test",
                     f"Perf Provider {org_index}", ["provider"])
        city = "Tehran" if heavy else city_names[org_index % len(city_names)]
        resource_count = HEAVY_RESOURCES if heavy else rng.randint(2, 4)
        out["organizations"].append({
            "id": str(org_id), "tenant_id": str(TENANT_ID),
            "name": "Perf Heavy Provider" if heavy else f"Perf Provider {org_index}",
            "slug": "perf-heavy-org" if heavy else f"perf-org-{org_index}",
            "legal_name": None, "country": "IR", "timezone": "UTC", "currency": "USD",
            "contact_email": f"provider{org_index}@perf.test", "phone": None,
            "address": json.dumps({"line1": f"{org_index} Industry St", "city": city,
                                   "state": "ST", "postal_code": "00000", "country": "IR"}),
            "status": "active", "created_by": str(owner_id),
            "created_at": ts(NOW - timedelta(days=rng.randint(60, 700))), "updated_at": ts(NOW)})
        out["org_staff"].append({
            "id": str(sid("staff", org_index)), "org_id": str(org_id), "user_id": str(owner_id),
            "role": "org_admin", "status": "active", "invited_by": None,
            "created_at": ts(NOW), "updated_at": ts(NOW)})

        for resource_index in range(resource_count):
            resource_id = sid("resource", org_index, resource_index)
            lat, lon = CITIES[city]
            out["capacity_resources"].append({
                "id": str(resource_id), "org_id": str(org_id),
                "category_id": category_ids[(org_index + resource_index) % len(category_ids)],
                "creator_id": str(owner_id),
                "name": f"{ADJECTIVES[(org_index + resource_index) % 8]} "
                        f"{NOUNS[resource_index % 8]} {org_index}-{resource_index}",
                "description": "Bookable capacity created by the performance fixture.",
                "capacity_mode": "scheduled",
                "address": json.dumps({"line1": f"{resource_index} Demo Ave", "city": city,
                                       "state": "ST", "postal_code": "00000", "country": "IR"}),
                "lat": f"{lat + rng.uniform(-0.08, 0.08):.5f}",
                "lon": f"{lon + rng.uniform(-0.08, 0.08):.5f}",
                "timezone": "UTC", "status": "active", "attributes": "{}", "photos": "[]",
                "documents": "[]", "created_at": ts(NOW), "updated_at": ts(NOW)})

            for definition_index in range(2):
                quantity = rng.choice([4, 6, 8, 10, 12, 14])
                definition_id = sid("definition", org_index, resource_index, definition_index)
                out["capacity_definitions"].append({
                    "id": str(definition_id), "resource_id": str(resource_id),
                    "name": f"slot-{definition_index + 1}", "unit_label": "hour",
                    "min_quantity": 1, "max_quantity": quantity, "slot_duration_minutes": 60,
                    "buffer_before_minutes": 0, "buffer_after_minutes": 0, "attributes": "{}",
                    "is_active": "true", "created_at": ts(NOW), "updated_at": ts(NOW)})
                for dow in range(6):
                    out["recurring_availabilities"].append({
                        "id": str(sid("rule", definition_id, dow)),
                        "definition_id": str(definition_id), "dow": dow,
                        "start_time": "09:00:00", "end_time": "21:00:00", "quantity": quantity,
                        "valid_from": None, "valid_until": None, "is_active": "true",
                        "created_at": ts(NOW), "updated_at": ts(NOW)})

                offer_id = sid("offer", org_index, resource_index, definition_index)
                price = rng.randrange(15, 900) * 100
                published = NOW - timedelta(days=rng.randint(1, 200))
                booking_mode = "instant" if (org_index + definition_index) % 5 \
                    else "request_confirm"
                offer_title = (f"{ADJECTIVES[(org_index + definition_index) % 8]} "
                               f"{NOUNS[(resource_index + definition_index) % 8]} "
                               f"for {city.lower()}")
                out["offers"].append({
                    "id": str(offer_id), "definition_id": str(definition_id),
                    "org_id": str(org_id), "resource_id": str(resource_id),
                    "title": offer_title,
                    "description": f"Availability in {city}: {quantity} units per hour, "
                                   f"{price // 100} USD base rate. Listed by the performance "
                                   f"fixture so marketplace search has a corpus to rank.",
                    "pricing_mode": "per_unit_time", "unit_amount_cents": price,
                    "currency": "USD", "min_lead_time_minutes": 0, "max_lead_time_days": 180,
                    "min_duration_minutes": 60, "max_duration_minutes": 240, "min_quantity": 1,
                    "max_quantity": quantity, "booking_mode": booking_mode, "hold_minutes": 15,
                    "cancellation_policy": json.dumps(
                        [{"hours_before": 24, "refund_pct": 100},
                         {"hours_before": 0, "refund_pct": 0}]),
                    "requires_payment": "true", "commission_rate_bp": 1000,
                    "status": "published", "published_at": ts(published),
                    "meta": json.dumps({"city": city}),
                    "created_at": ts(published), "updated_at": ts(published)})
                defs.append({"id": definition_id, "org_id": org_id, "offer_id": offer_id,
                             "title": offer_title, "quantity": quantity, "price": price,
                             "heavy": heavy, "booking_mode": booking_mode})

    customer_ids: list[uuid.UUID] = []
    for index in range(customer_count):
        heavy_customer = index == 0
        user_id = sid("user", "heavy-customer") if heavy_customer else sid("customer", index)
        add_user(user_id, CUSTOMER_EMAIL if heavy_customer
                 else f"perf-customer-{index}@perf.test", f"Perf Customer {index}", ["customer"])
        customer_ids.append(user_id)

    out["_defs"] = defs
    out["_customer_ids"] = customer_ids
    return out


# --------------------------------------------------------------------------- demand


def build_demand(rng: random.Random, supply: dict[str, list[dict]], scale: float
                 ) -> dict[str, list[dict]]:
    """Bookings plus the money, fulfillment, review and dispute rows each one earned."""
    defs = supply["_defs"]
    heavy_defs = [x for x in defs if x["heavy"]]
    customers = supply["_customer_ids"]
    target = max(500, int(40000 * scale))
    committed: dict[tuple[uuid.UUID, date, int], int] = {}

    out: dict[str, list[dict]] = {table: [] for table in DEMAND_TABLES}
    order_number = 100000
    index = 0
    attempts = 0
    while len(out["bookings"]) < target and attempts < target * 40:
        attempts += 1
        # The first slice of the corpus is deliberately concentrated on the heavy provider, so
        # its dashboard aggregates over real traffic instead of over a handful of rows.
        entry = (rng.choice(heavy_defs) if len(out["bookings"]) < target * HEAVY_BOOKING_SHARE
                 else rng.choice(defs))
        day = (NOW + timedelta(days=rng.randint(-120, 60))).date()
        if day.weekday() == 6:
            day += timedelta(days=1)
        hour = rng.randint(9, 19)
        start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc) \
            + timedelta(hours=hour)
        end = start + timedelta(hours=1)
        quantity = rng.randint(1, min(3, entry["quantity"]))
        key = (entry["id"], day, hour)
        used = committed.get(key, 0)
        if used + quantity > entry["quantity"]:
            continue
        committed[key] = used + quantity

        past = end <= NOW
        if past:
            status = rng.choices(["completed", "cancelled", "disputed", "expired"],
                                 weights=[78, 12, 5, 5])[0]
        else:
            status = rng.choices(["confirmed", "hold", "draft", "in_progress", "cancelled"],
                                 weights=[45, 22, 18, 8, 7])[0]
            if entry["booking_mode"] == "instant" and status == "draft":
                status = "confirmed"
        payment = {"hold": "unpaid", "draft": "unpaid", "expired": "unpaid",
                   "cancelled": "refunded", "completed": "paid", "confirmed": "paid",
                   "in_progress": "paid", "disputed": "partially_refunded"}[status]
        total = entry["price"] * quantity
        created = min(NOW - timedelta(minutes=5),
                      start - timedelta(days=rng.randint(1, 30), hours=rng.randint(0, 8)))
        booking_id, buyer = sid("booking", index), rng.choice(customers)
        out["bookings"].append({
            "id": str(booking_id), "offer_id": str(entry["offer_id"]),
            "definition_id": str(entry["id"]), "org_id": str(entry["org_id"]),
            "customer_id": str(buyer), "created_by_user_id": str(buyer),
            "source_match_id": None, "status": status, "payment_status": payment,
            "window_start": ts(start), "window_end": ts(end), "quantity": quantity,
            "unit_amount_cents": entry["price"], "currency": "USD", "total_cents": total,
            "hold_expires_at": ts(created + timedelta(minutes=15)) if status == "hold" else None,
            "request_fingerprint": str(sid("fingerprint", index)),
            "cancel_reason": "customer changed plans" if status == "cancelled" else None,
            "cancelled_at": ts(created + timedelta(hours=2)) if status == "cancelled" else None,
            "cancelled_by": None,
            "confirmed_at": ts(created + timedelta(minutes=2))
            if status not in ("hold", "draft") else None,
            "started_at": ts(start)
            if status in ("in_progress", "completed", "disputed") else None,
            "completed_at": ts(end) if status == "completed" else None,
            "dispute_opened_at": ts(end) if status == "disputed" else None,
            "meta": "{}", "created_at": ts(created), "updated_at": ts(end if past else created)})
        out["booking_status_events"].append({
            "id": str(sid("event", index)), "booking_id": str(booking_id), "from_status": None,
            "to_status": status, "actor_user_id": None, "reason": "fixture", "meta": "{}",
            "created_at": ts(created)})

        if status not in ("hold", "draft", "expired"):
            order_id = sid("order", index)
            commission = total * 1000 // 10000
            refunded = total if status == "cancelled" else (total // 2 if status == "disputed"
                                                            else 0)
            out["orders"].append({
                "id": str(order_id), "number": f"PF{order_number}", "buyer_id": str(buyer),
                "provider_org_id": str(entry["org_id"]), "booking_id": str(booking_id),
                "promotion_id": None, "subtotal_cents": total, "discount_cents": 0,
                "commission_cents": commission, "total_cents": total, "currency": "USD",
                "line_items": json.dumps([{
                    "description": entry["title"], "qty": quantity,
                    "unit_cents": entry["price"], "total_cents": total}]),
                "payment_status": payment,
                "status": {"cancelled": "refunded", "completed": "fulfilled"}.get(status, "paid"),
                "placed_at": ts(created + timedelta(minutes=1)), "cancelled_at": None,
                "refunded_cents": refunded, "created_at": ts(created), "updated_at": ts(created)})
            out["payments"].append({
                "id": str(sid("payment", index)), "order_id": str(order_id),
                "provider_key": "mock", "amount_cents": total, "currency": "USD",
                "status": "canceled" if payment == "refunded" else "succeeded",
                "client_secret": None, "provider_payment_id": f"pi_perf_{index}",
                "failure_reason": None, "confirmed_at": ts(created + timedelta(minutes=1)),
                "meta": "{}", "created_at": ts(created), "updated_at": ts(created)})
            out["commissions"].append({
                "id": str(sid("commission", index)), "order_id": str(order_id),
                "org_id": str(entry["org_id"]), "basis_cents": total, "rate_bp": 1000,
                "amount_cents": commission, "currency": "USD",
                "booked_at": ts(created + timedelta(minutes=1)),
                "created_at": ts(created), "updated_at": ts(created)})
            order_number += 1

        if status in ("in_progress", "completed", "disputed"):
            fulfillment_id = str(sid("fulfillment", index))
            out["fulfillments"].append({
                "id": fulfillment_id, "booking_id": str(booking_id),
                "org_id": str(entry["org_id"]),
                "status": "completed" if status in ("completed", "disputed") else "in_progress",
                "started_at": ts(start),
                "completed_at": ts(end) if status in ("completed", "disputed") else None,
                "notes": "[]", "completed_by": None,
                "created_at": ts(start), "updated_at": ts(end)})
            if status == "completed" and rng.random() < 0.35:
                out["reviews"].append({
                    "id": str(sid("review", index)), "booking_id": str(booking_id),
                    "fulfillment_id": fulfillment_id, "offer_id": str(entry["offer_id"]),
                    "org_id": str(entry["org_id"]), "reviewer_id": str(buyer),
                    "rating": rng.choices([5, 4, 3, 2, 1], weights=[45, 30, 15, 7, 3])[0],
                    "comment": "The session ran as listed.", "provider_reply": None,
                    "replied_at": None, "status": "published",
                    "created_at": ts(end + timedelta(hours=3)),
                    "updated_at": ts(end + timedelta(hours=3))})
        if status == "disputed":
            out["disputes"].append({
                "id": str(sid("dispute", index)), "booking_id": str(booking_id),
                "org_id": str(entry["org_id"]), "complainant_id": str(buyer), "kind": "quality",
                "description": "Fixture dispute for the moderation queue.",
                "status": rng.choice(["open", "under_review"]), "resolution_note": None,
                "resolved_by": None, "resolved_at": None, "refund_cents": total // 2,
                "created_at": ts(end), "updated_at": ts(end)})
        index += 1
    return out


# --------------------------------------------------------------------------- load


def load(database: str, scale: float, seed: int) -> dict:
    import psycopg

    from app.core.security import hash_password

    rng = random.Random(seed)
    ensure_database(database)
    migrate(database)
    supply = build_supply(rng, scale)
    demand = build_demand(rng, supply, scale)

    digest = hash_password(PERF_PLAIN_PASSWORD)
    for row in supply["users"]:
        row["password_hash"] = digest

    with psycopg.connect(sync_dsn(database), autocommit=True) as conn:
        with conn.cursor() as cur:
            for table in SUPPLY_TABLES:
                copy_rows(cur, table, supply[table])
            for table in DEMAND_TABLES:
                copy_rows(cur, table, demand[table])
            cur.execute("ANALYZE")

    counts: dict[str, int] = {}
    with psycopg.connect(sync_dsn(database), autocommit=True) as conn:
        with conn.cursor() as cur:
            for table in ("organizations", "capacity_resources", "capacity_definitions",
                          "recurring_availabilities", "offers", "users", "bookings", "orders",
                          "payments", "fulfillments", "reviews", "disputes"):
                counts[table] = cur.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            size = cur.execute(
                "SELECT pg_size_pretty(pg_database_size(current_database()))").fetchone()[0]
            biggest = cur.execute(
                "SELECT s.relname || ' USING ' || am.amname || ' = ' "
                "|| pg_size_pretty(pg_relation_size(i.indexrelid)) "
                "FROM pg_stat_user_indexes i JOIN pg_index x ON x.indexrelid = i.indexrelid "
                "JOIN pg_class c ON c.oid = i.indexrelid JOIN pg_am am ON am.oid = c.relam "
                "JOIN pg_class s ON s.oid = i.relid "
                "ORDER BY pg_relation_size(i.indexrelid) DESC LIMIT 8").fetchall()
    return {"counts": counts, "size": size, "indexes": biggest}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--database", default="capacity_perf")
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=1337,
                    help="RNG seed: the same seed rebuilds the same corpus, so runs compare")
    args = ap.parse_args()

    summary = load(args.database, args.scale, args.seed)
    print(f"[perf-volume] {args.database} rebuilt at scale={args.scale} seed={args.seed}")
    for table, count in summary["counts"].items():
        print(f"  {table:<24}{count:>9,}")
    print(f"  database size{summary['size']:>19}")
    for row in summary["indexes"]:
        print(f"  index {row[0]}")
    print(f"  fixture login: {PROVIDER_EMAIL} / {CUSTOMER_EMAIL} / {ADMIN_EMAIL} "
          f"password {PERF_PLAIN_PASSWORD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
