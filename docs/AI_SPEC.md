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
  published offers at city, then country, then global scope, labelled cold start when the
  sample is too small to be a market. Seasonality and time-of-year are **not** modelled; a
  suggestion prices against what the platform currently lists, and says how many offers it
  looked at. It never writes a price (§5.4 keeps pricing authority with the provider).
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
