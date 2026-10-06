"""seed.py — the CONTRACTS §9 demo dataset, on the same Postgres the API serves (localhost:5544).

What it builds: five provider organizations with real verticals, 15 provider accounts, 50
customers, a platform admin and a support agent, 55 capacity resources and 100 published offers
across all 12 catalog categories, recurring + one-off availability including closed overrides,
100 demands with scored matches, bookings in every status of the §5.5 machine, the money that
moved with them, fulfilments, reviews averaging 4.20, disputes, conversations, notifications and
one live coupon (`WELCOME10`).

How it stays honest: nothing is written beside the shipped invariants — holds, confirmations,
transitions, orders, payments, commissions, refunds, reviews, disputes and matches all go through
`app.services.*`, so seeded rows satisfy the same state machine, the same `unit*quantity==total`
and the same `used_count` bookkeeping the API enforces. The one exception is history: the booking
API refuses a window in the past by design, so past journeys are written as rows and then aged,
while everything hanging off them (order, payment, commission, fulfilment, review) still comes
from the services.

Deterministic: ids are uuid5 of a stable key, so two runs on empty databases agree byte for byte.
Idempotent: catalog rows upsert by their natural key (email, slug, category key, code, definition
+ title) and refresh on every run. Journey rows are relative to the clock — a hold expiring in
two minutes is only true for two minutes — so they are written by the run that first creates them
and left alone afterwards; rebuilding them against the current time is `--reset`.

    python backend/scripts/seed.py             # upsert the catalog, keep existing journeys
    python backend/scripts/seed.py --reset     # wipe the public schema, rebuild everything now
    python backend/scripts/seed.py --verify     # then read the result back over the real routes
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time as clock
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select, text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core.config import Settings, get_settings_cached  # noqa: E402
from app.core.db import build_engine, build_sessionmaker  # noqa: E402
from app.core.errors import (  # noqa: E402
    CapacityExceeded,
    Conflict,
    InvalidStateTransition,
    NoAvailability,
    ValidationFailed,
)
from app.core.eventloop import use_selector_event_loop  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.models import (  # noqa: E402
    AvailabilityOverride,
    Base,
    Booking,
    BookingStatusEvent,
    CapacityCategory,
    CapacityDefinition,
    CapacityDefinitionException,
    CapacityResource,
    Commission,
    Conversation,
    Demand,
    Dispute,
    Fulfillment,
    Match,
    Notification,
    Offer,
    Order,
    OrgStaff,
    Organization,
    Payment,
    Promotion,
    RecurringAvailability,
    Refund,
    Review,
    Tenant,
    User,
    UserRole,
)
from app.services import admin as admin_svc  # noqa: E402
from app.services import booking as booking_svc  # noqa: E402
from app.services import commerce, conversations, disputes  # noqa: E402
from app.services import matching as matching_svc  # noqa: E402
from app.services import notifications, reviews  # noqa: E402
from app.services.catalog import CATEGORIES  # noqa: E402

DEMO_PASSWORD = "Demo1234!"
SEED_TAG = "capacity-seed-v1"
COUPON_CODE = "WELCOME10"
HISTORY_DAYS = 26
DEMAND_COUNT = 100
REVIEW_COUNT = 30
#: 15x5 + 9x4 + 4x3 + 2 + 1 = 126 over 30 reviews => exactly 4.20, the §9 target average.
RATINGS = [5] * 15 + [4] * 9 + [3] * 4 + [2, 1]

_NS = uuid.uuid5(uuid.NAMESPACE_DNS, f"capacityexchange.demo.{SEED_TAG}")


def sid(*parts: Any) -> uuid.UUID:
    return uuid.uuid5(_NS, "/".join(str(p) for p in parts))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    """The §1 wire form, because a JSONB column cannot hold a datetime."""
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse(value: str) -> dtime:
    return dtime.fromisoformat(value)


# --------------------------------------------------------------------------- catalog design
# One entry per category: how the resource reads, how its capacity is counted, the hours it is
# open, the booking windows that provably fit inside them, and what a buyer pays.
STYLE: dict[str, dict[str, Any]] = {
    "warehouse": {
        "word": "Distribution Centre", "units": ("Dock Bay", "Racked Pallet Area"),
        "mode": "quantity", "unit_label": "pallet position", "ceiling": (24, 40), "slot": None,
        "hours": ((dtime(6, 0), dtime(22, 0)),),
        "windows": (("07:00", "10:00"), ("18:00", "21:00")),
        "pricing": "per_quantity", "price": (1200, 1800), "lead": 120, "horizon": 90,
        "attrs": {"pallet_positions": 40, "square_meters": 900, "loading_dock": True,
                  "temperature_controlled": False},
        "blurb": "Floor-loaded racking with a level dock apron and a forklift on site.",
        "demand": "Looking for {quantity} pallet positions for a stock overflow, fork truck "
                  "access needed on the day.",
    },
    "storage_space": {
        "word": "Storage Yard", "units": ("Pallet Rack Block", "Climate Locker"),
        "mode": "quantity", "unit_label": "storage block", "ceiling": (8, 12), "slot": None,
        "hours": ((dtime(6, 0), dtime(22, 0)),),
        "windows": (("08:00", "12:00"), ("13:00", "17:00")),
        "pricing": "per_quantity", "price": (2500, 3200), "lead": 60, "horizon": 60,
        "attrs": {"square_meters": 120, "shelved": True, "access_hours": "06:00-22:00"},
        "blurb": "Shelved, lockable blocks with trolley access from the yard.",
        "demand": "Need secure storage for {quantity} pallets of packaging, access during "
                  "working hours.",
    },
    "truck_return": {
        "word": "Return Depot", "units": ("Truck Return Window", "Trailer Yard Slot"),
        "mode": "scheduled", "unit_label": "return window", "ceiling": (2, 3), "slot": 120,
        "hours": ((dtime(6, 0), dtime(22, 0)),),
        "windows": (("07:00", "10:00"), ("18:00", "21:00")),
        "pricing": "per_unit_time", "price": (3000, 3600), "lead": 240, "horizon": 45,
        "attrs": {"route": "regional", "capacity_cbm": 34, "max_weight_kg": 12000,
                  "refrigerated": False},
        "blurb": "Booked return slot with a marshaller, so a vehicle turns around in one visit.",
        "demand": "Returning {quantity} trailers on a regional loop and need a dock slot with "
                  "a marshaller.",
    },
    "parking": {
        "word": "Vehicle Park", "units": ("Truck Yard Bay", "Visitor Space Block"),
        "mode": "quantity", "unit_label": "bay", "ceiling": (10, 20), "slot": None,
        "hours": ((dtime(0, 0), dtime(23, 59)),),
        "windows": (("08:00", "12:00"), ("13:00", "17:00")),
        "pricing": "per_quantity", "price": (900, 1400), "lead": 0, "horizon": 90,
        "attrs": {"vehicle_height_m": 4.2, "covered": False, "ev_charging": True},
        "blurb": "Hard-standing bays with barrier access and charging on the front row.",
        "demand": "Need {quantity} bays for a driver rest break, one of them with charging.",
    },
    "transport_vehicle": {
        "word": "Fleet Depot", "units": ("Refrigerated Van", "Curtain-side Truck"),
        "mode": "quantity", "unit_label": "vehicle-day", "ceiling": (2, 3), "slot": None,
        "hours": ((dtime(6, 0), dtime(22, 0)),),
        "windows": (("07:00", "15:00"), ("09:00", "17:00")),
        "pricing": "per_quantity", "price": (38000, 52000), "lead": 480, "horizon": 60,
        "attrs": {"vehicle_type": "3.5t box", "payload_kg": 1400, "driver_included": True},
        "blurb": "Insured vehicle with a driver for the day, fuel card included.",
        "demand": "Need {quantity} vehicles with a driver for a same-day distribution run.",
    },
    "equipment": {
        "word": "Plant and Tools", "units": ("Machine Hour Bank", "Tool Kit Set"),
        "mode": "quantity", "unit_label": "unit-day", "ceiling": (3, 6), "slot": None,
        "hours": ((dtime(7, 0), dtime(19, 0)),),
        "windows": (("08:00", "16:00"), ("09:00", "12:00")),
        "pricing": "per_quantity", "price": (15000, 21000), "lead": 240, "horizon": 45,
        "attrs": {"model": "class 2", "power_kw": 22, "operator_included": False},
        "blurb": "Serviced plant with certificates, collected from the yard gate.",
        "demand": "Hiring {quantity} units for a week of maintenance work, certificates "
                  "required.",
    },
    "production_slot": {
        "word": "Production Line", "units": ("CNC Cell", "Assembly Line"),
        "mode": "scheduled", "unit_label": "shift", "ceiling": (1, 2), "slot": 480,
        "hours": ((dtime(7, 0), dtime(19, 0)),),
        "windows": (("08:00", "16:00"), ("10:00", "18:00")),
        "pricing": "per_unit_time", "price": (25000, 31000), "lead": 1440, "horizon": 60,
        "attrs": {"machine": "5-axis cell", "tolerance_mm": 0.05, "min_batch_units": 40},
        "blurb": "Machine time with a setter on the line and first-article inspection.",
        "demand": "Need {quantity} production shifts machined to a tight tolerance, batch of "
                  "sixty.",
    },
    "meeting_room": {
        "word": "Meeting Floor", "units": ("Boardroom", "Workshop Hall"),
        "mode": "scheduled", "unit_label": "hour", "ceiling": (1, 2), "slot": 60,
        "hours": ((dtime(8, 0), dtime(18, 0)),),
        "windows": (("09:00", "12:00"), ("14:00", "16:00")),
        "pricing": "per_unit_time", "price": (4500, 6500), "lead": 60, "horizon": 90,
        "attrs": {"capacity_persons": 12, "has_projector": True, "has_whiteboard": True,
                  "floor": "2"},
        "blurb": "Daylight room with a screen, whiteboard and a coffee station outside.",
        "demand": "Room for {quantity} hours of board meeting with a projector and a whiteboard.",
    },
    "workstation": {
        "word": "Workstation Loft", "units": ("Desk Bank", "Studio Desk"),
        "mode": "quantity", "unit_label": "desk-day", "ceiling": (6, 10), "slot": None,
        "hours": ((dtime(7, 0), dtime(21, 0)),),
        "windows": (("08:00", "16:00"), ("09:00", "17:00")),
        "pricing": "per_quantity", "price": (3500, 4800), "lead": 60, "horizon": 90,
        "attrs": {"desk_type": "sit-stand", "monitors": 2, "network_gbps": 1},
        "blurb": "Wired desk with a monitor arm, meeting booths a floor up.",
        "demand": "Looking for {quantity} desks for a visiting team for a week, wired network "
                  "required.",
    },
    "appointment_slot": {
        "word": "Clinic Rooms", "units": ("Consultation Slot", "Treatment Chair"),
        "mode": "scheduled", "unit_label": "slot", "ceiling": (1, 2), "slot": 30,
        "hours": ((dtime(9, 0), dtime(13, 0)), (dtime(14, 0), dtime(18, 0))),
        "windows": (("09:30", "10:30"), ("15:00", "16:00")),
        "pricing": "per_unit_time", "price": (6000, 7500), "lead": 120, "horizon": 30,
        "attrs": {"practitioner": "on site", "service": "assessment", "room": "quiet"},
        "blurb": "Half-hour slot with a practitioner, notes written up straight after.",
        "demand": "Need {quantity} assessment slots this fortnight, quiet room preferred.",
    },
    "commercial_kitchen": {
        "word": "Kitchen Facility", "units": ("Hot Line Station", "Prep and Baking Station"),
        "mode": "scheduled", "unit_label": "line hour", "ceiling": (2, 3), "slot": 240,
        "hours": ((dtime(6, 0), dtime(20, 0)),),
        "windows": (("06:30", "10:30"), ("15:00", "19:00")),
        "pricing": "per_unit_time", "price": (12000, 16000), "lead": 480, "horizon": 45,
        "attrs": {"burners": 6, "oven_count": 2, "licensed_for_delivery": True,
                  "square_meters": 80},
        "blurb": "Licensed production kitchen with extraction, washing up and a walk-in.",
        "demand": "Need {quantity} line hours in a licensed kitchen for a catering batch.",
    },
    "hospitality_room": {
        "word": "Guest House", "units": ("Standard Room Block", "Suite Night"),
        "mode": "quantity", "unit_label": "room-day", "ceiling": (4, 6), "slot": None,
        "hours": ((dtime(8, 0), dtime(20, 0)),),
        "windows": (("09:00", "17:00"), ("11:00", "19:00")),
        "pricing": "per_quantity", "price": (9000, 14000), "lead": 240, "horizon": 120,
        "attrs": {"capacity_persons": 2, "catering_available": True,
                  "wheelchair_accessible": True},
        "blurb": "Quiet rooms over the courtyard, breakfast in the ground-floor dining room.",
        "demand": "Need {quantity} rooms for a crew between shifts, breakfast included.",
    },
}

#: One entry per organization: its vertical, where it is, who works there and which eleven of the
#: twelve categories it actually lists (so every category is live in at least three organizations).
ORG_SPECS: tuple[dict[str, Any], ...] = (
    {
        "slug": "northway-logistics", "name": "Northway Logistics",
        "legal_name": "Northway Logistics B.V.", "short": "Northway",
        "city": "Rotterdam", "country": "NL", "lat": 51.9244, "lon": 4.4777,
        "owner": ("Marleen de Vries", "+31105501001"),
        "staff": (("Ruud Bakker", "+31105501002"), ("Sanne Visser", "+31105501003")),
        "categories": ("warehouse", "storage_space", "truck_return", "parking",
                       "transport_vehicle", "equipment", "production_slot", "meeting_room",
                       "workstation", "appointment_slot", "commercial_kitchen"),
    },
    {
        "slug": "cityspace-rooms", "name": "CitySpace Rooms",
        "legal_name": "CitySpace Rooms GmbH", "short": "CitySpace",
        "city": "Berlin", "country": "DE", "lat": 52.5200, "lon": 13.4050,
        "owner": ("Jonas Weber", "+49305501001"),
        "staff": (("Inke Sorensen", "+49305501002"), ("Tobias Klein", "+49305501003")),
        "categories": ("meeting_room", "hospitality_room", "workstation", "appointment_slot",
                       "commercial_kitchen", "storage_space", "parking", "equipment",
                       "transport_vehicle", "warehouse", "production_slot"),
    },
    {
        "slug": "precision-fab", "name": "Precision Fabrication",
        "legal_name": "Precision Fab S.A.R.L.", "short": "Precision",
        "city": "Lyon", "country": "FR", "lat": 45.7640, "lon": 4.8357,
        "owner": ("Camille Ferrand", "+33475501001"),
        "staff": (("Yanis Bouchard", "+33475501002"), ("Ella Moreau", "+33475501003")),
        "categories": ("production_slot", "equipment", "warehouse", "storage_space",
                       "truck_return", "transport_vehicle", "meeting_room", "workstation",
                       "appointment_slot", "parking", "commercial_kitchen"),
    },
    {
        "slug": "freshbite-kitchens", "name": "FreshBite Kitchens",
        "legal_name": "FreshBite Catering S.L.", "short": "FreshBite",
        "city": "Barcelona", "country": "ES", "lat": 41.3874, "lon": 2.1686,
        "owner": ("Nuria Serrat", "+34935501001"),
        "staff": (("Marc Oliver", "+34935501002"), ("Aitana Ruiz", "+34935501003")),
        "categories": ("commercial_kitchen", "storage_space", "warehouse", "appointment_slot",
                       "meeting_room", "hospitality_room", "workstation", "parking", "equipment",
                       "transport_vehicle", "production_slot"),
    },
    {
        "slug": "transitline-fleet", "name": "TransitLine Fleet",
        "legal_name": "TransitLine Fleet Ltd", "short": "TransitLine",
        "city": "Manchester", "country": "GB", "lat": 53.4808, "lon": -2.2426,
        "owner": ("Hassan Iqbal", "+441615501001"),
        "staff": (("Gemma Whitfield", "+441615501002"), ("Peter Nowak", "+441615501003")),
        "categories": ("truck_return", "transport_vehicle", "parking", "warehouse", "equipment",
                       "workstation", "meeting_room", "storage_space", "appointment_slot",
                       "commercial_kitchen", "hospitality_room"),
    },
)

CUSTOMER_FIRST = ("Alice", "Bruno", "Chiara", "David", "Elena", "Farid", "Greta", "Hugo",
                 "Ines", "Jonas")
CUSTOMER_LAST = ("Marchetti", "Novak", "Okafor", "Persson", "Quintero", "Rossi", "Schneider",
                 "Toumi", "Urbanski", "Vega")

STAFF_ROLES = ("manager", "staff")

COMMENTS = {
    5: ("Exactly as listed, the handover took ten minutes and the paperwork was ready.",
        "Nothing to improve on: the slot started on time and the kit was clean."),
    4: ("Good value and easy to find; the signage at the gate could be clearer.",
        "Solid throughout, only the loading bay was busier than expected."),
    3: ("Fine for the price, though we had to wait a few minutes for access.",
        "Workable, but the room was smaller than the listing photo suggests."),
    2: ("The equipment was due a service and someone should flag that before listing.",
        "Two of us turned up and the unit was not the one in the photographs."),
    1: ("Access never happened and nobody answered the phone, so the day was lost.",),
}

REPLIES = (
    "Thank you - the gate signage has since been repainted and the access code is sent earlier.",
    "Noted with thanks; we now send a marshaller to meet every returning vehicle.",
    "Appreciate the honesty. The unit went straight back into the workshop and is serviced.",
)

DEMAND_EXPIRY_DAYS = (4, 6, 9, 12)


# --------------------------------------------------------------------------- harness
def seed_meta(**extra: Any) -> dict[str, Any]:
    """Provenance on a row: the tag marks it as journey data, the rest keeps the layout readable."""
    return {"seed": SEED_TAG, **extra}


async def _one(session: AsyncSession, model, **stable) -> Any | None:
    return (await session.execute(select(model).filter_by(**stable))).scalar_one_or_none()


async def _upsert(session: AsyncSession, model, *, stable: dict, values: dict,
                  seed_id: uuid.UUID) -> tuple[Any, bool]:
    """Insert under a deterministic id, or refresh the row that already owns the stable key.

    The stable columns are part of the row, not only the lookup: a natural key like `tenants.key`
    has nowhere else to come from on the insert.
    """
    row = await _one(session, model, **stable)
    if row is None:
        row = model(id=seed_id, **{**stable, **values})
        session.add(row)
        created = True
    else:
        for key, value in values.items():
            setattr(row, key, value)
        created = False
    await session.flush()
    return row, created


async def _ensure_role(session: AsyncSession, user_id: uuid.UUID, role: str,
                       granted_by: uuid.UUID | None) -> None:
    existing = (await session.execute(
        select(UserRole).where(UserRole.user_id == user_id, UserRole.role == role)
    )).scalar_one_or_none()
    if existing is None:
        session.add(UserRole(user_id=user_id, role=role, granted_by=granted_by))
        await session.flush()


def _open_day(base: date, *, back: bool = False) -> date:
    """The seeded rules run Monday..Saturday, so Sunday always moves to a neighbour."""
    day = base
    step = timedelta(days=-1 if back else 1)
    while day.weekday() == 6:
        day += step
    return day


def _next_weekday(base: date, weekday: int) -> date:
    """The first date at or after `base` falling on the ISO-ish `weekday` (0 = Monday)."""
    return base + timedelta(days=(weekday - base.weekday()) % 7)


def _window(style: dict, day: date, index: int) -> tuple[datetime, datetime]:
    start, end = style["windows"][index % len(style["windows"])]
    return (datetime.combine(day, parse(start), tzinfo=timezone.utc),
            datetime.combine(day, parse(end), tzinfo=timezone.utc))


def _parts(offer: Offer) -> tuple[dict, CapacityDefinition, CapacityResource]:
    style = STYLE[offer.meta["category_key"]]
    return style, offer._definition, offer._resource  # type: ignore[attr-defined]


@dataclass
class OrgCtx:
    spec: dict
    org: Organization
    owner: User
    staff: tuple[User, ...]
    resources: list[CapacityResource] = field(default_factory=list)
    offers: list[Offer] = field(default_factory=list)

    def person(self, index: int) -> User:
        return self.owner if index % 2 == 0 else self.staff[index % len(self.staff)]


@dataclass
class Ctx:
    settings: Settings
    tenant: Tenant
    admin: User
    support: User
    customers: list[User]
    orgs: list[OrgCtx]
    offers: list[Offer] = field(default_factory=list)
    by_category: dict[str, list[Offer]] = field(default_factory=dict)
    refusals: list[dict] = field(default_factory=list)
    #: Journeys need offers with known shapes; these are carved out of `offers` by `_classify`.
    instant: list[Offer] = field(default_factory=list)
    request_confirm: list[Offer] = field(default_factory=list)
    free: list[Offer] = field(default_factory=list)
    short_hold: list[Offer] = field(default_factory=list)
    #: Quantity-counted instant listings: no minimum duration, so a window can be pinned to the
    #: clock (a refund band is graded by hours-before-start, not by a tidy morning slot).
    clock_friendly: list[Offer] = field(default_factory=list)
    #: Scheduled listings with a one-unit ceiling — the only shape that can prove the slot is
    #: exclusive, which is what §9 asks the dataset to demonstrate.
    single_slot: list[Offer] = field(default_factory=list)
    #: Past journeys that ran to completion, so a dispute can be opened on delivered work.
    completed: list["Trip"] = field(default_factory=list)
    #: The live phases keep their trips: a dispute, a thread or a cancellation is opened on a
    #: booking another phase created, and the phases run in a fixed order for exactly that reason.
    confirmed: list["Trip"] = field(default_factory=list)
    held: list["Trip"] = field(default_factory=list)
    requests: list["Trip"] = field(default_factory=list)
    running: list["Trip"] = field(default_factory=list)
    #: Offers already given a live window: the clock-pinned phases must not stack two journeys on
    #: one definition, or the capacity they report would be a lie.
    live_used: set[uuid.UUID] = field(default_factory=set)
    #: Cancellation and dispute money, kept so the summary can print what was returned.
    refunds: list[dict[str, Any]] = field(default_factory=list)

    def org(self, org_id: uuid.UUID) -> OrgCtx:
        for ctx in self.orgs:
            if ctx.org.id == org_id:
                return ctx
        raise LookupError(f"no seeded organization {org_id}")

    def pick(self, index: int) -> Offer:
        """Deterministic spread over the catalog: stride 7 stays coprime with 100 offers."""
        return self.offers[(index * 7) % len(self.offers)]


def _classify(ctx: Ctx) -> None:
    """Sort the catalog into the shapes the journeys need, and prove the minimums are met."""
    ctx.instant = [o for o in ctx.offers if o.booking_mode == "instant"]
    ctx.request_confirm = [o for o in ctx.offers if o.booking_mode == "request_confirm"]
    # A free listing is only useful to a journey that books it through the API, and a booking is
    # only bookable when it is instant.
    ctx.free = [o for o in ctx.instant if not o.requires_payment]
    ctx.short_hold = [o for o in ctx.instant if o.hold_minutes == 5]
    ctx.clock_friendly = [o for o in ctx.instant
                          if STYLE[o.meta["category_key"]]["mode"] == "quantity"]
    ctx.single_slot = [o for o in ctx.instant
                       if STYLE[o.meta["category_key"]]["mode"] == "scheduled"
                       and o._definition.max_quantity == 1]  # type: ignore[attr-defined]
    missing = [name for name, rows in (("instant listing", ctx.instant),
                                       ("request_confirm listing", ctx.request_confirm),
                                       ("free listing", ctx.free),
                                       ("five-minute hold", ctx.short_hold),
                                       ("clock-friendly listing", ctx.clock_friendly),
                                       ("one-slot listing", ctx.single_slot))
               if len(rows) < 2]
    if missing:
        raise RuntimeError("catalog does not produce the offer shapes the journeys need: "
                           + ", ".join(missing))


def _attach_offer_graph(session_rows: list[tuple[Offer, CapacityDefinition, CapacityResource]],
                        ctx_by_org: dict[uuid.UUID, OrgCtx]) -> list[Offer]:
    offers = []
    for offer, definition, resource in sorted(session_rows, key=lambda r: str(r[0].id)):
        offer._definition, offer._resource = definition, resource  # type: ignore[attr-defined]
        ctx_by_org[offer.org_id].offers.append(offer)
        ctx_by_org[offer.org_id].resources.append(resource)
        offers.append(offer)
    return offers


# --------------------------------------------------------------------------- catalog phases
async def _seed_tenant(session: AsyncSession) -> Tenant:
    tenant, _ = await _upsert(
        session, Tenant, stable={"key": "default"},
        values={"name": "Capacity Exchange (default tenant)", "status": "active",
                "settings": {"markets": ["NL", "DE", "FR", "ES", "GB"]}},
        seed_id=sid("tenant", "default"))
    return tenant


async def _seed_categories(session: AsyncSession) -> int:
    """Reference data keyed by `key`: admins may have renamed a label, the key is the contract."""
    for entry in CATEGORIES:
        await _upsert(
            session, CapacityCategory, stable={"key": entry["key"]},
            values={"label": entry["label"], "is_active": True,
                    "attributes_schema": entry["attributes_schema"]},
            seed_id=sid("category", entry["key"]))
    return len(CATEGORIES)


async def _seed_platform_users(session: AsyncSession, tenant: Tenant,
                               password_hash: str) -> tuple[User, User]:
    rows = []
    for email, name, phone, role in (
        ("admin@capacityexchange.test", "Ada Kuipers", "+31205500001", "platform_admin"),
        ("support@capacityexchange.test", "Samil Rahimi", "+31205500002", "support"),
    ):
        user, _ = await _upsert(
            session, User, stable={"email": email},
            values={"tenant_id": tenant.id, "email": email, "phone": phone,
                    "password_hash": password_hash, "full_name": name,
                    "preferred_locale": "en", "is_active": True},
            seed_id=sid("user", email))
        await _ensure_role(session, user.id, role, granted_by=user.id)
        await _ensure_role(session, user.id, "customer", granted_by=user.id)
        rows.append(user)
    return rows[0], rows[1]


async def _seed_orgs(session: AsyncSession, tenant: Tenant,
                     password_hash: str) -> list[OrgCtx]:
    created: list[OrgCtx] = []
    for spec in ORG_SPECS:
        owner_email, staff_emails = f"owner@{spec['slug']}.test", f"staff@{spec['slug']}.test"
        second_email = f"staff2@{spec['slug']}.test"
        org, _ = await _upsert(
            session, Organization, stable={"slug": spec["slug"]},
            values={
                "tenant_id": tenant.id, "name": spec["name"], "slug": spec["slug"],
                "legal_name": spec["legal_name"], "country": spec["country"],
                "timezone": "UTC", "currency": "USD", "contact_email": owner_email,
                "phone": spec["owner"][1],
                "address": {"line1": f"{spec['short']} House", "city": spec["city"],
                            "country": spec["country"]},
                "status": "active",
            },
            seed_id=sid("org", spec["slug"]))

        people = [(spec["owner"][0], owner_email, spec["owner"][1], "org_admin"),
                  (spec["staff"][0][0], staff_emails, spec["staff"][0][1], "manager"),
                  (spec["staff"][1][0], second_email, spec["staff"][1][1], "staff")]
        users: list[User] = []
        for full_name, email, phone, staff_role in people:
            user, _ = await _upsert(
                session, User, stable={"email": email},
                values={"tenant_id": tenant.id, "email": email, "phone": phone,
                        "password_hash": password_hash, "full_name": full_name,
                        "preferred_locale": "en", "is_active": True},
                seed_id=sid("user", email))
            await _ensure_role(session, user.id, "provider", granted_by=user.id)
            await _ensure_role(session, user.id, "customer", granted_by=user.id)
            if staff_role == "org_admin":
                await _ensure_role(session, user.id, "org_admin", granted_by=user.id)
            await _upsert(
                session, OrgStaff, stable={"org_id": org.id, "user_id": user.id},
                values={"org_id": org.id, "user_id": user.id, "role": staff_role,
                        "status": "active", "invited_by": users[0].id if users else user.id},
                seed_id=sid("staff", org.id, user.id))
            users.append(user)

        # `created_by` is only known once the owner row exists.
        org.created_by = users[0].id
        await session.flush()
        created.append(OrgCtx(spec=spec, org=org, owner=users[0], staff=(users[1], users[2])))
    return created


async def _seed_customers(session: AsyncSession, tenant: Tenant,
                          password_hash: str) -> list[User]:
    locales = ("en", "de", "fr", "es", "nl", "fa")
    rows: list[User] = []
    for index in range(50):
        email = f"customer{index + 1:02d}@example.test"
        name = f"{CUSTOMER_FIRST[index % 10]} {CUSTOMER_LAST[(index // 10 + index % 7) % 10]}"
        user, _ = await _upsert(
            session, User, stable={"email": email},
            values={"tenant_id": tenant.id, "email": email, "phone": f"+31605550{index:03d}",
                    "password_hash": password_hash, "full_name": name,
                    "preferred_locale": locales[index % len(locales)], "is_active": True},
            seed_id=sid("user", email))
        await _ensure_role(session, user.id, "customer", granted_by=user.id)
        rows.append(user)
    return rows


async def _seed_capacity(session: AsyncSession, orgs: list[OrgCtx]) -> None:
    """55 resources, 100 bookable units and 100 published offers, plus the availability behind them.

    Every definition gets its recurring Monday..Saturday rules, and three of them carry the
    one-off cases the console has to render: an extra Sunday opening, a reduced-capacity day and
    a whole-day closure. The closure dates sit weeks away from the seeded journeys on purpose.
    """
    now = utcnow()
    closed_on = _open_day((now + timedelta(days=48)).date())
    reduced_on = _open_day((now + timedelta(days=55)).date())
    sunday_on = _next_weekday((now + timedelta(days=7)).date(), 6)

    counter = 0
    for org_ctx in orgs:
        spec = org_ctx.spec
        for slot, key in enumerate(spec["categories"]):
            style = STYLE[key]
            category = await _one(session, CapacityCategory, key=key)
            zone = chr(ord("A") + slot)
            units = 2 if slot < 9 else 1
            resource, _ = await _upsert(
                session, CapacityResource,
                stable={"org_id": org_ctx.org.id, "name": f"{spec['short']} {style['word']} {zone}"},
                values={
                    "org_id": org_ctx.org.id, "category_id": category.id,
                    "creator_id": org_ctx.owner.id,
                    "name": f"{spec['short']} {style['word']} {zone}",
                    "description": style["blurb"], "capacity_mode": style["mode"],
                    "address": {"line1": f"{zone} Yard, {spec['city']} Logistics Park",
                                "city": spec["city"], "country": spec["country"]},
                    "lat": round(spec["lat"] + 0.012 * slot, 5),
                    "lon": round(spec["lon"] + 0.017 * (slot % 4), 5),
                    "timezone": "UTC", "status": "active",
                    "attributes": {**style["attrs"], "zone": zone},
                    "photos": [], "documents": [],
                },
                seed_id=sid("resource", spec["slug"], key, zone))

            for unit_index in range(units):
                unit_name = style["units"][unit_index]
                ceiling = style["ceiling"][unit_index % len(style["ceiling"])]
                label = f"{unit_name} {zone}{unit_index + 1}"
                definition, _ = await _upsert(
                    session, CapacityDefinition,
                    stable={"resource_id": resource.id, "name": label},
                    values={
                        "resource_id": resource.id, "name": label,
                        "unit_label": style["unit_label"], "min_quantity": 1,
                        "max_quantity": ceiling, "slot_duration_minutes": style["slot"],
                        "buffer_before_minutes": 15 if style["mode"] == "scheduled" else 0,
                        "buffer_after_minutes": 0,
                        "attributes": {"zone": zone, "unit": unit_name},
                        "is_active": True,
                    },
                    seed_id=sid("definition", spec["slug"], key, zone, unit_index))

                for rule_index, (start, end) in enumerate(style["hours"]):
                    await _upsert(
                        session, RecurringAvailability,
                        stable={"definition_id": definition.id, "dow": 0, "start_time": start},
                        values={"definition_id": definition.id, "dow": 0, "start_time": start,
                                "end_time": end, "quantity": ceiling,
                                "valid_from": (now - timedelta(days=45)).date(),
                                "valid_until": (now + timedelta(days=150)).date(),
                                "is_active": True},
                        seed_id=sid("rule", definition.id, 0, rule_index))
                    for dow in range(1, 6):
                        await _upsert(
                            session, RecurringAvailability,
                            stable={"definition_id": definition.id, "dow": dow,
                                    "start_time": start},
                            values={"definition_id": definition.id, "dow": dow,
                                    "start_time": start, "end_time": end, "quantity": ceiling,
                                    "is_active": True},
                            seed_id=sid("rule", definition.id, dow, rule_index))

                counter += 1
                offer_index = counter
                booking_mode = "request_confirm" if offer_index % 3 == 0 else "instant"
                pricing_mode = style["pricing"]
                if offer_index % 23 == 0:
                    pricing_mode = "flat"
                free = offer_index % 17 == 3
                unit_cents = 0 if free else style["price"][unit_index % len(style["price"])]
                mode_copy = ("Requested bookings are confirmed by our team."
                             if booking_mode == "request_confirm"
                             else "Instant booking: the slot is yours on confirm.")
                title = f"{label} - {'guided' if offer_index % 3 == 0 else 'self service'}"
                offer, _ = await _upsert(
                    session, Offer, stable={"definition_id": definition.id, "title": title},
                    values={
                        "definition_id": definition.id, "org_id": org_ctx.org.id,
                        "resource_id": resource.id, "title": title[:120],
                        "description": (f"{spec['name']} lists {label.lower()} in "
                                        f"{spec['city']}. {style['blurb']} {mode_copy}"),
                        "pricing_mode": pricing_mode,
                        # A free listing is a real zero-price offer, not a flag: its order lands
                        # on `not_required` exactly as the API would put it.
                        "unit_amount_cents": unit_cents,
                        "currency": "USD",
                        "min_lead_time_minutes": style["lead"],
                        "max_lead_time_days": style["horizon"],
                        "min_duration_minutes": style["slot"],
                        "max_duration_minutes": (style["slot"] * 4) if style["slot"] else None,
                        "min_quantity": 1 if style["mode"] == "quantity" else None,
                        "max_quantity": ceiling if style["mode"] == "quantity" else None,
                        "booking_mode": booking_mode,
                        # One listing per organization runs a five-minute hold, so the console can
                        # show a reservation that is about to lapse.
                        "hold_minutes": 5 if offer_index % 29 == 4 else 15,
                        "cancellation_policy": (
                            [{"hours_before": 48, "refund_pct": 100},
                             {"hours_before": 24, "refund_pct": 50},
                             {"hours_before": 0, "refund_pct": 0}]
                            if offer_index % 5 == 0 else
                            [{"hours_before": 24, "refund_pct": 100},
                             {"hours_before": 0, "refund_pct": 0}]),
                        "requires_payment": not free,
                        "commission_rate_bp": 800 if offer_index % 11 == 0 else None,
                        "status": "published",
                        "published_at": now - timedelta(days=3 + offer_index % 21,
                                                        hours=offer_index % 9),
                        "meta": seed_meta(category_key=key, unit=unit_name, zone=zone),
                    },
                    seed_id=sid("offer", spec["slug"], key, zone, unit_index))

                if slot == 0 and unit_index == 0:
                    await _upsert(
                        session, AvailabilityOverride,
                        stable={"definition_id": definition.id, "override_date": closed_on,
                                "start_time": None},
                        values={"definition_id": definition.id, "override_date": closed_on,
                                "kind": "closed", "start_time": None, "end_time": None,
                                "quantity": None, "reason": "Annual maintenance shutdown",
                                "is_active": True},
                        seed_id=sid("override", definition.id, "closed"))
                if slot == 1 and unit_index == 0:
                    await _upsert(
                        session, AvailabilityOverride,
                        stable={"definition_id": definition.id, "override_date": reduced_on,
                                "start_time": None},
                        values={"definition_id": definition.id, "override_date": reduced_on,
                                "kind": "reduced", "start_time": None, "end_time": None,
                                "quantity": 1, "reason": "One crew off sick",
                                "is_active": True},
                        seed_id=sid("override", definition.id, "reduced"))
                if slot == 2:
                    await _upsert(
                        session, AvailabilityOverride,
                        stable={"definition_id": definition.id, "override_date": sunday_on,
                                "start_time": style["hours"][0][0]},
                        values={"definition_id": definition.id, "override_date": sunday_on,
                                "kind": "extra",
                                # The published opening time is stored as a `time`, the extra
                                # window's bounds are written in the same shape the API reads.
                                "start_time": style["hours"][0][0],
                                "end_time": dtime(12, 0), "quantity": 2,
                                "reason": "Weekend drop-in opening", "is_active": True},
                        seed_id=sid("override", definition.id, "extra"))
                if slot == 3 and unit_index == 1:
                    await _upsert(
                        session, CapacityDefinitionException,
                        stable={"definition_id": definition.id, "effective_from": closed_on},
                        values={"definition_id": definition.id, "effective_from": closed_on,
                                "effective_to": closed_on + timedelta(days=2),
                                "max_quantity_delta": None, "is_closed": True,
                                "reason": "Certification rework"},
                        seed_id=sid("exception", definition.id, "cert"))


async def _load_offers(session: AsyncSession, orgs: list[OrgCtx]) -> tuple[list[Offer], dict[str, list[Offer]]]:
    rows = (await session.execute(
        select(Offer, CapacityDefinition, CapacityResource)
        .join(CapacityDefinition, CapacityDefinition.id == Offer.definition_id)
        .join(CapacityResource, CapacityResource.id == Offer.resource_id)
        .where(Offer.status == "published")
    )).all()
    by_org = {ctx.org.id: ctx for ctx in orgs}
    offers = _attach_offer_graph(rows, by_org)
    by_category: dict[str, list[Offer]] = {}
    for offer in offers:
        by_category.setdefault(offer.meta["category_key"], []).append(offer)
    for ctx in orgs:
        ctx.resources = list({r.id: r for r in ctx.resources}.values())
    return offers, by_category


async def _seed_promotion(session: AsyncSession, ctx: Ctx) -> Promotion:
    """The one active coupon §9 asks for: 10% off, platform-wide, live for a month."""
    now = utcnow()
    existing = await _one(session, Promotion, code=COUPON_CODE)
    if existing is not None:
        return existing
    return await admin_svc.create_promotion(
        session,
        {"name": "Welcome discount", "kind": "coupon", "code": COUPON_CODE,
         "discount_config": {"type": "pct", "bp": 1000}, "min_order_cents": 5000,
         "applies_to": {}, "usage_limit": 500, "per_user_limit": 3,
         "starts_at": now - timedelta(days=20), "ends_at": now + timedelta(days=40),
         "status": "active"},
        actor_user_id=ctx.admin.id)


# --------------------------------------------------------------------------- journey harness
_ORDER_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
HISTORY_JOURNEYS = 40
HISTORY_RATED = 30
HISTORY_CANCELLED = 4
#: Coupon redemptions allowed inside the history. `WELCOME10` starts twenty days ago, so a
#: journey older than that could not have redeemed it without contradicting its own timestamps.
HISTORY_COUPONS = 1
HISTORY_COUPON_DAYS = 15
LIVE_HELD = 6
RUNNING_COUNT = 3
MATCH_ACCEPTS = 14
#: Of the fourteen drafts a provider wrote: eight the customer paid for, four declined, two left
#: in the inbox as drafts.
MATCH_PAID = 8
MATCH_CANCELLED = 4
#: The forward half of the dataset: what a demo user sees when they open the console today.
LIVE_CONFIRMED = 9
LIVE_COUPONS = 2
LIVE_REQUESTS = 4
LIVE_REQUESTS_ACCEPTED = 2
LIVE_BAND_CANCELS = 3
LIVE_HOLD_CANCELS = 2
EXPIRY_COUNT = 2
DISPUTE_COUNT = 6
CLOSED_DEMANDS = 3
CANCELLED_DEMANDS = 3
THREAD_COUNT = 8
PROMO_NOISE = 12
READ_USERS = 6


@dataclass
class Trip:
    """One customer journey: the booking plus the money and the review that hang off it."""

    booking: Booking
    offer: Offer
    customer: User
    org: OrgCtx
    order: Order | None = None
    payment: Payment | None = None
    review: Review | None = None
    booked_at: datetime | None = None
    paid_at: datetime | None = None
    finished_at: datetime | None = None


def _order_number(for_id: uuid.UUID, when: datetime) -> str:
    """The shipped number format, but derived from the row id.

    `commerce._order_number` appends six random characters; a demo whose order numbers change on
    every run cannot be quoted in a document, and the format is all a client is allowed to see.
    """
    suffix = "".join(_ORDER_ALPHABET[byte % 32] for byte in for_id.bytes[:6])
    return f"CX-{when.strftime('%Y%m%d')}-{suffix}"


async def _age_where(session: AsyncSession, model, *, sets: dict[str, Any],
                     **where: Any) -> int:
    """Place rows on the calendar the services could not put them on.

    `set_updated_at()` is a BEFORE UPDATE trigger, so an INSERT keeps the timestamps it carries;
    for rows a service wrote at `now()` this raw UPDATE is the only way to move them into the
    past. It is the one write in this script that bypasses `app.services.*`.
    """
    sql = (f"UPDATE {model.__tablename__} SET "
           + ", ".join(f"{key} = :set_{key}" for key in sets)
           + " WHERE " + " AND ".join(f"{key} = :where_{key}" for key in where))
    params = {**{f"set_{k}": v for k, v in sets.items()},
              **{f"where_{k}": v for k, v in where.items()}}
    result = await session.execute(text(sql), params)
    return int(result.rowcount or 0)


async def _age(session: AsyncSession, model, row_id: uuid.UUID, **columns: Any) -> None:
    await _age_where(session, model, sets=columns, id=row_id)


async def _age_notifications(session: AsyncSession, booking_id: uuid.UUID, *, paid_at: datetime,
                             review_at: datetime | None = None) -> None:
    """Move a journey's bell entries onto the journey's own calendar.

    A notification is keyed to its booking through `data`, which is JSONB — the same way the
    client's bell resolves a row to a screen — so this ages by that key instead of by id.
    """
    common = {"at": paid_at, "id": str(booking_id)}
    await session.execute(
        text("UPDATE notifications SET created_at = :at, updated_at = :at "
             "WHERE data ->> 'booking_id' = :id AND kind NOT LIKE 'review.%'"), common)
    if review_at is not None:
        await session.execute(
            text("UPDATE notifications SET created_at = :at, updated_at = :at "
                 "WHERE data ->> 'booking_id' = :id AND kind LIKE 'review.%'"),
            {"at": review_at, "id": str(booking_id)})


def _stamp(booking: Booking, **extra: Any) -> None:
    """Journeys created through the services carry the service's own `meta`; add the provenance.

    `meta` is plain JSONB, so this is a normal ORM write — it is how `--reset` and a re-run can
    tell a seeded journey from a booking a demo user made afterwards.
    """
    booking.meta = {**(booking.meta or {}), **seed_meta(**extra)}


def _not_future(value: datetime, now: datetime) -> datetime:
    """The freshest history is a day old; nothing here may claim to have happened tomorrow."""
    return min(value, now - timedelta(minutes=15))


def _future_window(style: dict, offer: Offer, now: datetime, index: int,
                   day_offset: int) -> tuple[datetime, datetime]:
    """A window the shipped validator accepts: open day, inside the rule hours, past the lead.

    The widest lead time in the catalog is 24 h and the shortest booking horizon is 30 days, so
    stepping forward by whole days always lands between the two.
    """
    day = _open_day((now + timedelta(days=day_offset)).date())
    start, end = _window(style, day, index)
    lead = timedelta(minutes=offer.min_lead_time_minutes + 30)
    while start - now < lead:
        day = _open_day(day + timedelta(days=1))
        start, end = _window(style, day, index)
    return start, end


def _fits(style: dict, start: datetime, end: datetime) -> bool:
    """Whether one published availability window of the style contains the whole range.

    `STYLE[...]["hours"]` holds `time` objects, the same type the availability column stores, so
    a wall-clock comparison needs no parsing.
    """
    if start.date() != end.date():
        return False
    return any(low <= start.time() and end.time() <= high for low, high in style["hours"])


def _clock_window(now: datetime, offers: list[Offer], *, min_hours: int, max_hours: int,
                  used: set[uuid.UUID], want_pct: int | None = None,
                  hours_long: int = 2) -> tuple[Offer, datetime, datetime]:
    """A window pinned to the clock, for the paths that are graded by hours-before-start.

    A refund band cannot be demonstrated from a listing's tidy 09:00 slot: the policy reads the
    distance from `now`. Quantity-counted listings are the pool because they carry no minimum
    duration, so a two-hour window is legal on any of them.
    """
    for offset in range(min_hours, max_hours + 1):
        target = now + timedelta(hours=offset)
        if target.weekday() == 6:
            continue
        hour = min(max(target.hour, 1), 23 - hours_long)
        start = datetime.combine(target.date(), dtime(hour, 0), tzinfo=timezone.utc)
        end = start + timedelta(hours=hours_long)
        hours_before = (start - now).total_seconds() / 3600
        if not min_hours <= hours_before <= max_hours:
            continue
        for offer in offers:
            if offer.id in used:
                continue
            if not _fits(STYLE[offer.meta["category_key"]], start, end):
                continue
            # The snap to a whole hour can pull the start closer than the offset suggested, and
            # the shipped validator grades the lead time on the snapped value.
            if (start - now).total_seconds() / 60 < offer.min_lead_time_minutes:
                continue
            if want_pct is not None and booking_svc.refund_pct_for(
                    offer.cancellation_policy, hours_before) != want_pct:
                continue
            used.add(offer.id)
            return offer, start, end
    raise RuntimeError(f"no listing serves a window {min_hours}-{max_hours} hours out "
                       f"that refunds {want_pct}%")


def _span_window(now: datetime, offers: list[Offer],
                 used: set[uuid.UUID]) -> tuple[Offer, datetime, datetime]:
    """A window that contains this moment, so a booking can honestly read as underway.

    No published rule crosses midnight, so in the last hour of the day the running window
    starts on the hour instead of an hour ago.
    """
    floor = now.replace(minute=0, second=0, microsecond=0)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    for back in (1, 0):
        start = max(floor - timedelta(hours=back), midnight + timedelta(minutes=5))
        for length in (3, 2, 1):
            end = start + timedelta(hours=length)
            for offer in offers:
                if offer.id in used:
                    continue
                if _fits(STYLE[offer.meta["category_key"]], start, end):
                    used.add(offer.id)
                    return offer, start, end
    raise RuntimeError("no listing can host a session that is running right now")


def _quantity_for(offer: Offer, index: int) -> int:
    if STYLE[offer.meta["category_key"]]["mode"] == "scheduled":
        return 1
    return min(1 + index % 2, offer.max_quantity or 1, 2)


async def _pay(session: AsyncSession, trip: Trip, settings: Settings, *,
               coupon_code: str | None = None) -> None:
    """Order, intent, capture — the three calls the checkout screen makes (§5.6)."""
    trip.order = await commerce.create_order(
        session, booking=trip.booking, offer=trip.offer, buyer_id=trip.customer.id,
        coupon_code=coupon_code)
    trip.paid_at = utcnow()
    if trip.order.total_cents <= 0:
        # Without a payment `_settle` never runs, so the fulfilment record is opened the way
        # `POST /bookings` opens it for a free or on-site-payment listing.
        await commerce.ensure_fulfillment(session, trip.booking, reason="booking confirmed")
        trip.paid_at = None
        return
    trip.payment = await commerce.create_payment_intent(
        session, order=trip.order, provider_key="mock", actor_user_id=trip.customer.id,
        settings=settings)
    await commerce.confirm_payment(session, trip.payment, settings=settings,
                                   actor_user_id=trip.customer.id)


async def _journeys_exist(session: AsyncSession) -> bool:
    tagged = (await session.execute(
        select(func.count()).select_from(Booking).where(Booking.meta["seed"].astext == SEED_TAG)
    )).scalar_one()
    return int(tagged) > 0


async def _age_trip(session: AsyncSession, trip: Trip, *, paid_at: datetime | None = None,
                    review_at: datetime | None = None) -> None:
    """Put a finished journey's rows on its own calendar, newest write last.

    Every timestamp here is one the services stamped at `now()` because they could only validate
    a window that was live when it was written.
    """
    now = utcnow()
    booking, order, payment = trip.booking, trip.order, trip.payment
    paid_at = _not_future(paid_at or trip.booked_at, now)
    finished = _not_future(trip.finished_at or paid_at, now)
    review_at = None if review_at is None else _not_future(review_at, now)

    columns: dict[str, Any] = {"created_at": trip.booked_at, "updated_at": finished,
                               "confirmed_at": trip.booked_at, "started_at": None,
                               "completed_at": None, "cancelled_at": None}
    if booking.status == "cancelled":
        columns["cancelled_at"] = _not_future(booking.window_start - timedelta(hours=2), now)
    else:
        columns["started_at"] = booking.window_start + timedelta(minutes=5)
        columns["completed_at"] = finished
    await _age(session, Booking, booking.id, **columns)

    events = list((await session.execute(
        select(BookingStatusEvent)
        .where(BookingStatusEvent.booking_id == booking.id)
        .order_by(BookingStatusEvent.created_at, BookingStatusEvent.id))).scalars().all())
    for step, event in enumerate(events):
        event.created_at = trip.booked_at + timedelta(minutes=6 * step)
    await session.flush()

    if order is not None:
        await _age(session, Order, order.id, created_at=trip.booked_at, updated_at=paid_at,
                   placed_at=trip.booked_at, number=_order_number(order.id, trip.booked_at))
        await _age_where(session, Commission,
                         sets={"created_at": paid_at, "updated_at": paid_at, "booked_at": paid_at},
                         order_id=order.id)
        if payment is not None:
            await _age(session, Payment, payment.id, created_at=trip.booked_at,
                       updated_at=paid_at, confirmed_at=paid_at)

    fulfillment = await commerce.fulfillment_for_booking(session, booking.id)
    if fulfillment is not None:
        # The note bodies are what the service wrote; only their stamps belong on the calendar.
        notes = [{"body": note.get("body", ""), "author_id": note.get("author_id"),
                  "created_at": iso(trip.booked_at if position == 0 else _not_future(
                      booking.window_start + timedelta(minutes=30 * position), finished))}
                 for position, note in enumerate(fulfillment.notes or [])]
        fulfillment.notes = notes
        await _age(session, Fulfillment, fulfillment.id, created_at=trip.booked_at,
                   updated_at=finished, started_at=booking.window_start,
                   completed_at=finished if fulfillment.status == "completed" else None)
        await session.flush()

    if trip.review is not None:
        await _age(session, Review, trip.review.id, created_at=review_at, updated_at=review_at,
                   replied_at=(None if trip.review.provider_reply is None
                               else _not_future(review_at + timedelta(hours=3), now)))

    await _age_notifications(session, booking.id, paid_at=paid_at, review_at=review_at)


# --------------------------------------------------------------------------- journey copy
FULFILLMENT_NOTES = (
    "Handover at the gate, paperwork signed and the unit released to the customer.",
    "Ran to the booked window; access code rotated afterwards as usual.",
    "Collected early, kit checked back in and put away clean.",
)
CANCEL_REASONS = (
    "Plans changed at our end, so the day is no longer needed.",
    "The shipment moved and this slot is not required after all.",
)
DISPUTE_COPY = {
    "quality": "The unit handed over was not the one in the listing photographs. We worked "
               "around it, but the day ran slower than quoted.",
    "no_show": "Nobody met us at the yard and the access code never arrived, so the whole "
               "window was lost.",
    "payment": "We were charged twice for the same session and only one of the charges has "
               "come back.",
    "damage": "A pallet jack was damaged while it was with us and the provider has asked us "
              "to cover the repair.",
    "other": "The hours published on the listing did not match the hours the gate actually "
             "kept.",
}
RESOLUTION_COPY = {
    "resolved_partial": "Half of the day was usable, so half of the session fee goes back and "
                        "the listing photograph has been corrected.",
    "resolved_refund": "The window was never delivered. The full amount is returned and the "
                       "listing stays paused until the access process is fixed.",
    "resolved_no_fault": "Both sets of logs line up: the crew arrived an hour after the booked "
                         "start, so the provider is not at fault and nothing is owed.",
    "under_review": "We have asked the provider for its gate log and will come back to you "
                    "with a decision.",
    "closed": "The parties settled this between themselves, so the case is closed with no "
              "money moving.",
}
OPEN_QUESTIONS = (
    "We arrive with two vehicles; can the second one stage in the yard until the slot opens?",
    "Please confirm the access code goes out the evening before rather than on the morning.",
    "Is a marshaller on site for the return, or do we bring our own?",
    "One of our crew needs a quiet room for an hour during the session; is that possible?",
    "Can we extend by an hour on the day if the load is late coming off?",
    "Do you have a spare pallet jack we could borrow for the transfer?",
    "Where exactly does the barrier arm sit relative to the gate number?",
    "Will the walk-in fridge be free for the whole of our window?",
)
PROVIDER_ANSWERS = (
    "Confirmed. The code goes out at 18:00 the day before and the second vehicle can wait in "
    "the yard.",
    "Yes, a marshaller is on site for every return slot. Bring nothing but the paperwork.",
    "The quiet room is free for that hour; we will hold it and send you the floor plan.",
    "We can extend on the day if the next slot is open — message the gate and they will decide.",
)
DEMAND_FALLBACK = "Looking for {quantity} of {word} in {city} for the week, access as published."


# --------------------------------------------------------------------------- history phase
async def _seed_history(session: AsyncSession, ctx: Ctx, settings: Settings) -> int:
    """Forty past journeys on last month's clock: paid, delivered, rated, and four cancelled.

    The booking API refuses a window that has already begun (§5.4), so the past is written as
    rows and then aged; everything hanging off those rows — order, payment, commission,
    fulfilment, review, cancellation — is produced by the same services the API calls.
    """
    now = utcnow()
    coupons_left = HISTORY_COUPONS
    for index in range(HISTORY_JOURNEYS):
        offer = ctx.pick(index * 3 + 1)
        style, definition, _resource = _parts(offer)
        day = _open_day((now - timedelta(days=1 + index % HISTORY_DAYS)).date(), back=True)
        start, end = _window(style, day, index)
        quantity = _quantity_for(offer, index)
        unit_cents, total_cents = booking_svc.price_cents(offer, definition, quantity, start, end)
        booked_at = start - timedelta(days=2, hours=index % 5)
        paid_at = booked_at + timedelta(minutes=7)
        customer = ctx.customers[(index * 13 + 2) % len(ctx.customers)]
        booking = Booking(
            id=sid("booking", "history", index), offer_id=offer.id,
            definition_id=definition.id, org_id=offer.org_id, customer_id=customer.id,
            created_by_user_id=customer.id, status="confirmed",
            payment_status="unpaid" if offer.requires_payment else "not_required",
            window_start=start, window_end=end, quantity=quantity,
            unit_amount_cents=unit_cents, currency=offer.currency, total_cents=total_cents,
            confirmed_at=booked_at,
            meta=seed_meta(phase="history", category=style["word"]))
        session.add(booking)
        session.add(BookingStatusEvent(booking_id=booking.id, from_status=None,
                                       to_status="confirmed", actor_user_id=customer.id,
                                       reason="booking created", meta={"quantity": quantity}))
        await session.flush()

        trip = Trip(booking=booking, offer=offer, customer=customer, org=ctx.org(offer.org_id),
                    booked_at=booked_at, finished_at=end + timedelta(minutes=45))
        # A coupon that started three weeks ago cannot have been redeemed before it existed, so
        # only the recent end of the history is allowed to carry one.
        use_coupon = bool(coupons_left) and (offer.requires_payment and total_cents >= 5000
                                             and (now - start).days <= HISTORY_COUPON_DAYS)
        coupons_left -= int(use_coupon)
        await _pay(session, trip, settings, coupon_code=COUPON_CODE if use_coupon else None)
        actor = trip.org.person(index)

        if index >= HISTORY_JOURNEYS - HISTORY_CANCELLED:
            # Cancelled after the window passed: the policy grades by hours-before-start, and
            # a negative distance clears no band, so nothing is owed back.
            await booking_svc.cancel(session, booking, actor_user_id=customer.id,
                                     reason=CANCEL_REASONS[index % len(CANCEL_REASONS)])
            await _age_trip(session, trip, paid_at=paid_at)
            continue

        fulfillment = await commerce.fulfillment_for_booking(session, booking.id)
        await booking_svc.transition(session, booking, "in_progress", actor_user_id=actor.id,
                                     reason="service started")
        await commerce.set_fulfillment_status(session, fulfillment, "in_progress",
                                             actor_user_id=actor.id)
        await commerce.add_fulfillment_note(session, fulfillment,
                                            body=FULFILLMENT_NOTES[index % len(FULFILLMENT_NOTES)],
                                            author_id=actor.id)
        await booking_svc.transition(session, booking, "completed", actor_user_id=actor.id,
                                     reason="service completed")
        await commerce.set_fulfillment_status(session, fulfillment, "completed",
                                              actor_user_id=actor.id)

        review_at = None
        if index < HISTORY_RATED:
            rating = RATINGS[index]
            trip.review = await reviews.create_review(
                session, booking=booking, rating=rating,
                comment=COMMENTS[rating][index % len(COMMENTS[rating])],
                reviewer_id=customer.id)
            if index % 3 == 0:
                await reviews.reply(session, trip.review,
                                    body=REPLIES[(index // 3) % len(REPLIES)],
                                    org_id=trip.org.org.id, actor_user_id=actor.id)
            review_at = trip.finished_at + timedelta(hours=5)
            ctx.completed.append(trip)
        await _age_trip(session, trip, paid_at=paid_at, review_at=review_at)
    return HISTORY_JOURNEYS


# --------------------------------------------------------------------------- live journey phases
async def _held(session: AsyncSession, offer: Offer, start: datetime, end: datetime,
                quantity: int, customer: User, *, phase: str, index: int) -> Booking:
    """Take capacity the way the checkout screen does: `POST /bookings/hold`, then `POST /bookings`."""
    hold = await booking_svc.create_hold(
        session, offer_id=offer.id, start=start, end=end, quantity=quantity,
        customer_id=customer.id, request=None)
    _stamp(hold, phase=phase, index=index, category=offer.meta["category_key"])
    booking = await booking_svc.create_booking(session, customer_id=customer.id, request=None,
                                              hold_id=hold.id)
    _stamp(booking, phase=phase, index=index, hold_id=str(hold.id),
           category=offer.meta["category_key"])
    return booking


async def _booked(session: AsyncSession, offer: Offer, start: datetime, end: datetime,
                  quantity: int, customer: User, *, phase: str, index: int) -> Booking:
    """Book straight from the listing: `confirmed` when instant, `draft` when it needs the owner."""
    booking = await booking_svc.create_booking(
        session, customer_id=customer.id, request=None, offer_id=offer.id, start=start, end=end,
        quantity=quantity)
    _stamp(booking, phase=phase, index=index, category=offer.meta["category_key"])
    return booking


def _trip(booking: Booking, offer: Offer, customer: User, ctx: Ctx) -> Trip:
    return Trip(booking=booking, offer=offer, customer=customer, org=ctx.org(offer.org_id),
                booked_at=booking.created_at)


async def _seed_live_bookings(session: AsyncSession, ctx: Ctx, settings: Settings) -> int:
    """Nine journeys still ahead of the clock, so the provider console has an upcoming list.

    Every other one is taken through a hold, which is the path that converts reserved capacity
    into confirmed capacity without counting it twice (§5.5 step 4).
    """
    now = utcnow()
    coupons_left = LIVE_COUPONS
    for index in range(LIVE_CONFIRMED):
        # The last journey is the free listing: a confirmed booking whose order lands on
        # `not_required` is the zero-money path, end to end.
        offer = (ctx.free[index % len(ctx.free)] if index == LIVE_CONFIRMED - 1
                 else ctx.clock_friendly[(index * 3) % len(ctx.clock_friendly)])
        ctx.live_used.add(offer.id)
        style = STYLE[offer.meta["category_key"]]
        start, end = _future_window(style, offer, now, index, 3 + index * 2)
        customer = ctx.customers[(index * 29 + 5) % len(ctx.customers)]
        booking = (await _held(session, offer, start, end, _quantity_for(offer, index), customer,
                               phase="live", index=index) if index % 2 == 0
                   else await _booked(session, offer, start, end, _quantity_for(offer, index),
                                      customer, phase="live", index=index))
        trip = _trip(booking, offer, customer, ctx)
        use_coupon = bool(coupons_left) and offer.requires_payment and booking.total_cents >= 5000
        coupons_left -= int(use_coupon)
        await _pay(session, trip, settings, coupon_code=COUPON_CODE if use_coupon else None)
        ctx.confirmed.append(trip)
    return LIVE_CONFIRMED


async def _seed_live_holds(session: AsyncSession, ctx: Ctx) -> int:
    """Six reservations that are open right now, the last of them two minutes from lapsing (§9)."""
    now = utcnow()
    expiring: list[uuid.UUID] = []
    for index in range(LIVE_HELD):
        last = index == LIVE_HELD - 1
        if last:
            # Only a quantity-counted listing can be pinned to the clock: a scheduled one carries
            # a minimum duration, and a two-minute-from-now window is not two hours long.
            pool = [o for o in ctx.short_hold
                    if STYLE[o.meta["category_key"]]["mode"] == "quantity"]
            offer, start, end = _clock_window(now, pool, min_hours=2, max_hours=36,
                                              used=ctx.live_used)
        else:
            offer = ctx.instant[(index * 17 + 4) % len(ctx.instant)]
            start, end = _future_window(STYLE[offer.meta["category_key"]], offer, now, index,
                                        1 + index)
        customer = ctx.customers[(index * 31 + 3) % len(ctx.customers)]
        hold = await booking_svc.create_hold(
            session, offer_id=offer.id, start=start, end=end,
            quantity=_quantity_for(offer, index), customer_id=customer.id, request=None)
        _stamp(hold, phase="held", index=index, category=offer.meta["category_key"])
        ctx.held.append(_trip(hold, offer, customer, ctx))
        if last:
            # "Expires in two minutes" is only true for two of a five-minute hold's minutes, so
            # the row is written onto the time it is meant to be read at.
            await _age(session, Booking, hold.id, created_at=now - timedelta(minutes=3),
                       updated_at=now - timedelta(minutes=3),
                       hold_expires_at=now + timedelta(minutes=2))
            await session.flush()
            await session.refresh(hold)
            expiring.append(hold.id)
    if not expiring:
        raise RuntimeError("no hold was placed on the expiring-soon path")
    return LIVE_HELD


async def _seed_live_requests(session: AsyncSession, ctx: Ctx, settings: Settings) -> int:
    """Four asks on `request_confirm` listings: two the owner has since accepted, two waiting.

    A draft is the shipped screen's inbox, so it carries the unpaid order and the bell the
    `POST /bookings` route writes for it — nothing here invents a state the API would refuse.
    """
    now = utcnow()
    for index in range(LIVE_REQUESTS):
        offer = ctx.request_confirm[(index * 7 + 2) % len(ctx.request_confirm)]
        style = STYLE[offer.meta["category_key"]]
        start, end = _future_window(style, offer, now, index, 4 + index * 3)
        customer = ctx.customers[(index * 41 + 11) % len(ctx.customers)]
        booking = await _booked(session, offer, start, end, _quantity_for(offer, index),
                               customer, phase="request", index=index)
        if booking.status != "draft":
            raise RuntimeError(f"a request_confirm listing booked as {booking.status}")
        trip = _trip(booking, offer, customer, ctx)
        trip.order = await commerce.create_order(session, booking=booking, offer=offer,
                                                buyer_id=customer.id)
        await notifications.notify_org(
            session, offer.org_id, kind="booking.requested", title="New booking request",
            body="A customer is waiting for confirmation on one of your offers.",
            data={"booking_id": str(booking.id)})
        if index < LIVE_REQUESTS_ACCEPTED:
            owner = ctx.org(offer.org_id).owner
            await booking_svc.confirm_request(session, booking, actor_user_id=owner.id)
            await _pay(session, trip, settings)
            trip.paid_at = trip.payment.confirmed_at if trip.payment else None
        ctx.requests.append(trip)
    return LIVE_REQUESTS


async def _seed_live_running(session: AsyncSession, ctx: Ctx, settings: Settings) -> int:
    """Three sessions open at this minute, which is what the provider console leads with.

    A booking cannot be *created* on a window that has already started (§5.4), so each one is
    created on a legal future window, paid, and then moved onto the window it is living inside —
    the row the `/start` route leaves behind, only reachable in one step here.
    """
    now = utcnow()
    for index in range(RUNNING_COUNT):
        offer, start, end = _span_window(now, ctx.clock_friendly, ctx.live_used)
        style, _definition, _resource = _parts(offer)
        created_start, created_end = _future_window(style, offer, now, index, 2 + index)
        customer = ctx.customers[(index * 37 + 7) % len(ctx.customers)]
        booking = await _booked(session, offer, created_start, created_end,
                               _quantity_for(offer, index), customer, phase="running", index=index)
        trip = _trip(booking, offer, customer, ctx)
        await _pay(session, trip, settings)
        booking.window_start = start
        booking.window_end = end
        await session.flush()
        actor = trip.org.person(index + 1)
        await booking_svc.transition(session, booking, "in_progress", actor_user_id=actor.id,
                                    reason="service started")
        fulfillment = await commerce.ensure_fulfillment(session, booking)
        await commerce.set_fulfillment_status(session, fulfillment, "in_progress",
                                            actor_user_id=actor.id)
        await commerce.add_fulfillment_note(session, fulfillment,
                                           body="Session opened on the booked window; the "
                                                "customer is on site.", author_id=actor.id)
        trip.finished_at = end
        ctx.running.append(trip)
    return RUNNING_COUNT


async def _seed_live_cancellations(session: AsyncSession, ctx: Ctx,
                                  settings: Settings) -> int:
    """Five cancellations: three graded by the refund bands, two holds that never became money."""
    now = utcnow()
    bands = ((100, 60, 96), (50, 28, 40), (0, 4, 12))
    for index, (want_pct, low, high) in enumerate(bands):
        offer, start, end = _clock_window(now, ctx.clock_friendly, min_hours=low, max_hours=high,
                                         used=ctx.live_used, want_pct=want_pct)
        customer = ctx.customers[(index * 43 + 17) % len(ctx.customers)]
        booking = await _held(session, offer, start, end, _quantity_for(offer, index), customer,
                             phase="cancelled", index=index)
        trip = _trip(booking, offer, customer, ctx)
        await _pay(session, trip, settings)
        hours_before = round((start - now).total_seconds() / 3600, 1)
        _booking, refunded = await booking_svc.cancel(
            session, booking, actor_user_id=customer.id,
            reason=CANCEL_REASONS[index % len(CANCEL_REASONS)])
        ctx.refunds.append({"path": "cancellation policy", "cents": refunded,
                            "refund_pct": want_pct, "hours_before_start": hours_before,
                            "order_number": trip.order.number if trip.order else None})
    for index in range(LIVE_HOLD_CANCELS):
        # A hold has no order yet, so cancelling it frees capacity and returns nothing (§5.6).
        offer = ctx.instant[(index * 23 + 9) % len(ctx.instant)]
        start, end = _future_window(STYLE[offer.meta["category_key"]], offer, now, index,
                                    6 + index * 4)
        customer = ctx.customers[(index * 11 + 23) % len(ctx.customers)]
        hold = await booking_svc.create_hold(session, offer_id=offer.id, start=start, end=end,
                                            quantity=_quantity_for(offer, index),
                                            customer_id=customer.id, request=None)
        _stamp(hold, phase="cancelled", index=index, category=offer.meta["category_key"])
        _booking, refunded = await booking_svc.cancel(
            session, hold, actor_user_id=customer.id,
            reason="Hold released: the run it was reserved for was postponed.")
        ctx.refunds.append({"path": "hold release", "cents": refunded, "refund_pct": 0,
                            "hours_before_start": round((start - now).total_seconds() / 3600, 1),
                            "order_number": None})
    return LIVE_BAND_CANCELS + LIVE_HOLD_CANCELS


async def _seed_live_expiry(session: AsyncSession, ctx: Ctx) -> int:
    """Two holds the §10 sweeper lapses on the run, so an `expired` row is a real one."""
    now = utcnow()
    due: list[uuid.UUID] = []
    for index in range(EXPIRY_COUNT):
        offer = ctx.instant[(index * 43 + 14) % len(ctx.instant)]
        start, end = _future_window(STYLE[offer.meta["category_key"]], offer, now, index,
                                    2 + index)
        customer = ctx.customers[(index * 19 + 27) % len(ctx.customers)]
        hold = await booking_svc.create_hold(session, offer_id=offer.id, start=start, end=end,
                                            quantity=_quantity_for(offer, index),
                                            customer_id=customer.id, request=None)
        _stamp(hold, phase="expired", index=index, category=offer.meta["category_key"])
        # The sweeper only lapses a hold whose deadline has passed, so the deadline is what moves.
        await _age(session, Booking, hold.id, created_at=now - timedelta(minutes=40),
                   updated_at=now - timedelta(minutes=40),
                   hold_expires_at=now - timedelta(minutes=5))
        await session.flush()
        await session.refresh(hold)
        due.append(hold.id)
    expired = await booking_svc.expire_due_holds(session)
    missing = [str(i) for i in due if i not in set(expired)]
    if missing:
        raise RuntimeError(f"the hold sweeper left these reservations live: {missing}")
    return len(expired)


async def _seed_live_disputes(session: AsyncSession, ctx: Ctx) -> int:
    """Six cases across the §5.7 lifecycle, every one decided by `disputes.resolve()`.

    Money only moves through `commerce`, so a partial resolution leaves the order
    `partially_refunded` and a full one leaves the booking cancelled exactly as support's decision
    would in production. A complaint about delivered work keeps the booking `completed` — the
    dispute compensates for the session, it does not un-deliver it.
    """
    support = ctx.support
    paid_live = [t for t in ctx.confirmed if t.payment is not None]
    delivered = [t for t in ctx.completed if t.payment is not None]
    if len(paid_live) < 2 or len(delivered) < 3:
        raise RuntimeError("the dispute phase needs paid live and delivered journeys to work on")

    # 1. Taken for review while the session is still open.
    running = ctx.running[0]
    case = await disputes.open_dispute(session, booking=running.booking, kind="quality",
                                       description=DISPUTE_COPY["quality"],
                                       complainant_id=running.customer.id)
    await disputes.resolve(session, case, status="under_review",
                           resolution_note=RESOLUTION_COPY["under_review"],
                           actor_user_id=support.id)

    # 2. Part of the session was usable, so part of the fee goes back.
    partial = paid_live[0]
    case = await disputes.open_dispute(session, booking=partial.booking, kind="payment",
                                      description=DISPUTE_COPY["payment"],
                                      complainant_id=partial.customer.id)
    _case, refunded = await disputes.resolve(
        session, case, status="resolved_partial",
        resolution_note=RESOLUTION_COPY["resolved_partial"], actor_user_id=support.id,
        refund_cents=max(1, partial.order.total_cents // 5))
    ctx.refunds.append({"path": "dispute resolved_partial", "cents": refunded,
                        "order_number": partial.order.number})

    # 3. Nothing was delivered, so everything goes back and the booking is treated as cancelled.
    full = paid_live[1]
    case = await disputes.open_dispute(session, booking=full.booking, kind="no_show",
                                      description=DISPUTE_COPY["no_show"],
                                      complainant_id=full.customer.id)
    _case, refunded = await disputes.resolve(
        session, case, status="resolved_refund", resolution_note=RESOLUTION_COPY["resolved_refund"],
        actor_user_id=support.id)
    ctx.refunds.append({"path": "dispute resolved_refund", "cents": refunded,
                        "order_number": full.order.number})

    # 4. The provider complains about a delivered job and support finds no fault.
    no_fault = delivered[0]
    case = await disputes.open_dispute(
        session, booking=no_fault.booking, kind="damage", description=DISPUTE_COPY["damage"],
        complainant_id=no_fault.org.person(1).id)
    await disputes.resolve(session, case, status="resolved_no_fault",
                           resolution_note=RESOLUTION_COPY["resolved_no_fault"],
                           actor_user_id=support.id)

    # 5. A customer's complaint about delivered work, still waiting for a decision.
    waiting = delivered[1]
    await disputes.open_dispute(session, booking=waiting.booking, kind="other",
                               description=DISPUTE_COPY["other"],
                               complainant_id=waiting.customer.id)

    # 6. Settled between the parties, so the case closes with no money moving.
    settled = delivered[2]
    case = await disputes.open_dispute(session, booking=settled.booking, kind="quality",
                                      description=DISPUTE_COPY["quality"],
                                      complainant_id=settled.customer.id)
    await disputes.resolve(session, case, status="closed",
                           resolution_note=RESOLUTION_COPY["closed"], actor_user_id=support.id)
    return DISPUTE_COUNT


async def _prove_exclusions(session: AsyncSession, ctx: Ctx) -> int:
    """§9 asks the dataset to *prove* capacity is exclusive, so three refusals are recorded here.

    Each one is the engine's own answer, not a hand-written row: the same 409 a second customer
    would get if they tried to take what is already taken.
    """
    now = utcnow()

    def refused(proof: str, error: Exception, *, expected: type) -> None:
        if not isinstance(error, expected):
            raise RuntimeError(f"{proof}: expected {expected.__name__}, got "
                               f"{type(error).__name__}: {error}")
        ctx.refusals.append({"proof": proof, "error": type(error).__name__,
                             "details": getattr(error, "details", None)})

    # (a) One exclusive slot: the second customer is refused, not queued.
    offer = ctx.single_slot[0]
    style = STYLE[offer.meta["category_key"]]
    start, end = _future_window(style, offer, now, 0, 5)
    first = await booking_svc.create_hold(session, offer_id=offer.id, start=start, end=end,
                                        quantity=1, customer_id=ctx.customers[0].id, request=None)
    _stamp(first, phase="conflict", proof="exclusive_slot", category=offer.meta["category_key"])
    try:
        await booking_svc.create_hold(session, offer_id=offer.id, start=start, end=end,
                                    quantity=1, customer_id=ctx.customers[1].id, request=None)
    except (NoAvailability, CapacityExceeded) as exc:
        refused("exclusive_slot", exc, expected=(NoAvailability, CapacityExceeded))
    else:
        raise RuntimeError("an exclusive slot accepted a second customer")

    # (b) Fungible quantity: one unit held of a two-unit ceiling, then a request for two more.
    capped = [o for o in ctx.clock_friendly if o._definition.max_quantity == 2]  # type: ignore[attr-defined]
    if not capped:
        raise RuntimeError("no two-unit listing to prove the quantity ceiling on")
    offer = capped[0]
    style = STYLE[offer.meta["category_key"]]
    start, end = _future_window(style, offer, now, 1, 5)
    held = await booking_svc.create_hold(session, offer_id=offer.id, start=start, end=end,
                                        quantity=1, customer_id=ctx.customers[2].id, request=None)
    _stamp(held, phase="conflict", proof="quantity_ceiling", category=offer.meta["category_key"])
    try:
        await booking_svc.create_hold(session, offer_id=offer.id, start=start, end=end,
                                    quantity=2, customer_id=ctx.customers[3].id, request=None)
    except CapacityExceeded as exc:
        refused("quantity_ceiling", exc, expected=CapacityExceeded)
    else:
        raise RuntimeError("the quantity ceiling let a third unit through")

    # (c) A window the published hours simply do not keep.
    night_day = _open_day((now + timedelta(days=1)).date())
    night = [o for o in ctx.clock_friendly
             if not _fits(STYLE[o.meta["category_key"]],
                          datetime.combine(night_day, dtime(23, 0), tzinfo=timezone.utc),
                          datetime.combine(night_day, dtime(23, 30), tzinfo=timezone.utc))]
    if not night:
        raise RuntimeError("every listing is open all night, so the closed-window case cannot run")
    offer = night[0]
    try:
        await booking_svc.create_hold(
            session, offer_id=offer.id,
            start=datetime.combine(night_day, dtime(23, 0), tzinfo=timezone.utc),
            end=datetime.combine(night_day, dtime(23, 30), tzinfo=timezone.utc),
            quantity=1, customer_id=ctx.customers[4].id, request=None)
    except NoAvailability as exc:
        refused("outside_published_hours", exc, expected=NoAvailability)
    else:
        raise RuntimeError("a window outside the published hours was booked")
    return len(ctx.refusals)


async def _seed_demands(session: AsyncSession, ctx: Ctx, settings: Settings) -> tuple[int, int]:
    """One hundred asks, each anchored on a live listing's own category and city, then scored.

    Anchoring is what keeps the run inside a minute: `refresh_matches` prices every candidate's
    free quantity for the requested window, so a demand that could match a hundred listings would
    cost a hundred capacity reads. A category in one city has one or two.
    """
    now = utcnow()
    demands: list[Demand] = []
    for index in range(DEMAND_COUNT):
        anchor = ctx.pick(index * 11 + 5)
        style, definition, resource = _parts(anchor)
        category = await _one(session, CapacityCategory, key=anchor.meta["category_key"])
        start, end = _future_window(style, anchor, now, index, 2 + index % 12)
        # Never ask for more than the smallest unit of the listing can serve, or the capacity
        # engine would honestly answer with no candidates at all.
        quantity = 1 + index % min(3, style["ceiling"][0])
        unit_cents, total_cents = booking_svc.price_cents(anchor, definition, quantity, start, end)
        customer = ctx.customers[(index * 17 + 4) % len(ctx.customers)]
        demand = await matching_svc.create_demand(
            session, customer_id=customer.id,
            values={
                "category_id": category.id,
                "description": style["demand"].format(quantity=quantity),
                "address": dict(resource.address or {}),
                "lat": resource.lat, "lon": resource.lon,
                "desired_start": start, "desired_end": end, "quantity": quantity,
                "budget_min_cents": max(0, total_cents - 20000),
                "budget_max_cents": total_cents + 25000,
                "status": "open",
                "expires_at": now + timedelta(days=DEMAND_EXPIRY_DAYS[index % 4]),
            })
        demands.append(demand)
        await matching_svc.refresh_matches(session, demand, limit=5)
    return DEMAND_COUNT, await _answer_demands(session, ctx, settings, demands)


async def _answer_demands(session: AsyncSession, ctx: Ctx, settings: Settings,
                          demands: list[Demand]) -> int:
    """Providers answer fourteen proposals; the customers then pay, decline or leave them open."""
    accepted: list[Booking] = []
    for demand in demands:
        if len(accepted) >= MATCH_ACCEPTS:
            break
        for match in await matching_svc.list_matches(session, demand.id):
            if match.status != "suggested":
                continue
            offer = await session.get(Offer, match.offer_id)
            org_ctx = ctx.org(offer.org_id)
            try:
                booking = await matching_svc.accept_match(
                    session, match, actor_user_id=org_ctx.owner.id, org_id=org_ctx.org.id)
            except (NoAvailability, CapacityExceeded, Conflict, ValidationFailed,
                    InvalidStateTransition) as exc:
                # A proposal the engine can no longer honour is a real answer, and worth showing:
                # the score is a ranking, never a reservation.
                ctx.refusals.append({"proof": "match_accept", "error": type(exc).__name__,
                                     "details": getattr(exc, "details", None)})
                continue
            _stamp(booking, phase="match", demand_id=str(demand.id),
                   category=offer.meta["category_key"])
            accepted.append(booking)
            break
    if len(accepted) < MATCH_ACCEPTS:
        raise RuntimeError(f"providers could only answer {len(accepted)} of {MATCH_ACCEPTS} "
                           f"proposals ({len(ctx.refusals)} refusals)")
    for index, booking in enumerate(accepted):
        offer = await session.get(Offer, booking.offer_id)
        trip = Trip(booking=booking, offer=offer,
                    customer=await session.get(User, booking.customer_id),
                    org=ctx.org(booking.org_id))
        if index < MATCH_PAID:
            # The customer confirms the provider's draft, which revalidates capacity (§5.5 step 5).
            await booking_svc.confirm_request(session, booking, actor_user_id=trip.customer.id)
            trip.order = await commerce.create_order(session, booking=booking, offer=offer,
                                                    buyer_id=trip.customer.id)
            await _pay(session, trip, settings)
            ctx.confirmed.append(trip)
        elif index < MATCH_PAID + MATCH_CANCELLED:
            _booking, refunded = await booking_svc.cancel(
                session, booking, actor_user_id=trip.customer.id,
                reason="Declined after the provider answered: the week moved.")
            ctx.refunds.append({"path": "match declined", "cents": refunded,
                               "order_number": None})
        # The rest stay `draft`: an inbox the demo user can still act on.
    closed = [d for d in demands if d.status == "open"]
    for demand in closed[:CLOSED_DEMANDS]:
        await matching_svc.set_status(session, demand, "closed",
                                     actor_user_id=demand.customer_id)
    for demand in closed[CLOSED_DEMANDS:CLOSED_DEMANDS + CANCELLED_DEMANDS]:
        await matching_svc.set_status(session, demand, "cancelled",
                                     actor_user_id=demand.customer_id)
    return len(accepted)


# --------------------------------------------------------------------------- conversations
async def _seed_conversations(session: AsyncSession, ctx: Ctx) -> int:
    """Nine threads: eight about a live booking, one enquiry against a listing, with the answers.

    A thread is opened the way the client opens it — by the customer, from the booking or the
    offer — and the provider's reply is written by somebody who actually manages that org.
    """
    pool = ctx.confirmed[:6] + ctx.requests[:2]
    threads = 0
    for index, trip in enumerate(pool):
        conversation, created = await conversations.open_conversation(
            session, user_id=trip.customer.id, org_ids=set(), is_platform_admin=False,
            kind="booking", ref_id=trip.booking.id,
            initial_body=OPEN_QUESTIONS[index % len(OPEN_QUESTIONS)])
        if not created:
            raise RuntimeError("a seeded booking shared its thread with another journey")
        await conversations.post_message(
            session, conversation, sender_id=trip.org.person(index).id,
            body=PROVIDER_ANSWERS[index % len(PROVIDER_ANSWERS)])
        if index % 3 == 1:
            await conversations.post_message(
                session, conversation, sender_id=trip.customer.id,
                body="That works for us — thank you for confirming so quickly.")
        threads += 1

    enquiry_offer = ctx.pick(57)
    asker = ctx.customers[13]
    conversation, _created = await conversations.open_conversation(
        session, user_id=asker.id, org_ids=set(), is_platform_admin=False, kind="offer",
        ref_id=enquiry_offer.id,
        initial_body="Is the listing photo the unit we would actually get, and can we collect "
                     "an hour earlier than the published window?")
    await conversations.post_message(
        session, conversation, sender_id=ctx.org(enquiry_offer.org_id).owner.id,
        body="Yes, that is the unit, and the gate can let a collection in an hour early if we "
             "have the vehicle plate the day before.")
    threads += 1

    await conversations.set_status(session, conversation, to_status="closed",
                                  actor_user_id=asker.id)
    return threads


# --------------------------------------------------------------------------- notification noise
PROMO_TITLES = (
    ("New capacity listed near you", "A new dock block went live in your city this week."),
    ("Weekend rates are open", "Two kitchens published weekend line hours for this month."),
    ("Your coupon is still live", "WELCOME10 takes ten percent off your next confirmed booking."),
    ("Fleet capacity added", "Refrigerated vans with a driver are bookable in Manchester now."),
)
PROVIDER_REMINDER = ("Weekly capacity review",
                     "Check that your published hours and hold windows still match the yard.")


async def _seed_notification_noise(session: AsyncSession, ctx: Ctx,
                                   settings: Settings) -> dict[str, int]:
    """The bell needs more than lifecycle traffic, and the §10 dispatcher runs once over it.

    `SMTP_HOST` is unset in this environment, so outbound rows are recorded as sent without a
    delivery — the same no-op the worker performs, never a failure (§11).
    """
    for index in range(PROMO_NOISE):
        customer = ctx.customers[(index * 19 + 6) % len(ctx.customers)]
        title, body = PROMO_TITLES[index % len(PROMO_TITLES)]
        await notifications.notify(
            session, user_id=customer.id, kind="promotion.catalogue", title=title, body=body,
            data={"campaign": "seed-weekly", "position": str(index)},
            channels=("in_app", "email"))
    for org_ctx in ctx.orgs:
        await notifications.notify_org(
            session, org_ctx.org.id, kind="provider.review_reminder",
            title=PROVIDER_REMINDER[0], body=PROVIDER_REMINDER[1],
            data={"cadence": "weekly"})

    stats = await notifications.send_pending(session, settings, limit=500)
    for index in range(READ_USERS):
        await notifications.mark_all_read(session, ctx.customers[40 + index].id)
    reader = ctx.customers[46].id
    rows = list((await session.execute(
        select(Notification.id).where(Notification.user_id == reader,
                                      Notification.read_at.is_(None))
        .order_by(Notification.created_at.desc()).limit(2))).scalars().all())
    for notification_id in rows:
        await notifications.mark_read(session, notification_id, reader)
    stats["individually_read"] = len(rows)
    return stats


# --------------------------------------------------------------------------- summary
async def _counts(session: AsyncSession, model, **where: Any) -> int:
    stmt = select(func.count()).select_from(model)
    for key, value in where.items():
        stmt = stmt.where(getattr(model, key) == value)
    return int((await session.execute(stmt)).scalar_one())


async def _summary(session: AsyncSession, ctx: Ctx) -> dict[str, Any]:
    """Read the dataset back the way an auditor would: by counting what is actually stored."""
    statuses = dict((await session.execute(
        select(Booking.status, func.count()).group_by(Booking.status))).all())
    rating, rated = (await session.execute(
        select(func.avg(Review.rating), func.count())
        .where(Review.status == "published"))).one()
    dispute_rows = dict((await session.execute(
        select(Dispute.status, func.count()).group_by(Dispute.status))).all())
    categories_used = int((await session.execute(
        select(func.count(func.distinct(Offer.meta["category_key"].astext)))
        .where(Offer.status == "published"))).scalar_one())
    money = (await session.execute(text(
        "SELECT coalesce(sum(total_cents), 0), coalesce(sum(refunded_cents), 0) FROM orders"
    ))).one()
    return {
        "users": await _counts(session, User),
        "organizations": await _counts(session, Organization),
        "resources": await _counts(session, CapacityResource),
        "definitions": await _counts(session, CapacityDefinition),
        "offers_published": await _counts(session, Offer, status="published"),
        "categories_in_use": categories_used,
        "recurring_rules": await _counts(session, RecurringAvailability),
        "overrides": await _counts(session, AvailabilityOverride),
        "definition_exceptions": await _counts(session, CapacityDefinitionException),
        "bookings": await _counts(session, Booking),
        "booking_statuses": {k: int(v) for k, v in sorted(statuses.items())},
        "orders": await _counts(session, Order),
        "payments": await _counts(session, Payment),
        "commissions": await _counts(session, Commission),
        "refunds": await _counts(session, Refund),
        "fulfillments": await _counts(session, Fulfillment),
        "reviews": int(rated),
        "review_average": None if rating is None else round(float(rating), 2),
        "demands": await _counts(session, Demand),
        "matches": await _counts(session, Match),
        "disputes": await _counts(session, Dispute),
        "dispute_statuses": {k: int(v) for k, v in sorted(dispute_rows.items())},
        "conversations": await _counts(session, Conversation),
        "notifications": await _counts(session, Notification),
        "promotions": await _counts(session, Promotion, status="active"),
        "paid_cents": int(money[0]),
        "refunded_cents": int(money[1]),
        "refusals": len(ctx.refusals),
        "refund_paths": ctx.refunds,
    }


#: Every §5.5 status the demo must be able to show a row for.
REQUIRED_STATUSES = ("draft", "hold", "confirmed", "in_progress", "disputed", "completed",
                     "expired", "cancelled")


# --------------------------------------------------------------------------- verification
async def _verify_plan(session: AsyncSession) -> dict[str, str]:
    """Choose the accounts by what is actually stored, so the checks cannot be vacuous."""
    provider = (await session.execute(
        select(User.email, Organization.slug)
        .join(OrgStaff, OrgStaff.user_id == User.id)
        .join(Organization, Organization.id == OrgStaff.org_id)
        .join(Booking, Booking.org_id == Organization.id)
        .where(OrgStaff.role == "org_admin", OrgStaff.status == "active",
               Booking.status.in_(("confirmed", "in_progress")),
               Booking.window_start > text("now()"))
        .order_by(Booking.window_start).limit(1))).first()
    if provider is None:
        raise RuntimeError("no organization has an upcoming booking to show the console")
    reviewer = (await session.execute(
        select(User.email).join(Review, Review.reviewer_id == User.id)
        .where(Review.status == "published").limit(1))).scalar_one_or_none()
    demander = (await session.execute(
        select(User.email).join(Demand, Demand.customer_id == User.id).limit(1)
    )).scalar_one_or_none()
    return {"provider": provider[0], "provider_staff": f"staff@{provider[1]}.test",
            "customer": reviewer, "demander": demander,
            "admin": "admin@capacityexchange.test",
            "support": "support@capacityexchange.test"}


async def _verify(settings: Settings) -> None:
    """Read the dataset back over the shipped routes, the same HTTP stack the clients use.

    The job runner is switched off for this: it would otherwise lapse the hold that §9 wants
    shown as 'expiring in two minutes' while the checks are still running.
    """
    import httpx

    from app.main import create_app

    runtime = settings.model_copy(update={"celery_mode": "celery", "rate_limit_per_min": 100000})
    app = create_app(runtime)
    async with app.router.lifespan_context(app):
        maker = app.state.sessionmaker
        async with maker() as session:
            plan = await _verify_plan(session)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test/api/v1",
                                     timeout=60.0) as client:
            tokens = {}
            for label, email in plan.items():
                if email is None:
                    raise RuntimeError(f"the dataset gave no {label} account to verify with")
                tokens[label] = await _login(client, email)

            provider, staff = tokens["provider"], tokens["provider_staff"]
            customer, demander = tokens["customer"], tokens["demander"]
            admin, support = tokens["admin"], tokens["support"]

            await _check(client, provider, "/dashboard/provider",
                         require="upcoming_bookings", label="provider console")
            await _check(client, staff, "/organizations/mine", require="items")
            await _check(client, staff, "/capacities", require="items")
            await _check(client, staff, "/offers?mine=true", require="items")
            await _check(client, staff, "/bookings?provider=true", require="items")
            await _check(client, staff, "/reviews?provider=true")
            await _check(client, staff, "/conversations?provider=true")
            await _check(client, staff, "/demands?provider=true")
            await _check(client, staff, "/matches?provider=true")

            await _check(client, customer, "/dashboard/customer")
            await _check(client, customer, "/bookings?mine=true", require="items")
            await _check(client, customer, "/orders", require="items")
            await _check(client, customer, "/reviews", require="items")
            await _check(client, customer, "/notifications", require="items")
            await _check(client, customer, "/notifications?unread=true")
            await _check(client, customer, "/conversations")
            await _check(client, customer, "/disputes")
            await _check(client, customer, "/catalog/categories", require="items")
            await _check(client, customer, "/offers?city=Berlin", require="items")
            await _check(client, customer, "/offers?q=dock")
            await _check(client, demander, "/demands", require="items")

            await _check(client, admin, "/dashboard/admin")
            await _check(client, admin, "/admin/analytics")
            await _check(client, admin, "/admin/analytics?days=90")
            await _check(client, admin, "/admin/users", require="items")
            await _check(client, admin, "/admin/providers", require="items")
            await _check(client, admin, "/admin/disputes", require="items")
            await _check(client, admin, "/admin/promotions?limit=50", require="items")
            await _check(client, admin, "/admin/audit-logs", require="items")
            await _check(client, admin, "/admin/categories", require="items")
            await _check(client, support, "/admin/disputes", require="items")

            # A search that is live for the requested window must still answer, and the plan
            # screen must render the availability the capacity engine published.
            listed = await _check(client, customer, "/offers?city=Rotterdam", require="items")
            offer_id = listed["items"][0]["id"]
            await _check(client, customer, f"/offers/{offer_id}")
            resource = await _check(client, staff, "/capacities", require="items")
            resource_id = resource["items"][0]["id"]
            units = await _check(client, staff, f"/capacities/{resource_id}/definitions",
                                 require="items")
            today = utcnow().date()
            await _check(client, staff,
                         f"/capacities/{resource_id}/availability?definition_id="
                         f"{units['items'][0]['id']}"
                         f"&from={today.isoformat()}&to={(today + timedelta(days=6)).isoformat()}")


async def _login(client, email: str) -> str:
    response = await client.post("/auth/login",
                                json={"email": email, "password": DEMO_PASSWORD})
    if response.status_code != 200:
        raise RuntimeError(f"login as {email} -> {response.status_code} {response.text[:200]}")
    token = response.json().get("access_token")
    if not token:
        raise RuntimeError(f"login as {email} returned no access_token")
    return str(token)


async def _check(client, token: str, path: str, *, require: str | None = None,
                 label: str | None = None) -> Any:
    response = await client.get(path, headers={"Authorization": f"Bearer {token}"})
    if response.status_code != 200:
        raise RuntimeError(f"GET {path} ({label or 'read'}) -> {response.status_code} "
                           f"{response.text[:300]}")
    body = response.json()
    if require is not None:
        value = body.get(require) if isinstance(body, dict) else body
        if not value:
            raise RuntimeError(f"GET {path}: '{require}' came back empty in {str(body)[:200]}")
    return body


# --------------------------------------------------------------------------- entry point
BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _sync_dsn(async_dsn: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(async_dsn.replace("+psycopg", ""))
    return (f"postgresql://{parsed.username}:{parsed.password}@{parsed.hostname}"
            f":{parsed.port}{parsed.path}")


def _prepare_database(settings: Settings) -> None:
    """Rebuild `public` in the demo database, then bring it up to the head migration (§3).

    Dropping the schema rather than the database is what lets a re-run start from the same empty
    contract the test harness uses.
    """
    from urllib.parse import urlparse

    import psycopg
    from alembic import command
    from alembic.config import Config

    dsn = settings.sqlalchemy_database_url
    target = urlparse(dsn).path.lstrip("/")
    maintenance = urlparse(_sync_dsn(dsn))._replace(path="/postgres").geturl()
    with psycopg.connect(maintenance, autocommit=True) as conn:
        existing = [row[0] for row in conn.execute("SELECT datname FROM pg_database")]
        if target not in existing:
            conn.execute(f'CREATE DATABASE "{target}"')
    with psycopg.connect(_sync_dsn(dsn), autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
        conn.execute("CREATE SCHEMA public")
        conn.execute("GRANT ALL ON SCHEMA public TO CURRENT_USER")
        conn.execute("GRANT CREATE, USAGE ON SCHEMA public TO PUBLIC")
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    cfg.attributes["configure_logger"] = False
    cfg.set_main_option("sqlalchemy.url", dsn)
    command.upgrade(cfg, "head")


async def run(*, verify: bool) -> None:
    """One transaction for the whole dataset: the demo either exists complete or not at all."""
    use_selector_event_loop()
    settings = get_settings_cached()
    engine = build_engine(settings)
    maker = build_sessionmaker(engine)
    timings: list[tuple[str, float]] = []
    started = clock.monotonic()
    try:
        async with maker() as session:
            async with session.begin():
                stage = clock.monotonic
                tenant = await _seed_tenant(session)
                categories = await _seed_categories(session)
                password_hash = hash_password(DEMO_PASSWORD)
                admin, support = await _seed_platform_users(session, tenant, password_hash)
                orgs = await _seed_orgs(session, tenant, password_hash)
                customers = await _seed_customers(session, tenant, password_hash)
                await _seed_capacity(session, orgs)
                offers, by_category = await _load_offers(session, orgs)
                timings.append(("catalog", stage() - started))

                ctx = Ctx(settings=settings, tenant=tenant, admin=admin, support=support,
                          customers=customers, orgs=orgs, offers=offers,
                          by_category=by_category)
                _classify(ctx)
                promotion = await _seed_promotion(session, ctx)

                if await _journeys_exist(session):
                    kept = True
                else:
                    kept = False
                    mark = stage()
                    await _seed_history(session, ctx, settings)
                    timings.append(("history journeys", stage() - mark))
                    for label, phase in (
                        ("live bookings", lambda: _seed_live_bookings(session, ctx, settings)),
                        ("live holds", lambda: _seed_live_holds(session, ctx)),
                        ("hold expiry", lambda: _seed_live_expiry(session, ctx)),
                        ("booking requests", lambda: _seed_live_requests(session, ctx, settings)),
                        ("running sessions", lambda: _seed_live_running(session, ctx, settings)),
                        ("cancellations", lambda: _seed_live_cancellations(session, ctx, settings)),
                        ("disputes", lambda: _seed_live_disputes(session, ctx)),
                        ("exclusion proofs", lambda: _prove_exclusions(session, ctx)),
                        ("demands and matches", lambda: _seed_demands(session, ctx, settings)),
                        ("conversations", lambda: _seed_conversations(session, ctx)),
                        ("notifications", lambda: _seed_notification_noise(session, ctx, settings)),
                    ):
                        mark = stage()
                        await phase()
                        timings.append((label, stage() - mark))

                summary = await _summary(session, ctx)
                missing = [s for s in REQUIRED_STATUSES if not summary["booking_statuses"].get(s)]
                if missing:
                    raise RuntimeError(f"the dataset has no booking in status: {', '.join(missing)}")
                if round(summary["review_average"] or 0, 1) != 4.2:
                    raise RuntimeError(f"review average {summary['review_average']} is not the "
                                       f"4.2 §9 promises")
                plan = await _verify_plan(session) if verify else None
    finally:
        await engine.dispose()

    elapsed = clock.monotonic() - started
    print(f"seed: tenant={tenant.key} categories={categories} offers={summary['offers_published']}"
          f" coupon={promotion.code} journeys={'kept (already present)' if kept else 'rebuilt'}")
    for label, seconds in timings:
        print(f"  {label:<24} {seconds:6.2f}s")
    print(f"  {'total':<24} {elapsed:6.2f}s")
    for key in ("users", "organizations", "resources", "definitions", "offers_published",
                "categories_in_use", "recurring_rules", "overrides", "definition_exceptions",
                "bookings", "orders", "payments", "commissions", "refunds", "fulfillments",
                "reviews", "review_average", "demands", "matches", "disputes", "conversations",
                "notifications", "promotions", "paid_cents", "refunded_cents", "refusals"):
        print(f"  {key:<20} {summary[key]}")
    print(f"  booking statuses   {summary['booking_statuses']}")
    print(f"  dispute statuses     {summary['dispute_statuses']}")
    for entry in summary["refund_paths"]:
        print(f"  refund: {entry['path']:<24} {entry['cents']:>8} cents"
              f"  order={entry.get('order_number')}")
    for refusal in ctx.refusals:
        print(f"  refused: {refusal['proof']:<22} {refusal['error']}")
    if elapsed >= 60:
        raise RuntimeError(f"the demo took {elapsed:.1f}s; §9 promises under 60")
    if plan:
        await _verify(settings)
        print(f"verify: {plan['provider']} / {plan['customer']} / {plan['admin']} all read back "
              f"over the shipped routes")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the CONTRACTS §9 demo dataset on the API's own database.")
    parser.add_argument("--reset", action="store_true",
                        help="drop the public schema, migrate, and rebuild the journeys against "
                             "the current clock")
    parser.add_argument("--verify", action="store_true",
                        help="read the result back through the real routes and their auth")
    args = parser.parse_args(argv)
    use_selector_event_loop()
    if args.reset:
        # Alembic's online mode drives its own loop, so the schema rebuild happens before the
        # seeding transaction opens.
        _prepare_database(get_settings_cached())
    asyncio.run(run(verify=args.verify))
    return 0


if __name__ == "__main__":
    sys.exit(main())
