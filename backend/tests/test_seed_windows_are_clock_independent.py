"""The seed's refund-band window search, over every hour and weekday a runner can start at.

`_clock_window` is a pure function of (now, catalog), and it is what made the live gate red on one
runner and green on the next: the seeded availability rules run Monday..Saturday, so the distance
from `now` a refund band needs is not always on the calendar. The pool here is the catalog the
script publishes - each quantity-counted category at its posted opening hours, one listing in five
on the 100/50/0 policy, exactly as `seed.py` assigns them.
"""
from __future__ import annotations

import importlib.util
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

MONDAY = datetime(2026, 10, 12, tzinfo=timezone.utc)  # the week under test is walked from here
HOURS_LONG = 2  # the seed pins two-hour windows: quantity listings carry no minimum duration
#: Every weekday and hour a runner can start at: the completeness check is the one that has to see
#: the whole week, because the bug it guards was a specific Friday-evening gap.
CLOCKS = [(day, hour) for day in range(7) for hour in range(24)]
#: The other three properties are about one window, not about the search space, so a spread of the
#: week plus the Friday-evening edge is enough and keeps the file off the critical path.
CLOCKS_SAMPLE = [(day, hour) for day in range(7) for hour in (0, 4, 9, 13, 17, 20)] + [
    (4, 21), (4, 22), (4, 23), (5, 0)]


