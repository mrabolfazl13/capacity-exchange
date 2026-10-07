"""Capacity Exchange AI/Data package.

Design rules (docs/AI_SPEC.md + docs/CONTRACTS.md):

- Deterministic first. Every capability in this package produces a real result
  with NO API key, NO network and NO model, so the marketplace works in a
  sandbox, in CI and offline. The optional LLM enhancement is deliberately not
  implemented for v1: nothing here pretends to be smarter than a lookup table,
  and no caller branches on a path that does not exist.
- Core booking/payment correctness NEVER depends on this package. A failed or
  absent suggestion cannot block, alter or price a booking.
- Algorithms are pure functions over plain dataclasses (``semantic_query``,
  ``pricing``, ``insights``, ``listing_draft``). ``schemas`` holds the
  API-facing DTOs.
- Database access lives only behind the read-only adapter protocols in
  ``capacity_ai.db``. The backend implements them with SQLAlchemy and injects
  them; nothing in this package opens a connection or imports a driver.
- DTO conventions: snake_case, money as integer minor units, ISO-8601 UTC
  datetimes, lowercase_with_underscore enums (CONTRACTS §1).
"""

from capacity_ai.db import (
    CatalogCategory,
    CategoryDemand,
    ComparableOffer,
    DefinitionUsage,
    SlotBucket,
)
from capacity_ai.insights import (
    CopilotSummary,
    DefinitionUtilization,
    DemandSignal,
    IdleWindow,
    idle_windows,
    measure,
    summarize_copilot,
)
from capacity_ai.listing_draft import ListingDraft, RecurringPattern, draft_listing
from capacity_ai.pricing import PriceSuggestion, suggest_price
from capacity_ai.semantic_query import CategorySpec, ParsedQuery, parse_query

__version__ = "1.0.0"

__all__ = [
    "CatalogCategory",
    "CategoryDemand",
    "CategorySpec",
    "ComparableOffer",
    "CopilotSummary",
    "DefinitionUsage",
    "DefinitionUtilization",
    "DemandSignal",
    "IdleWindow",
    "ListingDraft",
    "ParsedQuery",
    "PriceSuggestion",
    "RecurringPattern",
    "SlotBucket",
    "draft_listing",
    "idle_windows",
    "measure",
    "parse_query",
    "suggest_price",
    "summarize_copilot",
]
