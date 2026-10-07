"""Deterministic semantic query parser: free text -> structured filters.

Pure function ``parse_query(text, now, catalog) -> ParsedQuery``. No network,
no API key, no regex-only brittle code: the parser is a token-span consumer
driven by lookup tables (synonyms, gazetteer, unit map, time phrases, budget
qualifiers, constraint keywords). Unmatched tokens are returned as
``unparsed_fragments`` with an overall ``confidence`` so callers can degrade
gracefully.

Times are computed in UTC; timezone inference from text is out of scope (the
backend can pre-normalize with the user's locale).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable, Optional, Sequence

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CategorySpec:
    """One capacity category from the catalog (mirrors capacity_categories)."""

    key: str
    label: str
    synonyms: tuple[str, ...] = ()


@dataclass
class ParsedQuery:
    raw_text: str
    category_key: Optional[str] = None
    category_confidence: float = 0.0
    city: Optional[str] = None
    country: Optional[str] = None
    quantity: Optional[int] = None
    unit: Optional[str] = None
    window_start: Optional[datetime] = None
    window_end: Optional[datetime] = None
    budget_min_cents: Optional[int] = None
    budget_max_cents: Optional[int] = None
    currency: Optional[str] = None
    constraints: list[str] = field(default_factory=list)
    confidence: float = 0.0
    unparsed_fragments: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Tables (seed keys per CONTRACTS §5.2)
# ---------------------------------------------------------------------------

DEFAULT_CATEGORIES: tuple[CategorySpec, ...] = (
    CategorySpec("meeting_room", "Meeting room",
                 ("meeting room", "meeting rooms", "boardroom", "board room",
                  "conference room", "meeting", "workshop space")),
    CategorySpec("warehouse", "Warehouse",
                 ("warehouse", "warehousing", "distribution center",
                  "distribution centre", "fulfilment center", "fulfillment center")),
    CategorySpec("parking", "Parking",
                 ("parking", "car park", "parking space", "parking spot",
                  "parking garage", "garage spot")),
    CategorySpec("equipment", "Equipment",
                 ("equipment", "forklift", "pallet jack", "generator",
                  "compressor", "machine rental")),
    CategorySpec("appointment_slot", "Appointment slot",
                 ("appointment", "appointment slot", "time slot", "consultation slot")),
    CategorySpec("truck_return", "Truck return",
                 ("truck return", "return truck", "truck drop", "drop trailer",
                  "trailer return")),
    CategorySpec("commercial_kitchen", "Commercial kitchen",
                 ("commercial kitchen", "shared kitchen", "ghost kitchen",
                  "kitchen space", "cooking space", "bakery space")),
    CategorySpec("production_slot", "Production slot",
                 ("production slot", "manufacturing slot", "machine time",
                  "production line", "fabrication slot", "cnc time")),
    CategorySpec("storage_space", "Storage space",
                 ("storage", "storage space", "self storage", "cage space",
                  "pallet storage", "cold storage", "locker")),
    CategorySpec("hospitality_room", "Hospitality room",
                 ("hotel room", "guest room", "hospitality room", "suite",
                  "lodging")),
    CategorySpec("workstation", "Workstation",
                 ("workstation", "desk", "hot desk", "coworking", "co-working",
                  "office desk", "workspace")),
    CategorySpec("transport_vehicle", "Transport vehicle",
                 ("transport vehicle", "van", "truck rental", "box truck",
                  "reefer truck", "hauler", "courier vehicle", "minibus")),
)

# Normalized name -> (display city, ISO country). Small curated gazetteer.
CITY_GAZETTEER: dict[str, tuple[str, str]] = {
    "oslo": ("Oslo", "NO"), "bergen": ("Bergen", "NO"), "trondheim": ("Trondheim", "NO"),
    "stockholm": ("Stockholm", "SE"), "malmo": ("Malmo", "SE"), "gothenburg": ("Gothenburg", "SE"),
    "copenhagen": ("Copenhagen", "DK"), "helsinki": ("Helsinki", "FI"),
    "london": ("London", "GB"), "manchester": ("Manchester", "GB"), "birmingham": ("Birmingham", "GB"),
    "leeds": ("Leeds", "GB"), "glasgow": ("Glasgow", "GB"), "dublin": ("Dublin", "IE"),
    "new york": ("New York", "US"), "ny": ("New York", "US"), "chicago": ("Chicago", "US"),
    "los angeles": ("Los Angeles", "US"), "san francisco": ("San Francisco", "US"),
    "seattle": ("Seattle", "US"), "austin": ("Austin", "US"), "boston": ("Boston", "US"),
    "denver": ("Denver", "US"), "atlanta": ("Atlanta", "US"), "dallas": ("Dallas", "US"),
    "berlin": ("Berlin", "DE"), "munich": ("Munich", "DE"), "hamburg": ("Hamburg", "DE"),
    "frankfurt": ("Frankfurt", "DE"), "cologne": ("Cologne", "DE"),
    "paris": ("Paris", "FR"), "lyon": ("Lyon", "FR"), "marseille": ("Marseille", "FR"),
    "amsterdam": ("Amsterdam", "NL"), "rotterdam": ("Rotterdam", "NL"), "utrecht": ("Utrecht", "NL"),
    "warsaw": ("Warsaw", "PL"), "krakow": ("Krakow", "PL"), "gdansk": ("Gdansk", "PL"),
    "madrid": ("Madrid", "ES"), "barcelona": ("Barcelona", "ES"), "valencia": ("Valencia", "ES"),
    "milan": ("Milan", "IT"), "rome": ("Rome", "IT"), "turin": ("Turin", "IT"),
    "toronto": ("Toronto", "CA"), "vancouver": ("Vancouver", "CA"), "montreal": ("Montreal", "CA"),
    "singapore": ("Singapore", "SG"), "tokyo": ("Tokyo", "JP"), "osaka": ("Osaka", "JP"),
    "sydney": ("Sydney", "AU"), "melbourne": ("Melbourne", "AU"), "dubai": ("Dubai", "AE"),
}

COUNTRY_GAZETTEER: dict[str, str] = {
    "norway": "NO", "norwegian": "NO", "sweden": "SE", "swedish": "SE",
    "denmark": "DK", "danish": "DK", "finland": "FI", "uk": "GB", "united kingdom": "GB",
    "england": "GB", "great britain": "GB", "ireland": "IE", "germany": "DE", "german": "DE",
    "france": "FR", "french": "FR", "netherlands": "NL", "dutch": "NL", "poland": "PL",
    "polish": "PL", "spain": "ES", "spanish": "ES", "italy": "IT", "italian": "IT",
    "usa": "US", "united states": "US", "us": "US", "america": "US", "american": "US",
    "canada": "CA", "canadian": "CA", "singapore": "SG", "japan": "JP", "australia": "AU",
    "uae": "AE", "dubai": "AE",
}

# Unit normalization: token(s) -> canonical unit_label (§5.2 unit_label values).
UNIT_SYNONYMS: dict[str, str] = {
    "hour": "hour", "hours": "hour", "hr": "hour", "hrs": "hour",
    "pallet": "pallet", "pallets": "pallet", "pallet-space": "pallet",
    "seat": "seat", "seats": "seat",
    "vehicle_slot": "vehicle_slot", "vehicle slots": "vehicle_slot",
    "vehicleslot": "vehicle_slot", "loading slot": "vehicle_slot",
    "loading dock": "vehicle_slot", "dock": "vehicle_slot", "bays": "vehicle_slot",
    "day": "day", "days": "day", "night": "night", "nights": "night",
    "week": "week", "weeks": "week", "month": "month", "months": "month",
    "room": "room", "rooms": "room", "desk": "desk", "desks": "desk",
    "truck": "truck", "trucks": "truck", "van": "van", "vans": "van",
    "forklift": "forklift", "forklifts": "forklift", "machine": "machine",
    "machines": "machine", "appointment": "appointment", "appointments": "appointment",
    # A headcount is a seat count: "12 person meeting room" asks for room for twelve.
    "person": "seat", "persons": "seat", "people": "seat", "pax": "seat",
    "sqm": "square_meter", "square meter": "square_meter",
    "square meters": "square_meter", "square metre": "square_meter",
    "m2": "square_meter",
}

#: Units that count things. Only these may arrive hyphenated to a number
#: ("12-seat"), because "24-hour" and "48-hour" describe a duration, not 24 units.
_COUNTED_UNITS = frozenset({"seat", "pallet", "desk", "room", "truck", "van", "forklift",
                            "machine", "appointment", "vehicle_slot"})

# Weekday names -> ISO dow (0=Monday .. 6=Sunday) per §1.
WEEKDAYS: dict[str, int] = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
    "mon": 0, "tue": 1, "tues": 1, "wed": 2, "thu": 3, "thur": 3, "thurs": 3,
    "fri": 4, "sat": 5, "sun": 6,
}

# Day-part name -> (start time, end time) in UTC on the resolved day.
DAY_PARTS: dict[str, tuple[time, time]] = {
    "morning": (time(8, 0), time(12, 0)),
    "afternoon": (time(12, 0), time(17, 0)),
    "evening": (time(17, 0), time(22, 0)),
    "midday": (time(11, 0), time(14, 0)),
}

# Constraint keywords: phrase -> machine constraint code.
CONSTRAINT_KEYWORDS: dict[str, str] = {
    "instant booking": "instant_booking", "instantly": "instant_booking",
    "instant confirm": "instant_booking",
    "24/7": "always_available", "24 7": "always_available", "around the clock": "always_available",
    "with staff": "staffed", "staffed": "staffed", "manned": "staffed",
    "forklift included": "forklift_available", "with forklift": "forklift_available",
    "loading dock available": "loading_dock", "loading dock": "loading_dock",
    "outdoor": "outdoor",
    "secured": "secured_access", "gated": "secured_access", "fenced": "secured_access",
    "wheelchair": "wheelchair_accessible", "accessible": "wheelchair_accessible",
    "cold": "temperature_controlled", "refrigerated": "temperature_controlled",
    "chilled": "temperature_controlled", "heated": "temperature_controlled",
    "monthly": "recurring_monthly", "weekly": "recurring_weekly",
    "long term": "long_term", "long-term": "long_term",
    "electricity": "power_supply", "three phase": "three_phase_power",
}

# Budget qualifiers: phrase -> role ("max" | "min" | "estimate").
BUDGET_QUALIFIERS: dict[str, str] = {
    "under": "max", "below": "max", "less than": "max", "up to": "max",
    "max": "max", "maximum": "max", "at most": "max", "budget": "max",
    "budget of": "max", "within": "max", "cap": "max", "ceiling": "max",
    "over": "min", "above": "min", "at least": "min", "minimum": "min",
    "around": "estimate", "about": "estimate", "approx": "estimate",
    "approximately": "estimate", "roughly": "estimate",
}

CURRENCY_TOKENS: dict[str, str] = {
    "$": "USD", "usd": "USD", "dollar": "USD", "dollars": "USD",
    "€": "EUR", "eur": "EUR", "euro": "EUR", "euros": "EUR",
    "£": "GBP", "gbp": "GBP", "pound": "GBP", "pounds": "GBP",
    "kr": "NOK", "nok": "NOK", "krone": "NOK", "kroner": "NOK",
    "sek": "SEK", "yen": "JPY", "jpy": "JPY", "cad": "CAD", "aed": "AED",
}

_STOPWORDS = frozenset(
    "a an the for of in at on to and or with i we our need looking search me my"
    "please find available availability some any cheap best in".split()
)

_NUM_RE = re.compile(r"^\d+(?:[.,]\d+)?$")
_LEADING_CURRENCY_RE = re.compile(r"^[\$€£](\d+(?:[.,]\d+)?)$")
_TRAILING_CURRENCY_RE = re.compile(r"^(\d+(?:[.,]\d+)?)[\$€£]$")
#: "12-seat", "12-pallet" — a counted unit written as a compound.
_HYPHEN_COUNT_RE = re.compile(r"^(\d{1,6})[-–]([a-z]+)s?$")
#: Sentence punctuation is not part of a word, but it does stick to one
#: ("a room in Berlin."). Kept deliberately narrow: "$", "/", "-" and the
#: decimal point all carry meaning inside a token.
_EDGE_PUNCT = ",;:!?()[]{}\"'`«»“”‘’…"


# ---------------------------------------------------------------------------
# Tokenizer / span consumer
# ---------------------------------------------------------------------------


class _Text:
    """Lowercased token stream with consumption bookkeeping."""

    def __init__(self, raw: str) -> None:
        self.raw = raw
        norm = raw.lower().replace("\n", " ").replace("\t", " ")
        norm = re.sub(r"\s+", " ", norm).strip()
        # Punctuation is stripped at the edges of each token so "berlin." still
        # reads as the city; inside a token "$", "/" and "-" stay meaningful.
        self.tokens: list[str] = [tok for tok in (
            chunk.strip(_EDGE_PUNCT).rstrip(".") for chunk in norm.split(" ")
        ) if tok]
        self.consumed: list[bool] = [False] * len(self.tokens)

    def find_phrase(self, phrase: str, consume: bool = True, *,
                    ignore_consumed: bool = False) -> Optional[int]:
        """First index of ``phrase``; consumes the span.

        ``ignore_consumed`` also matches a span another section already claimed.
        A feature word and a category word can be the same word ("cold storage"
        names both the category and its temperature control), and dropping the
        second reading would silently lose a constraint the user typed.
        """
        wanted = phrase.split(" ")
        n = len(self.tokens)
        m = len(wanted)
        for i in range(n - m + 1):
            if self.consumed[i] and not ignore_consumed:
                continue
            if self.tokens[i:i + m] != wanted:
                continue
            if not ignore_consumed and any(self.consumed[i:i + m]):
                continue
            if consume:
                for j in range(i, i + m):
                    self.consumed[j] = True
            return i
        return None

    def consume_index(self, idx: int, length: int) -> None:
        for j in range(idx, min(idx + length, len(self.tokens))):
            self.consumed[j] = True

    def leftover_fragments(self) -> list[str]:
        """Contiguous runs of unconsumed tokens, stopwords stripped."""
        fragments: list[str] = []
        current: list[str] = []
        for i, tok in enumerate(self.tokens):
            if not self.consumed[i]:
                current.append(tok)
            if self.consumed[i] or i == len(self.tokens) - 1:
                if current:
                    core = [t for t in current if t not in _STOPWORDS and not _NUM_RE.match(t)]
                    if core:
                        fragments.append(" ".join(current).strip())
                    current = []
        return [f for f in fragments if f]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _category_index(catalog: Optional[Iterable[CategorySpec]]) -> list[tuple[str, str]]:
    """Phrase -> key pairs, longest phrase first.

    A live catalog *adds* to the curated vocabulary instead of replacing it: the
    database knows the real keys and labels, while the built-in table knows the
    synonyms ("cold storage" for `storage_space`) that a two-column registry
    cannot carry. Replacing one with the other made a deployed parser dumber than
    the tested one.
    """
    specs: dict[str, CategorySpec] = {spec.key: spec for spec in DEFAULT_CATEGORIES}
    for provided in catalog or ():
        current = specs.get(provided.key)
        if current is None:
            specs[provided.key] = provided
            continue
        specs[provided.key] = CategorySpec(
            key=provided.key or current.key,
            label=provided.label or current.label,
            synonyms=tuple(dict.fromkeys((*current.synonyms, *provided.synonyms))),
        )

    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for key, spec in specs.items():
        for phrase in (key.replace("_", " "), spec.label.lower(), *spec.synonyms):
            p = phrase.strip()
            if p and (p, key) not in seen:
                seen.add((p, key))
                pairs.append((p, key))
    # Longest phrases first so "meeting room" beats "meeting".
    pairs.sort(key=lambda kv: len(kv[0].split(" ")), reverse=True)
    return pairs


def _next_weekday(today: date, dow: int) -> date:
    delta = (dow - today.weekday()) % 7
    if delta == 0:
        delta = 7  # "next friday" on a Friday means the following week
    return today + timedelta(days=delta)


def _day_window(day: date, part: Optional[str]) -> tuple[datetime, datetime]:
    if part and part in DAY_PARTS:
        start_t, end_t = DAY_PARTS[part]
    else:
        start_t, end_t = time(0, 0), time(23, 59)
    start = datetime.combine(day, start_t, tzinfo=timezone.utc)
    end = datetime.combine(day, end_t, tzinfo=timezone.utc)
    return start, end


def _parse_number(token: str) -> Optional[float]:
    try:
        cleaned = token.replace(",", ".") if re.match(r"^\d+[.,]\d+$", token) else token
        return float(cleaned)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Section parsers (each: mutate parsed, consume spans)
# ---------------------------------------------------------------------------


def _parse_category(t: _Text, parsed: ParsedQuery, cat_pairs: list[tuple[str, str]]) -> None:
    hits: dict[str, int] = {}
    specific: dict[str, int] = {}
    first_at: dict[str, int] = {}
    for phrase, key in cat_pairs:
        words = len(phrase.split(" "))
        idx = t.find_phrase(phrase)
        while idx is not None:
            hits[key] = hits.get(key, 0) + 1
            specific[key] = max(specific.get(key, 0), words)
            first_at.setdefault(key, idx)
            idx = t.find_phrase(phrase)
    if not hits:
        return
    # A category is the thing on offer, so the most specific phrase names it and an
    # accessory mentioned beside it ("with forklift") does not. Where two phrases are
    # equally specific, the earlier one is the noun: "warehouse with forklift" is a
    # warehouse, and "forklift for the warehouse" is a forklift.
    best_key = max(sorted(hits), key=lambda k: (specific[k], hits[k], -first_at[k]))
    words = sum(hits.values())
    parsed.category_key = best_key
    parsed.category_confidence = round(min(0.95, 0.55 + 0.15 * words), 2)


#: "9 to 18", "9am-6pm", "09:00-17:00" — opening hours, not a bookable window.
_HOURS_RE = re.compile(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s*(?:-|–|to|until|till)\s*"
                       r"\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b")
_CONNECTORS = frozenset({"to", "-", "–", "until", "till", "through"})
#: Which weekdays a listing recurs on — a shape of the week, not a date range.
_RECURRING_DAYS_RE = re.compile(
    r"\b(?:weekdays?|working\s+days?|business\s+days?|mon(?:day)?\s*(?:-|–|to)\s*fri(?:day)?)\b")
#: Amount written together with the unit it bills by: "$60/hour", "€40 per day".
_PER_UNIT_AMOUNT_RE = re.compile(r"^[\$€£](\d+(?:[.,]\d+)?)(?:\s*/\s*|\s*per\s*)([a-z]+)$")


def _token_at(joined: str, char: int) -> int:
    """Index of the token covering ``char`` in a space-joined token string."""
    return 0 if char == 0 else len(joined[:char].split(" ")) - 1


def _parse_location(t: _Text, parsed: ParsedQuery) -> None:
    joined = " ".join(t.tokens)
    for name in sorted(CITY_GAZETTEER, key=len, reverse=True):
        idx = joined.find(name)
        while idx != -1:
            word_start = idx == 0 or joined[idx - 1] == " "
            end = idx + len(name)
            word_end = end == len(joined) or joined[end] == " "
            if word_start and word_end:
                tok_i = _token_at(joined, idx)
                if not any(t.consumed[tok_i:tok_i + len(name.split(" "))]):
                    city, country = CITY_GAZETTEER[name]
                    parsed.city = parsed.city or city
                    parsed.country = parsed.country or country
                    t.consume_index(tok_i, len(name.split(" ")))
                    break
            idx = joined.find(name, idx + 1)
    for name in sorted(COUNTRY_GAZETTEER, key=len, reverse=True):
        if t.find_phrase(name) is not None:
            parsed.country = parsed.country or COUNTRY_GAZETTEER[name]


def _parse_quantity(t: _Text, parsed: ParsedQuery) -> None:
    n = len(t.tokens)
    for i in range(n):
        tok = t.tokens[i]
        if t.consumed[i]:
            continue
        compound = _HYPHEN_COUNT_RE.match(tok)
        if compound:
            unit = (UNIT_SYNONYMS.get(compound.group(2))
                    or UNIT_SYNONYMS.get(compound.group(2) + "s"))
            if unit in _COUNTED_UNITS and parsed.quantity is None:
                parsed.quantity = max(1, int(compound.group(1)))
                parsed.unit = unit
                t.consume_index(i, 1)
            continue
        if not _NUM_RE.match(tok):
            continue
        value = _parse_number(tok)
        if value is None:
            continue
        unit = None
        span = 1
        for j in (i + 1, i + 2):
            if j < n and not t.consumed[j]:
                pair = " ".join(t.tokens[i + 1:j + 1])
                if pair in UNIT_SYNONYMS and j == i + 2:
                    unit = UNIT_SYNONYMS[pair]
                    span = 3
                    break
                if t.tokens[j] in UNIT_SYNONYMS and j == i + 1:
                    unit = UNIT_SYNONYMS[t.tokens[j]]
                    span = 2
                    break
        if unit is None:
            continue
        if unit == "square_meter":
            # An area is not a bookable count, and `min_quantity` is the only
            # quantity filter `/offers` takes: "40 sqm" must not become "40 seats".
            continue
        if parsed.quantity is None:
            parsed.quantity = max(1, int(value))
            parsed.unit = unit
            t.consume_index(i, span)


def _closing_span(t: _Text, i: int, start_dow: int) -> Optional[tuple[int, int]]:
    """'monday to friday' -> (2 tokens, 4 days) when the second day closes the span."""
    if i + 1 >= len(t.tokens) or t.consumed[i] or t.consumed[i + 1]:
        return None
    if t.tokens[i] not in _CONNECTORS:
        return None
    end_dow = WEEKDAYS.get(t.tokens[i + 1])
    if end_dow is None or end_dow < start_dow:
        return None
    return 2, end_dow - start_dow


def _consume_hour_ranges(t: _Text) -> None:
    """Drop opening hours and recurring-day words from the fragment list.

    `/offers` has no hour-of-day filter and no "which weekdays" filter, so
    "9 to 18" and "weekdays" cannot become windows. Leaving them unconsumed
    would put them in the keyword box, where they match nothing and turn a
    readable search into an empty one.
    """
    joined = " ".join(t.tokens)
    for pattern in (_HOURS_RE, _RECURRING_DAYS_RE):
        for m in pattern.finditer(joined):
            t.consume_index(_token_at(joined, m.start()), len(m.group(0).split(" ")))


def _parse_time(t: _Text, parsed: ParsedQuery, now: datetime) -> None:
    today = now.astimezone(timezone.utc).date()
    part = next((p for p in ("morning", "afternoon", "evening", "midday")
                 if t.find_phrase(p, consume=False) is not None), None)
    box = {"start": None, "end": None}

    def set_day(day: date, override_part: Optional[str] = None) -> None:
        box["start"], box["end"] = _day_window(day, override_part or part)

    if t.find_phrase("day after tomorrow") is not None:
        set_day(today + timedelta(days=2))
    elif t.find_phrase("tomorrow") is not None:
        set_day(today + timedelta(days=1))
    elif t.find_phrase("tonight") is not None:
        set_day(today, "evening")
    elif t.find_phrase("today") is not None:
        set_day(today)
    elif t.find_phrase("next week") is not None:
        monday = today + timedelta(days=7 - today.weekday())
        box["start"] = datetime.combine(monday, time(0, 0), tzinfo=timezone.utc)
        box["end"] = datetime.combine(monday + timedelta(days=6), time(23, 59), tzinfo=timezone.utc)
    elif t.find_phrase("this weekend") is not None or t.find_phrase("weekend") is not None:
        sat = _next_weekday(today, 5)
        box["start"] = datetime.combine(sat, time(0, 0), tzinfo=timezone.utc)
        box["end"] = datetime.combine(sat + timedelta(days=1), time(23, 59), tzinfo=timezone.utc)
    else:
        for prefix in ("next ", "this ", ""):
            found = False
            for name, dow in sorted(WEEKDAYS.items(), key=lambda kv: -len(kv[0])):
                phrase = prefix + name
                idx = t.find_phrase(phrase, consume=False)
                if idx is None:
                    continue
                if prefix == "this ":
                    day = today + timedelta(days=(dow - today.weekday()) % 7)
                else:
                    day = _next_weekday(today, dow)
                closing = _closing_span(t, idx + len(phrase.split(" ")), dow)
                tokens = len(phrase.split(" ")) + (closing[0] if closing else 0)
                t.consume_index(idx, tokens)
                set_day(day)
                if closing:
                    # "monday to friday" is a span, not a day: keep the start and
                    # close the window on the weekday that ends it.
                    end_t = DAY_PARTS[part][1] if part else time(23, 59)
                    box["end"] = datetime.combine(day + timedelta(days=closing[1]), end_t,
                                                  tzinfo=timezone.utc)
                found = True
                break
            if found:
                break
    if box["start"] is not None and part is not None:
        t.find_phrase(part)  # consume the day-part phrase
    _consume_hour_ranges(t)
    if box["start"] is None:
        return
    parsed.window_start, parsed.window_end = box["start"], box["end"]


def _amount_at(t: _Text, i: int) -> tuple[Optional[float], Optional[str], int]:
    """(amount, currency, span) for an unconsumed numeric token at index i."""
    n = len(t.tokens)
    if i >= n or t.consumed[i]:
        return None, None, 0
    tok = t.tokens[i]
    m = _LEADING_CURRENCY_RE.match(tok)
    if m:
        return _parse_number(m.group(1)), CURRENCY_TOKENS.get(tok[0]), 1
    m = _PER_UNIT_AMOUNT_RE.match(tok)
    if m:
        # "$60/hour" is a price per unit, and `max_unit_price` is the filter for it.
        return _parse_number(m.group(1)), CURRENCY_TOKENS.get(tok[0]), 1
    m = _TRAILING_CURRENCY_RE.match(tok)
    if m:
        return _parse_number(m.group(1)), CURRENCY_TOKENS.get(tok[-1]), 1
    if _NUM_RE.match(tok):
        nxt = t.tokens[i + 1] if i + 1 < n and not t.consumed[i + 1] else ""
        currency = CURRENCY_TOKENS.get(nxt)
        return _parse_number(tok), currency, 2 if currency else 1
    return None, None, 0


def _parse_budget(t: _Text, parsed: ParsedQuery) -> None:
    n = len(t.tokens)

    # 1) "between X and Y" (X and Y pure amounts, no trailing unit words)
    b = t.find_phrase("between", consume=False)
    if b is not None and b + 3 < n:
        x, xc, xs = _amount_at(t, b + 1)
        if x is not None and xs == 1 and t.tokens[b + 2] == "and":
            y, yc, ys = _amount_at(t, b + 3)
            if y is not None and ys == 1:
                lo, hi = sorted((round(x * 100), round(y * 100)))
                parsed.budget_min_cents, parsed.budget_max_cents = lo, hi
                parsed.currency = parsed.currency or xc or yc
                t.consume_index(b, 4)
                return

    # 2) qualifier + amount, or currency-marked bare amount
    i = 0
    while i < n:
        amount, currency, span = _amount_at(t, i)
        if amount is None or amount <= 0:
            i += 1
            continue
        role = None
        q_idx = None
        for size in (3, 2, 1):
            start = i - size
            if start < 0:
                continue
            window = t.tokens[start:i]
            if any(t.consumed[start:i]):
                continue
            phrase = " ".join(window)
            if phrase in BUDGET_QUALIFIERS:
                role = BUDGET_QUALIFIERS[phrase]
                q_idx = start
                break
        if role is None and currency is None:
            i += 1  # bare number with no budget context: not a budget
            continue
        if role is None:
            role = "max"
        cents = round(amount * 100)
        if role == "min":
            parsed.budget_min_cents = max(parsed.budget_min_cents or 0, cents)
        elif role == "estimate":
            if parsed.budget_min_cents is None:
                parsed.budget_min_cents = round(cents * 0.8)
            if parsed.budget_max_cents is None:
                parsed.budget_max_cents = round(cents * 1.2)
        else:  # max (explicit qualifier or currency-marked bare amount)
            if parsed.budget_max_cents is None or cents < parsed.budget_max_cents:
                parsed.budget_max_cents = cents
        parsed.currency = parsed.currency or currency
        if q_idx is not None:
            t.consume_index(q_idx, i - q_idx)
        t.consume_index(i, span)
        i += span


def _parse_constraints(t: _Text, parsed: ParsedQuery) -> None:
    for phrase in sorted(CONSTRAINT_KEYWORDS, key=lambda p: -len(p.split(" "))):
        # Every keyword gets one look, even over spans the category already took.
        if t.find_phrase(phrase, ignore_consumed=True) is not None:
            code = CONSTRAINT_KEYWORDS[phrase]
            if code not in parsed.constraints:
                parsed.constraints.append(code)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_query(
    text: str,
    now: datetime,
    catalog: Optional[Sequence[CategorySpec]] = None,
) -> ParsedQuery:
    """Parse natural-language capacity search into structured filters.

    Deterministic and offline-safe. ``catalog`` (CategorySpec items) extends
    or overrides the built-in seed categories. Returns confidence plus any
    unparsed fragments so callers can decide whether to ask the user.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    t = _Text(text)
    parsed = ParsedQuery(raw_text=text)
    _parse_category(t, parsed, _category_index(catalog))
    _parse_time(t, parsed, now)
    _parse_quantity(t, parsed)
    _parse_budget(t, parsed)
    _parse_location(t, parsed)
    _parse_constraints(t, parsed)

    score = 0.2
    if parsed.category_key:
        score += 0.25
    if parsed.window_start:
        score += 0.2
    if parsed.quantity:
        score += 0.1
    if parsed.budget_max_cents or parsed.budget_min_cents:
        score += 0.1
    if parsed.city or parsed.country:
        score += 0.1
    if parsed.constraints:
        score += 0.05
    parsed.confidence = round(min(0.95, score), 2)
    parsed.unparsed_fragments = t.leftover_fragments()
    return parsed