def _load_seed():
    """`scripts/seed.py` is a script, not a package module; its dataclasses need sys.modules."""
    path = Path(__file__).resolve().parents[1] / "scripts" / "seed.py"
    spec = importlib.util.spec_from_file_location("capacity_seed_script", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["capacity_seed_script"] = module
    spec.loader.exec_module(module)
    return module


seed = _load_seed()


@pytest.fixture(scope="module")
def catalog() -> list[SimpleNamespace]:
    offers = []
    for index, (key, style) in enumerate(sorted(seed.STYLE.items())):
        if style["mode"] != "quantity":
            continue
        for fifth in range(5):
            offers.append(SimpleNamespace(
                id=uuid.UUID(int=index * 5 + fifth), meta={"category_key": key},
                min_lead_time_minutes=style["lead"], requires_payment=True,
                cancellation_policy=(seed.POLICY_WIDE if fifth == 0 else seed.POLICY_NARROW)))
    return offers


def _a_window_exists(now: datetime, catalog, low: int, high: int, want_pct: int,
                     spent: set[uuid.UUID]) -> bool:
    """Is there any legal window in the band at all, found without the search's ordering?

    The oracle only shares the *legality* rules with the search (`_fits`, the lead time, the
    policy); it does not share how candidates are enumerated, which is the part that was wrong.
    """
    floor = now + timedelta(hours=low)
    start = floor.replace(minute=0, second=0, microsecond=0)
    if start < floor:
        start += timedelta(hours=1)
    limit = (now + timedelta(hours=high)).replace(minute=0, second=0, microsecond=0)
    while start <= limit:
        end = start + timedelta(hours=HOURS_LONG)
        if start.weekday() != 6 and start.hour <= 23 - HOURS_LONG:
            hours_before = (start - now).total_seconds() / 3600
            for offer in catalog:
                if offer.id in spent:
                    continue
                if not seed._fits(seed.STYLE[offer.meta["category_key"]], start, end):
                    continue
                if (start - now).total_seconds() / 60 < offer.min_lead_time_minutes:
                    continue
                if seed.booking_svc.refund_pct_for(offer.cancellation_policy,
                                                   hours_before) == want_pct:
                    return True
        start += timedelta(hours=1)
    return False


@pytest.mark.parametrize(("day", "hour"), CLOCKS)
def test_the_search_gives_up_only_where_the_calendar_has_nothing(catalog, day, hour):
    """A band is unsatisfiable exactly when no legal window grades that way - never by ordering.

    The first version tried one snapped hour per offset, so from Friday 22:37 it rejected
    Saturday 21:00 for sitting 23.4 hours out, never tried the next hour, and reported a band the
    listings plainly served.
    """
    now = (MONDAY + timedelta(days=day)).replace(hour=hour, minute=17)
    spent: set[uuid.UUID] = set()
    for want_pct, low, high in seed.REFUND_BANDS:
        exists = _a_window_exists(now, catalog, low, high, want_pct, set(spent))
        try:
            seed._clock_window(now, catalog, min_hours=low, max_hours=high, used=spent,
                               want_pct=want_pct, hours_long=HOURS_LONG)
            placed = True
        except RuntimeError:
            placed = False
        assert placed == exists, f"{now:%a %H:%M}: band {want_pct}% placed={placed} " \
                                 f"but a legal window exists={exists}"


@pytest.mark.parametrize(("day", "hour"), CLOCKS_SAMPLE)
def test_every_window_it_hands_back_is_bookable_and_graded_as_claimed(catalog, day, hour):
    """The search may be opportunistic about which window it picks, not about what it claims."""
    now = (MONDAY + timedelta(days=day)).replace(hour=hour, minute=17)
    spent: set[uuid.UUID] = set()
    placed = 0
    for want_pct, low, high in seed.REFUND_BANDS:
        before = set(spent)
        try:
            offer, start, end = seed._clock_window(now, catalog, min_hours=low, max_hours=high,
                                                   used=spent, want_pct=want_pct,
                                                   hours_long=HOURS_LONG)
        except RuntimeError:
            continue
        placed += 1
        assert spent - before == {offer.id}, "a placed window must spend exactly one listing"
        assert start.weekday() != 6, "the seeded rules do not run on Sunday"
        assert end - start == timedelta(hours=HOURS_LONG)
        assert seed._fits(seed.STYLE[offer.meta["category_key"]], start, end)
        hours_before = (start - now).total_seconds() / 3600
        assert low <= hours_before <= high
        assert (start - now).total_seconds() / 60 >= offer.min_lead_time_minutes
        assert seed.booking_svc.refund_pct_for(offer.cancellation_policy,
                                               hours_before) == want_pct
    assert spent and len(spent) == placed, "each placed band spent its own listing"


@pytest.mark.parametrize(("day", "hour"), CLOCKS_SAMPLE)
def test_a_grade_above_zero_is_demonstrable_at_any_clock(catalog, day, hour):
    """The cancellation phase refuses to write a dataset with no graded refund in it."""
    now = (MONDAY + timedelta(days=day)).replace(hour=hour, minute=17)
    want_pct, low, high = seed.REFUND_BANDS[0]
    offer, start, _end = seed._clock_window(now, catalog, min_hours=low, max_hours=high,
                                            used=set(), want_pct=want_pct)
    assert seed.booking_svc.refund_pct_for(
        offer.cancellation_policy, (start - now).total_seconds() / 3600) == want_pct


@pytest.mark.parametrize(("day", "hour"), CLOCKS_SAMPLE)
def test_the_zero_grade_always_has_a_real_path(catalog, day, hour):
    """Either a window inside 24 hours, or a session that already closed: both owe nothing back."""
    now = (MONDAY + timedelta(days=day)).replace(hour=hour, minute=17)
    spent: set[uuid.UUID] = set()
    want_pct, low, high = seed.REFUND_BANDS[2]
    try:
        offer, start, end = seed._clock_window(now, catalog, min_hours=low, max_hours=high,
                                               used=spent, want_pct=want_pct)
    except RuntimeError:
        offer, start, end = seed._late_window(now, [o for o in catalog if o.requires_payment],
                                              spent)
        assert end <= now, "the fallback must be a window that has closed"
    assert start.weekday() != 6, "the seeded rules do not run on Sunday"
    assert seed.booking_svc.refund_pct_for(
        offer.cancellation_policy, (start - now).total_seconds() / 3600) == 0
