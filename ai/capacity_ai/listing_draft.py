"""Listing generator: raw provider prose -> a structured draft (docs/AI_SPEC.md).

Deterministic on purpose. The draft fills in what the text actually said and
lists the rest under ``missing_fields``, so the publish form always shows the
provider which fields are their decision rather than ours. It never invents an
address, a price or an opening hour.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Sequence

from capacity_ai.semantic_query import (
    CONSTRAINT_KEYWORDS,
    CategorySpec,
    ParsedQuery,
    parse_query,
)

# "12 m2", "120 square meters", "about 40 sqm"
_AREA_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(m2|sqm|square\s*meters?|square\s*metres?)\b")
# "20 seats", "room for 12 people", "seating for 20"
_PEOPLE_RE = re.compile(r"(\d+)\s*(?:seats?|people|persons?|pax|desks?|stations?)\b")
# "9am-6pm", "09:00-17:00", "9 to 5", "9 - 17"
_RANGE_RE = re.compile(
    r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:-|–|—|to|until|till)\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b"
)
# "$60/hour", "60 EUR per hour", "€40 an hour"
_PRICE_RE = re.compile(
    r"(?:[\$€£]\s*)?(\d+(?:[.,]\d{1,2})?)\s*(?:[\$€£])?\s*(?:/|per|an?)\s*"
    r"(hour|hr|day|week|month|night|pallet|sqm|m2|session)\b"
)
# "mon-fri", "Monday to Friday", "sat and sun"
_DOW_RE = re.compile(
    r"\b(mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)(?:day|nesday|rsday|urday|unday)?\b"
)
_CURRENCY_BY_TOKEN = {"$": "USD", "€": "EUR", "£": "GBP"}
_UNIT_BY_WORD = {
    "hour": "hour", "hr": "hour", "session": "hour",
    "day": "day", "week": "week", "month": "month", "night": "night",
    "pallet": "pallet", "sqm": "square_meter", "m2": "square_meter",
}
_DOW_BY_ABBREV = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
# Units that read naturally in front of a listing name ("12 pallet cold storage").
_COUNTED_UNITS = {"pallet", "seat", "desk", "room", "truck", "van", "forklift", "machine",
                  "appointment", "vehicle_slot"}
# Units that describe how the thing is sold, so they belong after "by".
_PER_UNIT_TITLE = {"hour": "hour", "day": "day", "week": "week", "month": "month",
                   "night": "night"}
_DAY_SPANS = {(0, 4): "weekdays", (5, 6): "weekend"}
MAX_TITLE_CHARS = 120


@dataclass(frozen=True)
class RecurringPattern:
    dow: int
    start_time: str
    end_time: str
    quantity: int
    source_phrase: str


@dataclass
class ListingDraft:
    title: str
    description: str
    category_key: Optional[str]
    category_confidence: float
    attributes: dict = field(default_factory=dict)
    suggested_availabilities: list[RecurringPattern] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    unit_label: Optional[str] = None
    suggested_unit_amount_cents: Optional[int] = None
    currency: Optional[str] = None


def _clock(hour: int, minute: int, meridiem: Optional[str], *, end: bool) -> tuple[int, int]:
    if meridiem == "pm":
        h = hour % 12 + 12
    elif meridiem == "am":
        h = hour % 12
    elif end and hour < 7:
        h = hour + 12  # "9-5" ends in the afternoon, "9-17" already speaks 24-hour
    else:
        h = hour
    return max(0, min(23, h)), max(0, min(59, minute))


def _time_ranges(text: str) -> list[tuple[str, int, int, int, int]]:
    """[(phrase, start_hour, start_min, end_hour, end_min)] with start < end."""
    out = []
    for m in _RANGE_RE.finditer(text):
        sh, sm = _clock(int(m.group(1)), int(m.group(2) or 0), m.group(3), end=False)
        eh, em = _clock(int(m.group(4)), int(m.group(5) or 0), m.group(6), end=True)
        if (sh, sm) < (eh, em):
            out.append((m.group(0).strip(), sh, sm, eh, em))
    return out


def _day_spans(text: str) -> list[tuple[str, int, int]]:
    """[(phrase, first_dow, last_dow)] for single days and inclusive ranges."""
    out = []
    for m in _DOW_RE.finditer(text):
        first = _DOW_BY_ABBREV[m.group(1)]
        tail = text[m.end():]
        span = re.match(r"\s*(?:-|–|to|until|through)\s*(mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)", tail, re.I)
        if span:
            last = _DOW_BY_ABBREV[span.group(1).lower()[:3]]
            phrase = (m.group(0) + span.group(0)).strip()
        else:
            last, phrase = first, m.group(0)
        if first <= last:
            out.append((phrase, first, last))
    return out


def _availability_patterns(text: str, quantity: int) -> list[RecurringPattern]:
    patterns: list[RecurringPattern] = []
    ranges = _time_ranges(text)
    spans = _day_spans(text)
    for phrase, first, last in spans:
        if ranges:
            for r_phrase, sh, sm, eh, em in ranges:
                for dow in range(first, last + 1):
                    patterns.append(
                        RecurringPattern(dow, f"{sh:02d}:{sm:02d}", f"{eh:02d}:{em:02d}",
                                         quantity, f"{phrase} {r_phrase}".strip())
                    )
        else:
            for dow in range(first, last + 1):
                patterns.append(RecurringPattern(dow, "09:00", "17:00", quantity, phrase))
    if not patterns:
        for r_phrase, sh, sm, eh, em in ranges:
            for dow in range(0, 7):
                patterns.append(RecurringPattern(dow, f"{sh:02d}:{sm:02d}", f"{eh:02d}:{em:02d}",
                                                 quantity, r_phrase))
    # Collapse duplicates: same (dow, start, end) keeps its first source phrase.
    seen: set[tuple[int, str, str]] = set()
    unique = []
    for p in patterns:
        key = (p.dow, p.start_time, p.end_time)
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return sorted(unique, key=lambda p: (p.dow, p.start_time))[:14]


def _attributes(text: str, parsed: ParsedQuery) -> dict:
    attrs: dict = {}
    area = _AREA_RE.search(text)
    if area:
        attrs["area_square_meters"] = float(area.group(1).replace(",", "."))
    people = _PEOPLE_RE.search(text)
    if people:
        attrs["capacity_persons"] = int(people.group(1))
    if parsed.quantity:
        attrs["quantity"] = parsed.quantity
    if parsed.unit:
        attrs["unit_label"] = parsed.unit
    for phrase, code in sorted(CONSTRAINT_KEYWORDS.items()):
        if phrase in text:
            attrs[code] = True
    if _PRICE_RE.search(text):
        attrs["price_stated_by_provider"] = True
    return attrs


def _price(text: str) -> tuple[Optional[int], Optional[str], Optional[str]]:
    m = _PRICE_RE.search(text)
    if not m:
        return None, None, None
    raw = m.group(1).replace(",", ".")
    cents = round(float(raw) * 100)
    unit = _UNIT_BY_WORD.get(m.group(2).lower())
    symbol = next((c for c in _CURRENCY_BY_TOKEN if c in m.group(0)), None)
    currency = _CURRENCY_BY_TOKEN.get(symbol) if symbol else None
    return (cents if cents > 0 else None), unit, currency


def _slot_quantity(parsed: ParsedQuery) -> int:
    """How many units one availability slot publishes.

    An area ("40 sqm") or a budget number is not bookable quantity, so it must
    not leak into the slot — a room sized 40 m² with 12 seats publishes 12
    seats, and a plain room publishes 1.
    """
    people = _PEOPLE_RE.search(parsed.raw_text.lower())
    if people:
        return max(1, int(people.group(1)))
    if parsed.quantity and parsed.unit in _COUNTED_UNITS:
        return max(1, parsed.quantity)
    return 1


def _title(parsed: ParsedQuery, label: Optional[str], city: Optional[str]) -> str:
    noun = label or (parsed.category_key.replace("_", " ").title() if parsed.category_key else "Capacity")
    people = _PEOPLE_RE.search(" ".join(parsed.raw_text.split()).lower())
    descriptor = None
    if people:
        descriptor = f"{people.group(1)}-person {noun}"
    elif parsed.quantity and parsed.unit in _COUNTED_UNITS:
        unit = "seat" if parsed.unit == "seat" else parsed.unit.replace("_", " ")
        descriptor = f"{parsed.quantity} {unit} {noun}"
    elif parsed.unit in _PER_UNIT_TITLE:
        descriptor = f"{noun} by {_PER_UNIT_TITLE[parsed.unit]}"
    pieces = [descriptor or noun]
    if city:
        pieces.append(f"in {city}")
    title = " ".join(pieces)
    return title[:MAX_TITLE_CHARS]


def _description(raw_text: str, parsed: ParsedQuery, patterns: Sequence[RecurringPattern]) -> str:
    """Normalize the provider's own words; add nothing they did not write."""
    body = re.sub(r"\s+", " ", raw_text).strip()
    sentences = [body[:1].upper() + body[1:]] if body else []
    extras = []
    if patterns:
        days = _DAY_SPANS.get((patterns[0].dow, patterns[-1].dow))
        hours = f"{patterns[0].start_time}-{patterns[0].end_time}"
        if days and all(p.start_time == patterns[0].start_time for p in patterns):
            extras.append(f"Suggested availability: {days} {hours}.")
        else:
            extras.append(f"Suggested availability across {len(patterns)} slot(s), from {hours}.")
    if parsed.constraints:
        extras.append("Stated features: " + ", ".join(sorted(parsed.constraints)) + ".")
    if parsed.budget_max_cents:
        extras.append("Budget mentioned by the provider was noted but not published as a price.")
    return " ".join(sentences + extras).strip()


