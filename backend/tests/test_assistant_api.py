"""§8 `/ai/*`: the assistant endpoints answer from real rows, not from fixtures of themselves.

The point of these routes is that a provider or a customer can hand over prose and get
something true about *this* database back — the seeded category registry, the offers that
are actually published, the availability the capacity engine expands. So each test seeds
through the shipped routes and asserts the suggestion against the numbers that made it.
"""
from __future__ import annotations

from datetime import date, timedelta

from tests.test_auth_api import auth, register
from tests.test_booking_api import END, START, hold, live_offer
from tests.test_capacity_api import DAY, DOW, category_id_of, make_provider
from tests.test_commerce_api import book_offer
from tests.test_dashboard_api import provider_screen
from tests.test_reviews_disputes_api import pay

PROSE = "need a cold storage unit for 12 pallets in Oslo next monday morning under $400"
DRAFT_TEXT = (
    "Bright 40 sqm meeting room with 12 seats and a loading dock, "
    "open mon-fri 9am-6pm, $60/hour. Wheelchair accessible and staffed on weekdays."
)


async def parse(client, tokens, text: str) -> dict:
    resp = await client.post("/ai/parse-search", headers=auth(tokens), json={"text": text})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def insights(client, tokens, **params) -> dict:
    resp = await client.get("/ai/utilization-insights", headers=auth(tokens), params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def price(client, tokens, **body) -> dict:
    resp = await client.post("/ai/price-suggest", headers=auth(tokens), json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ------------------------------------------------------------------ semantic search


async def test_prose_becomes_the_filters_the_offer_search_already_accepts(client):
    buyer = await register(client, "ai.buyer@example.test")
    parsed = await parse(client, buyer, PROSE)

    assert parsed["category_key"] == "storage_space"
    assert parsed["category_id"] == await category_id_of(client, buyer, "storage_space")
    assert parsed["city"] == "Oslo" and parsed["country"] == "NO"
    assert parsed["quantity"] == 12 and parsed["unit"] == "pallet"
    assert parsed["budget_max_cents"] == 40000 and parsed["currency"] == "USD"
    assert parsed["constraints"] == ["temperature_controlled"], "'cold storage' is a feature"
    # "next monday" from today's date, in the morning band: the parser resolves relative
    # days rather than guessing, so the window must land on a Monday.
    start, end = parsed["window_start"], parsed["window_end"]
    assert date.fromisoformat(start[:10]).weekday() == 0
    assert start.endswith("08:00:00Z") and end.endswith("12:00:00Z")
    assert parsed["confidence"] > 0.5, "a well-parsed sentence should not sound unsure"

    # The filters are usable where they land: the same search route accepts them.
    listing = await client.get("/offers", headers=auth(buyer),
                               params={"category": parsed["category_id"], "city": "Oslo"})
    assert listing.status_code == 200 and listing.json()["total"] == 0


async def test_the_parser_says_what_it_could_not_understand(client):
    buyer = await register(client, "ai.vague@example.test")
    parsed = await parse(client, buyer, "somewhere quiet with a view, soonish")
    assert parsed["category_key"] is None and parsed["category_id"] is None
    assert parsed["city"] is None and parsed["quantity"] is None
    assert parsed["confidence"] < 0.4
    assert parsed["unparsed_fragments"], "an unparseable request must show its loose ends"

    empty = await client.post("/ai/parse-search", headers=auth(buyer), json={"text": ""})
    assert empty.status_code == 422
    assert (await client.post("/ai/parse-search", json={"text": PROSE})).status_code == 401


# ------------------------------------------------------------------ listing draft


async def test_a_description_becomes_a_draft_without_inventing_anything(client):
    provider = await make_provider(client, "ai.provider@example.test")
    resp = await client.post("/ai/listing-draft", headers=auth(provider),
                             json={"raw_text": DRAFT_TEXT})
    assert resp.status_code == 200, resp.text
    draft = resp.json()

    assert draft["category_key"] == "meeting_room"
    assert draft["category_id"] == await category_id_of(client, provider)
    assert draft["title"] == "12-person Meeting room"
    assert draft["unit_label"] == "hour" and draft["suggested_unit_amount_cents"] == 6000
    assert draft["currency"] == "USD"

    slots = {(s["dow"], s["start_time"], s["end_time"], s["quantity"])
             for s in draft["suggested_availabilities"]}
    assert slots == {(d, "09:00", "18:00", 12) for d in range(5)}, "'mon-fri 9am-6pm' is 5 slots"

    attrs = draft["attributes"]
    assert attrs["area_square_meters"] == 40.0 and attrs["capacity_persons"] == 12
    assert attrs["loading_dock"] is True and attrs["wheelchair_accessible"] is True
    assert attrs["staffed"] is True

    # Nothing in the text named a city, and the draft must not guess one.
    assert draft["missing_fields"] == ["location"]
    assert "Tehran" not in draft["description"] and draft["description"].startswith("Bright 40")


async def test_a_thin_description_lists_every_field_left_to_the_provider(client):
    provider = await make_provider(client, "ai.thin@example.test")
    resp = await client.post("/ai/listing-draft", headers=auth(provider),
                             json={"raw_text": "a van sometimes available"})
    draft = resp.json()
    assert set(draft["missing_fields"]) == {"availability", "unit_amount_cents", "location",
                                            "description"}
    assert draft["suggested_availabilities"] == []

    unknown = await client.post("/ai/listing-draft", headers=auth(provider),
                                json={"raw_text": "space", "category_key": "time_machine"})
    assert unknown.status_code == 400
    assert unknown.json()["error"]["code"] == "validation_error"


# ------------------------------------------------------------------ pricing


async def test_a_price_band_comes_from_published_offers_and_nothing_else(client):
    provider = await make_provider(client, "ai.priced@example.test")
    mine = await live_offer(client, provider, quantity=2)
    for cents in (4000, 7000, 9000):
        other = await make_provider(client, f"ai.other{cents}@example.test")
        await live_offer(client, other, quantity=2, unit_amount_cents=cents)
    # A draft at an absurd price must not move the band: only published offers are compared.
    hidden = await make_provider(client, "ai.hidden@example.test")
    secret = await live_offer(client, hidden, quantity=2, unit_amount_cents=9_000_00)
    await client.post(f"/offers/{secret['id']}/pause", headers=auth(hidden))

    band = await price(client, provider, offer_id=mine["id"])
    assert band["method"] == "comparables"
    assert band["comparable_count"] == 3, "the caller's own offer is not its comparable"
    assert band["suggested_min_cents"] <= 7000 <= band["suggested_max_cents"]
    assert "meeting_room" in band["rationale"] and band["confidence"] > 0

    # The category genuinely has no market for this shape, and says so.
    cold = await price(client, provider, category_key="hospitality_room", currency="USD")
    assert cold["method"] == "cold_start_floor" and cold["comparable_count"] == 0
    assert cold["suggested_min_cents"] <= cold["suggested_max_cents"]

    assert (await client.post("/ai/price-suggest", headers=auth(provider),
                              json={})).status_code == 400
    bad_id = await client.post("/ai/price-suggest", headers=auth(provider),
                               json={"offer_id": "not-a-uuid"})
    assert bad_id.status_code == 400
    assert bad_id.json()["error"]["message"] == "`offer_id` must be a UUID"
    missing = await client.post("/ai/price-suggest", headers=auth(provider),
                                json={"offer_id": "00000000-0000-4000-8000-000000000000"})
    assert missing.status_code == 404


async def test_pricing_widens_its_scope_instead_of_quoting_nothing(client):
    provider = await make_provider(client, "ai.wide@example.test")
    for cents in (5000, 5500, 6500):
        other = await make_provider(client, f"ai.market{cents}@example.test", name="Owner")
        await live_offer(client, other, quantity=2, unit_amount_cents=cents)

    local = await price(client, provider, category_key="meeting_room", city="Tehran")
    assert local["method"] == "comparables" and local["comparable_count"] == 3
    assert " in Tehran" in local["rationale"]

    # A city with no listings of its own must not answer "no data" when the country
    # does: the scope widens, and the rationale names the scope it actually used.
    wider = await price(client, provider, category_key="meeting_room",
                        city="Shiraz", country="IR")
    assert wider["method"] == "comparables" and wider["comparable_count"] == 3
    assert " in IR" in wider["rationale"]


async def test_a_price_band_names_the_billing_unit_it_compared(client):
    """Widening may drop the capacity mode, but never the money axis in silence.

    A per-pallet listing priced from per-hour listings is a different number, so the
    only acceptable answer either keeps `unit_label` or says the band came from every
    billing unit in the category.
    """
    provider = await make_provider(client, "ai.units@example.test")
    for cents in (5000, 5500, 6500):
        other = await make_provider(client, f"ai.unit{cents}@example.test")
        await live_offer(client, other, quantity=2, unit_amount_cents=cents)

    kept = await price(client, provider, category_key="meeting_room", currency="USD",
                       unit_label="hour", capacity_mode="quantity")
    assert kept["method"] == "comparables" and kept["comparable_count"] == 3
    assert "meeting_room per hour" in kept["rationale"], "the sample is hour-priced"

    widened = await price(client, provider, category_key="meeting_room", currency="USD",
                          unit_label="pallet")
    assert widened["method"] == "comparables"
    assert "any billing unit" in widened["rationale"], "no pallet price exists to compare"
    assert " per pallet" not in widened["rationale"]


# ------------------------------------------------------------------ utilization


async def test_insights_count_the_hours_nobody_booked(client):
    provider = await make_provider(client, "ai.used@example.test")
    offer = await live_offer(client, provider, quantity=2)
    buyer = await register(client, "ai.customer@example.test")
    await book_offer(client, buyer, offer["id"])

    window = {"from": DAY, "to": DAY}
    screen = await insights(client, provider, **window)
    assert screen["total"] == 1 and screen["truncated"] is False
    row = screen["items"][0]
    assert row["name"] == "Hour slot" and row["category_key"] == "meeting_room"
    # The rule is 09:00-18:00 at quantity 2 (18 unit-hours); 10:00-12:00 x 1 consumed 2.
    assert row["utilization_pct"] == round(2 / 18 * 100, 1)
    assert row["total_slots"] == 9 and row["booked_slots"] == 2
    assert screen["window"] == {"from": f"{DAY}T00:00:00Z", "to": f"{_next_day(DAY)}T00:00:00Z"}

    long_window = {"from": _shift(DAY, -14), "to": _shift(DAY, 14)}
    quiet = await insights(client, provider, **long_window)
    idle = quiet["idle_windows"]
    assert idle, "nine published hours with two booked must leave recurring idle time"
    on_booking_day = {w["start_time"] for w in idle if w["dow"] == DOW}
    assert on_booking_day == {"12:00", "15:00"}, (
        "09:00-12:00 holds the two booked hours, so it is not idle")
    # The rule runs one weekday only, valid from DAY for 30 days: inside this window
    # that weekday came up three times, and the insight must report three — not the
    # four the calendar length implies.
    assert {w["occurrences"] for w in idle} == {3}
    assert quiet["items"][0]["total_slots"] == 9
    assert quiet["items"][0]["utilization_pct"] == round(2 / (18 * 3) * 100, 1)
    assert [w["definition_id"] for w in idle] == [quiet["items"][0]["definition_id"]] * len(idle)

    assert quiet["demand_counts"] == [], "nobody posted a request in this window"


async def test_insights_stay_inside_the_organization_that_owns_them(client):
    provider = await make_provider(client, "ai.mine@example.test")
    await live_offer(client, provider, quantity=2)
    rival = await make_provider(client, "ai.rival@example.test", name="Rival")
    await live_offer(client, rival, quantity=2, name="Rival Room")
    buyer = await register(client, "ai.none@example.test")

    mine = (await insights(client, provider, **{"from": DAY, "to": DAY}))["items"]
    assert len(mine) == 1
    theirs = (await insights(client, rival, **{"from": DAY, "to": DAY}))["items"]
    assert len(theirs) == 1 and theirs[0]["definition_id"] != mine[0]["definition_id"], \
        "each org sees its own rows, not the other's"

    stealing = await client.get("/ai/utilization-insights", headers=auth(rival),
                                params={"org_id": _org_of(provider), "from": DAY, "to": DAY})
    assert stealing.status_code == 403
    anonymous = await client.get("/ai/utilization-insights", params={"from": DAY, "to": DAY})
    assert anonymous.status_code == 401
    assert (await client.get("/ai/utilization-insights", headers=auth(buyer))).status_code == 400


# ------------------------------------------------------------------ copilot


async def test_the_copilot_paragraph_is_the_numbers_read_back(client):
    provider = await make_provider(client, "ai.copilot@example.test")
    offer = await live_offer(client, provider, quantity=2)
    buyer = await register(client, "ai.payer@example.test")

    quiet = (await client.get("/ai/copilot", headers=auth(provider))).json()
    assert "1 active capacity definition" in quiet["summary_text"]
    assert quiet["next_7d_bookings"] == 0 and quiet["revenue_last_30d_cents"] == 0
    assert quiet["currency"] == "USD" and quiet["at_risk_holds"] == []
    assert "No recurring idle block stands out yet" in quiet["summary_text"], (
        "one week of published hours is not a pattern, and the copilot says so")

    booking = await book_offer(client, buyer, offer["id"])
    held = await hold(client, buyer, offer["id"], start=START, end=END)
    await pay(client, buyer, booking)

    busy = (await client.get("/ai/copilot", headers=auth(provider))).json()
    assert busy["next_7d_bookings"] == 2, "the confirmed booking and the live hold"
    assert "USD 100.00" in busy["summary_text"]
    assert busy["revenue_last_30d_cents"] == 10000

    # The two money axes, each labelled as itself: this order is paid today for a
    # service on DAY (three days out), so the cash-dated briefing has collected it
    # while the service-dated dashboard over the same trailing 30 days has not.
    # The dashboard still counts it on the day the work is due.
    trailing = {"from": (date.today() - timedelta(days=30)).isoformat(),
                "to": date.today().isoformat()}
    assert (await provider_screen(client, provider, **trailing))["revenue_cents"] == 0
    assert (await provider_screen(client, provider, **{"from": DAY, "to": DAY}))["revenue_cents"] \
        == 10000

    assert [h["booking_id"] for h in busy["at_risk_holds"]] == [held["id"]]
    assert "1 hold(s) expire within 24 hours" in busy["summary_text"]
    idle_row = busy["top_idle_capacity"][0]
    assert idle_row["name"] == "Hour slot" and idle_row["utilization_pct"] > 0
    # This org has exactly one definition, so the paragraph may name it once at most,
    # and the percentage it reads out is the one the ranking row carries. A generated
    # summary is worthless when it disagrees with its own payload.
    assert busy["summary_text"].count("Hour slot") == 1
    assert f"Hour slot at {idle_row['utilization_pct']}%" in busy["summary_text"]


def _shift(day: str, days: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=days)).isoformat()


def _next_day(day: str) -> str:
    return _shift(day, 1)


def _org_of(tokens: dict) -> str:
    org = tokens["user"]["active_org_id"]
    assert org, "the helper expects a provider session with an active organization"
    return org
