# AI Specification

## Capabilities

### Semantic Search
Understand natural-language requests and map them to capacity categories, locations, time windows, quantities and constraints.

### Matching
Rank compatible offers for a demand request using deterministic constraints first and ML/semantic relevance second.

### Listing Generator
Help providers turn raw descriptions into structured capacity listings.

### Pricing Recommendation
Suggest price ranges using historical utilization, demand, time, seasonality and comparable offers. Never silently change provider prices.

### Utilization Intelligence
Detect recurring unused capacity and suggest actions.

### Demand Forecasting
Estimate demand patterns from historical data when sufficient data exists.

### Provider Copilot
Summarize bookings, risks, idle capacity and suggested actions.

AI must degrade gracefully. Core booking and financial correctness must never depend on AI.

## Implementation status (v1)

Everything listed as shipped is deterministic: pure functions in `ai/capacity_ai` over data
the backend already stores, with no model, no API key and no network call, so CI, a sandbox
and an offline machine get the identical answer. There is **no LLM implementation** in v1 and
no code path branches on one; the optional enhancement described below is a plan, not a
harness that quietly falls back.

- **Semantic search** — shipped. `POST /ai/parse-search` maps prose onto the filters
  `GET /offers` accepts, using the live category registry as its vocabulary, and returns
  `unparsed_fragments` so a client can ask instead of guessing.
- **Matching** — shipped as the deterministic half. `GET /demands/{id}/matches` scores 1000
  points over capacity fit, price, distance, rating and text relevance, and ships the reasons
  that produced each score. No ML re-rank exists; the candidate set is only ever what
  `marketplace.search` would already accept, so a match cannot promise a slot the capacity
  engine would refuse.
- **Listing generator** — shipped. `POST /ai/listing-draft` extracts title, attributes,
  recurring availability and price from the provider's own words; anything absent is reported
  in `missing_fields` rather than invented.
- **Pricing recommendation** — shipped for comparable-backed advice: a percentile band over
  published offers, sampled on a widening ladder (§Pricing ladder below) and labelled cold
  start when even the widest sample is too small to be a market. Seasonality and time-of-year
  are **not** modelled; a suggestion prices against what the platform currently lists, and
  says how many offers it looked at and on which axis it compared them. It never writes a
  price (§5.4 keeps pricing authority with the provider).
- **Utilization intelligence** — shipped. `GET /ai/utilization-insights` compares published
  unit-hours with consumed ones per weekday/hour bucket and only reports a recurring idle
  block when those hours stayed empty across as many observations as its least-covered hour.
- **Demand forecasting** — not implemented. What ships is a period-over-period count
  (`demand_counts[].trend_note`), which says demand rose, fell or is new; it is not a
  forecast and is not presented as one.
- **Provider copilot** — shipped. `GET /ai/copilot` assembles one paragraph from the same
  figures its own payload carries, so a confident sentence and a number cannot disagree.
  Money in the copilot is cash-dated (`orders.placed_at`) and worded as collected, while
  dashboard revenue stays service-dated; the two axes are labelled, not merged.

Suggestions are advisory and read-only: no `/ai/*` route writes, and none may gate, delay or
price a booking.

## Pricing ladder

`POST /ai/price-suggest` samples published offers in the same category and currency, and widens
the sample **one axis at a time** until at least three comparables exist (`MIN_COMPARABLES`):

1. same city, same capacity mode, same billing unit
2. same country, same capacity mode, same billing unit
3. anywhere, same capacity mode, same billing unit
4. anywhere, same capacity mode, any billing unit
5. anywhere, anything in the category

Geography widens before the billing unit, and the capacity mode last: a Berlin price learned
from national data is still a price per the same unit, while a per-pallet price learned from
per-hour listings is simply a different number. The response cannot hide which step paid —
`rationale` names the scope the band was measured on, either `"<category> per <unit>"` or
`"<category>, any billing unit"`, plus the region if one was still in the filter. If no tier
reaches three comparables, the answer is a `cold_start_floor` with a `0.15`–`0.25` confidence
rather than a range dressed up as a market.

## How prose is read

The parser is a deterministic reader, not a classifier: it consumes tokens and can only name a
feature it found in the text. The rules that matter when a sentence reads wrong:

- **The most specific phrase names the category, and the earliest such phrase wins a tie.**
  "warehouse with forklift" is a warehouse; "forklift for the warehouse" is a forklift.
- **Only counted units may arrive hyphenated to a number.** "12-seat" and "12-pallet" are
  quantities; "24-hour" is a duration. An area stays an attribute — `min_quantity` is the only
  quantity filter `/offers` accepts, so "40 sqm" must never become 40 seats.
- **Punctuation is stripped at token edges, never inside a token.** "Berlin." still reads as
  the city while `$60/hour` survives.
- **A recurring day group means that group.** "weekdays 9 to 18" is Monday–Friday; reading only
  day names made the phrase look like no day at all and published the hours on all seven.
- **Opening hours are consumed once their day is known**, so they do not also reappear in
  `unparsed_fragments`.
- **An amount written with the unit it bills by is a unit price, not a budget**, and a stated
  price does not additionally get a "Budget mentioned" sentence.

Anything the reader could not place is returned in `unparsed_fragments` so the client can ask
about it instead of guessing.

## Known limitations

- **A day-span crossed with several time ranges over-generates slots.** "weekdays 9-18 and
  Saturday 10-14" produces every range on every day of every span, because the reader cannot
  tell which range belongs to which day group. Each pattern carries the `source_phrase` it came
  from, the draft is never written to the calendar without the provider confirming it, and the
  provider sees the slot list, so the failure is visible and correctable rather than silent.
- **No seasonality, no time-of-year, no demand forecast.** `demand_counts[].trend_note` is a
  period-over-period count and is worded as one.
- **No LLM.** Nothing in v1 calls a model, and no code path branches on one.