def draft_listing(
    raw_text: str,
    *,
    now: Optional[datetime] = None,
    catalog: Optional[Sequence[CategorySpec]] = None,
    category_key: Optional[str] = None,
    currency: str = "USD",
) -> ListingDraft:
    """Turn a provider's free-text description into a structured listing draft."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    parsed = parse_query(raw_text, moment, catalog)
    labels = {spec.key: spec.label for spec in _catalog_specs(catalog)}

    category = category_key or parsed.category_key
    patterns = _availability_patterns(raw_text.lower(), _slot_quantity(parsed))
    cents, price_unit, price_currency = _price(raw_text.lower())
    # `unit_label` is what the offer bills by, so a stated price wins over an area:
    # "40 sqm room at $60/hour" sells hours, and 40 m² stays an attribute.
    unit = price_unit or parsed.unit

    missing: list[str] = []
    if not category:
        missing.append("category_key")
    if not patterns:
        missing.append("availability")
    if cents is None:
        missing.append("unit_amount_cents")
    if not (parsed.city or parsed.country):
        missing.append("location")
    if len(re.sub(r"\s+", " ", raw_text).strip()) < 40:
        missing.append("description")

    return ListingDraft(
        title=_title(parsed, labels.get(category or ""), parsed.city),
        description=_description(raw_text, parsed, patterns),
        category_key=category,
        category_confidence=parsed.category_confidence if not category_key else 1.0,
        attributes=_attributes(raw_text.lower(), parsed),
        suggested_availabilities=patterns,
        missing_fields=missing,
        unit_label=unit,
        suggested_unit_amount_cents=cents,
        currency=price_currency or currency,
    )


def _catalog_specs(catalog: Optional[Sequence[CategorySpec]]) -> Sequence[CategorySpec]:
    from capacity_ai.semantic_query import DEFAULT_CATEGORIES

    return list(catalog) if catalog else list(DEFAULT_CATEGORIES)
