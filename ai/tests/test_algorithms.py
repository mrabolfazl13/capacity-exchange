"""capacity_ai's own tests: literals in, numbers out, no database and no app.

`backend/tests/test_assistant_api.py` proves the routes answer from real rows. This file
proves the rules themselves, including the shapes a seeded calendar cannot produce: a
price list that is really two markets, a Friday that only appeared twice, a listing that
states no price.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from capacity_ai.db import CategoryDemand, ComparableOffer, DefinitionUsage, SlotBucket
from capacity_ai.insights import (
    MIN_WEEKS_OBSERVED,
    demand_signal,
    idle_windows,
    measure,
    summarize_copilot,
    utilization_pct,
)
from capacity_ai.listing_draft import draft_listing
from capacity_ai.pricing import suggest_price
from capacity_ai.semantic_query import CategorySpec, parse_query

#: 2026-10-07 is a Wednesday, so "next monday" below resolves to 2026-10-12.
NOW = datetime(2026, 10, 7, 9, 0, tzinfo=timezone.utc)
PERIOD_START = NOW - timedelta(days=28)
PERIOD_END = NOW + timedelta(days=4)


def _usage(buckets: list[SlotBucket], *, weeks: int, name: str = "Hour slot") -> DefinitionUsage:
    return DefinitionUsage(
        definition_id=f"definition-{name}",
        name=name,
        category_key="meeting_room",
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        weeks_observed=weeks,
        capacity_unit_hours=round(sum(b.capacity_unit_hours for b in buckets), 4),
        booked_unit_hours=round(sum(b.booked_unit_hours for b in buckets), 4),
        buckets=tuple(buckets),
    )


def _friday_morning(*, occurrences: int = 4, thin_hour: bool = False,
                    booked_hour: int | None = None) -> DefinitionUsage:
    """Friday 09:00-12:00 published at quantity 2, with knobs for the edge cases."""
    buckets = []
    for hour in (9, 10, 11):
        count = 2 if (thin_hour and hour == 11) else occurrences
        booked = 2.0 * count if hour == booked_hour else 0.0
        buckets.append(SlotBucket(dow=4, hour=hour, capacity_unit_hours=2.0 * count,
                                  booked_unit_hours=booked, occurrences=count))
    return _usage(buckets, weeks=occurrences)


# --------------------------------------------------------------------------- pricing


def test_the_band_is_the_middle_half_of_the_sample():
    sample = [ComparableOffer(cents, "USD") for cents in (4000, 5000, 6000, 7000, 20000)]
    suggestion = suggest_price(sample, currency="USD", region="Oslo", scope="meeting_room")
    assert (suggestion.method, suggestion.comparable_count) == ("comparables", 5)
    assert (suggestion.suggested_min_cents, suggestion.suggested_max_cents) == (5000, 7000)
    assert "Based on 5 published offers for meeting_room in Oslo" in suggestion.rationale


def test_a_two_market_sample_cannot_drag_the_suggestion_down():
    # The 25th percentile here is USD 30.00 while the middle of the market is USD
    # 100.00; quoting the percentile would recommend a price that reads as an error,
    # so the band is floored at 80% of the median instead.
    sample = [ComparableOffer(cents, "USD") for cents in (2000, 3000, 10000, 12000, 14000)]
    suggestion = suggest_price(sample, currency="USD")
    assert suggestion.suggested_min_cents == 8000
    assert suggestion.suggested_max_cents == 12000


def test_too_few_comparables_is_a_floor_and_says_so():
    suggestion = suggest_price([ComparableOffer(6000, "USD")], currency="USD", scope="parking",
                               cold_start_floor_cents=5000)
    assert suggestion.method == "cold_start_floor" and suggestion.comparable_count == 1
    assert (suggestion.suggested_min_cents, suggestion.suggested_max_cents) == (5000, 7500)
    assert "too few to describe a market" in suggestion.rationale
    assert suggestion.confidence < 0.3


def test_a_free_listing_is_not_a_comparable():
    sample = [ComparableOffer(cents, "USD") for cents in (0, 5000, 6000, 7000)]
    suggestion = suggest_price(sample, currency="USD")
    assert suggestion.comparable_count == 3
    assert (suggestion.suggested_min_cents, suggestion.suggested_max_cents) == (5500, 6600)


def test_confidence_tracks_how_much_the_sample_agrees():
    tight = suggest_price([ComparableOffer(c, "USD") for c in (5000, 5000, 5000, 5000)],
                          currency="USD")
    wide = suggest_price([ComparableOffer(c, "USD") for c in (3000, 3000, 30000, 30000)],
                         currency="USD")
    assert wide.confidence < tight.confidence
    assert 0.0 < wide.confidence <= 0.9, "a suggestion never claims more than 0.9"


# --------------------------------------------------------------------------- insights


def test_utilization_is_a_capped_percentage_of_published_hours():
    assert utilization_pct(18.0, 2.0) == 11.1
    assert utilization_pct(0.0, 5.0) == 0.0
    assert utilization_pct(10.0, 12.0) == 100.0, "overbooked reads as full, not as 120%"


def test_a_recurring_empty_block_is_reported_with_its_real_depth():
    windows = idle_windows([_friday_morning()])
    assert [(w.dow, w.start_time, w.end_time, w.occurrences) for w in windows] == [
        (4, "09:00", "12:00", 4)]
    assert "never booked" in windows[0].note


def test_the_block_is_as_deep_as_its_least_observed_hour():
    # Claiming four weeks because the calendar was four weeks long would upgrade a
    # two-Friday sample into a pattern; the depth is the thinnest hour in the block.
    assert [w.occurrences for w in idle_windows([_friday_morning(thin_hour=True)])] == [2]


def test_one_booked_hour_breaks_the_pattern():
    assert idle_windows([_friday_morning(booked_hour=10)]) == []


def test_two_idle_hours_out_of_three_is_not_a_block():
    buckets = [SlotBucket(4, 9, 8.0, 0.0, 4), SlotBucket(4, 10, 8.0, 0.0, 4)]
    assert idle_windows([_usage(buckets, weeks=4)]) == []


def test_a_short_history_is_called_short_instead_of_quiet():
    assert idle_windows([_friday_morning(occurrences=1)], min_weeks=MIN_WEEKS_OBSERVED) == []


def test_measure_counts_published_slots_not_calendar_days():
    report = measure(_friday_morning(booked_hour=10))
    assert (report.booked_slots, report.total_slots) == (1, 3)
    assert report.utilization_pct == round(8 / 24 * 100, 1)


def test_demand_wording_separates_a_new_category_from_a_rising_one():
    assert "against none in the previous period" in \
        demand_signal(CategoryDemand("parking", 3, 0)).trend_note
    assert "up 25%" in demand_signal(CategoryDemand("parking", 5, 4)).trend_note
    assert "down 50%" in demand_signal(CategoryDemand("parking", 2, 4)).trend_note
    assert "unchanged" in demand_signal(CategoryDemand("parking", 4, 4)).trend_note
    assert demand_signal(CategoryDemand("parking", 0, 0)).trend_note.startswith(
        "No open requests")


def test_the_copilot_paragraph_reads_back_its_own_payload():
    summary = summarize_copilot(
        usage=[_usage([SlotBucket(0, 9, 18.0, 2.0, 4)], weeks=4)],
        next_7d_bookings=3,
        at_risk_holds=[{"booking_id": "b1"}],
        revenue_last_30d_cents=123456,
        currency="USD",
        org_name="North Court",
    )
    text = summary.summary_text
    assert text.startswith("North Court has 1 active capacity definition(s).")
    assert "Next 7 days: 3 booking(s)." in text
    assert "1 hold(s) expire within 24 hours" in text
    assert "Collected in the last 30 days: USD 1,234.56." in text
    assert summary.next_7d_bookings == 3 and summary.revenue_last_30d_cents == 123456
    row = summary.top_idle_capacity[0]
    assert row["utilization_pct"] == 11.1
    assert f"Hour slot at {row['utilization_pct']}%" in text
    assert "No recurring idle block stands out yet" in text, (
        "the booked hour is not an idle pattern, and the paragraph should not invent one")


def test_the_copilot_suggests_an_action_only_about_a_gap_it_counted():
    summary = summarize_copilot(usage=[_friday_morning()], currency="USD")
    assert "Suggested action: Friday 09:00-12:00 on Hour slot" in summary.summary_text
    assert "stayed empty every time" in summary.summary_text
    assert "No recurring idle block" not in summary.summary_text


# --------------------------------------------------------------------------- listing draft


def test_the_draft_fills_in_only_what_the_provider_wrote():
    draft = draft_listing(
        "Bright 40 sqm meeting room with 12 seats, open mon-fri 9am-6pm, $60/hour. "
        "Wheelchair accessible and staffed on weekdays.", now=NOW)
    assert draft.category_key == "meeting_room"
    assert draft.suggested_unit_amount_cents == 6000 and draft.currency == "USD"
    # A stated price decides how the room is sold: hours. The 40 m² stays an attribute.
    assert draft.unit_label == "hour"
    assert draft.attributes["area_square_meters"] == 40.0
    assert draft.attributes["capacity_persons"] == 12
    assert draft.attributes["wheelchair_accessible"] is True
    # Seats, not the floor area, are what one slot publishes.
    assert {(p.dow, p.start_time, p.end_time, p.quantity)
            for p in draft.suggested_availabilities} == {
                (dow, "09:00", "18:00", 12) for dow in range(5)}
    assert draft.title == "12-person Meeting room"
    assert set(draft.missing_fields) == {"location"}, "the text never said where"


def test_a_draft_without_a_price_asks_for_one_instead_of_guessing():
    draft = draft_listing("A quiet space in Oslo, open Mondays 9am-5pm.", now=NOW)
    assert draft.suggested_unit_amount_cents is None
    assert "unit_amount_cents" in draft.missing_fields
    assert "price_stated_by_provider" not in draft.attributes
    assert "Budget mentioned" not in draft.description


# --------------------------------------------------------------------------- semantic query


PROSE = ("need a cold storage unit for 12 pallets in Oslo next monday morning "
         "under $400")


def test_prose_becomes_filters_and_keeps_the_date_it_resolved():
    parsed = parse_query(PROSE, NOW)
    assert parsed.category_key == "storage_space"
    assert (parsed.city, parsed.country) == ("Oslo", "NO")
    assert (parsed.quantity, parsed.unit) == (12, "pallet")
    assert (parsed.budget_max_cents, parsed.currency) == (40000, "USD")
    assert parsed.window_start == datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc)
    assert parsed.window_end == datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
    assert parsed.constraints == ["temperature_controlled"]
    assert parsed.confidence > 0.7


def test_a_category_phrase_does_not_swallow_the_constraint_inside_it():
    parsed = parse_query("cold storage unit", NOW)
    assert parsed.category_key == "storage_space"
    assert "temperature_controlled" in parsed.constraints, (
        "'cold' is a requirement the provider must still be able to filter on")


def test_a_live_catalog_extends_the_vocabulary_instead_of_replacing_it():
    live = [CategorySpec("pop_up_store", "Pop-up store", ("pop up", "pop-up"))]
    assert parse_query("pop up in Berlin", NOW, live).category_key == "pop_up_store"
    assert parse_query("boardroom for 8 people", NOW, live).category_key == "meeting_room"


def test_relative_days_resolve_against_the_supplied_clock():
    monday = parse_query("meeting room next monday", NOW).window_start
    assert monday is not None and monday.weekday() == 0
    assert monday.date() == date(2026, 10, 12)
    from_a_monday = parse_query("meeting room next monday",
                               datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc)).window_start
    assert from_a_monday is not None and from_a_monday.date() == date(2026, 10, 12)


def test_what_the_parser_cannot_understand_is_returned_not_dropped():
    parsed = parse_query("zythos qux for gleeb", NOW)
    assert parsed.category_key is None
    assert parsed.confidence <= 0.3, "no signal, so no confidence either"
    assert parsed.unparsed_fragments, "the caller has to be able to say what it missed"
