"""Deterministic price suggestion (docs/AI_SPEC.md, "Pricing Recommendation").

Suggests a range from what comparable published offers actually charge. No
forecasting model, no network: the answer is explainable because it is a
percentile of a list the caller can print.

A suggestion never changes a price. The backend writes nothing here, and the
clients present the range beside the provider's own field (CONTRACTS §5.4 keeps
pricing authority with the provider).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from capacity_ai.db import ComparableOffer

# With fewer than this many comparables the sample cannot describe a market, so
# the answer is labelled a floor instead of a range.
MIN_COMPARABLES = 3
# Prices are rounded to this step so a suggestion reads like a price
# ("USD 60.00"), not like a percentile ("USD 58.73").
ROUNDING_STEP_CENTS = 50
COLD_START_SPREAD = 1.5


@dataclass(frozen=True)
class PriceSuggestion:
    suggested_min_cents: int
    suggested_max_cents: int
    currency: str
    rationale: str
    confidence: float
    method: str  # "comparables" | "cold_start_floor"
    comparable_count: int


def _percentile(sorted_values: Sequence[int], pct: float) -> float:
    """Linear-interpolated percentile of an already-sorted sample."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    pos = (len(sorted_values) - 1) * pct
    low = int(pos)
    high = min(low + 1, len(sorted_values) - 1)
    frac = pos - low
    return sorted_values[low] * (1 - frac) + sorted_values[high] * frac


def _round_to_step(cents: float, step: int = ROUNDING_STEP_CENTS) -> int:
    return int((round(cents / step) * step))


def _amount(cents: int, currency: str) -> str:
    return f"{currency} {cents / 100:,.2f}"


def suggest_price(
    comparables: Sequence[ComparableOffer],
    *,
    currency: str,
    region: Optional[str] = None,
    scope: Optional[str] = None,
    cold_start_floor_cents: int = 0,
) -> PriceSuggestion:
    """Suggest a price range from comparable published offers.

    ``comparables`` is expected to already be filtered to the same category,
    unit label and currency (the adapter's job); this function only looks at
    the numbers, which is why the result is auditable.
    """
    values = sorted(c.unit_amount_cents for c in comparables if c.unit_amount_cents > 0)
    count = len(values)
    where = f" in {region}" if region else ""
    what = f" for {scope}" if scope else ""

    if count < MIN_COMPARABLES:
        floor = max(0, _round_to_step(cold_start_floor_cents))
        ceiling = max(floor, _round_to_step(floor * COLD_START_SPREAD))
        reason = (
            f"Only {count} published offer(s){what}{where} to compare, which is too few "
            f"to describe a market. Suggested as a starting floor rather than a range; "
            f"verify against local prices before publishing."
        )
        return PriceSuggestion(
            suggested_min_cents=floor,
            suggested_max_cents=ceiling,
            currency=currency,
            rationale=reason,
            confidence=0.25 if count else 0.15,
            method="cold_start_floor",
            comparable_count=count,
        )

    p25 = _percentile(values, 0.25)
    median = _percentile(values, 0.50)
    p75 = _percentile(values, 0.75)

    low = _round_to_step(max(p25, median * 0.8))
    high = _round_to_step(max(p75, median * 1.1))
    if high < low:  # a rounding artifact on a tight sample, never a real inversion
        high = low

    # Confidence rises with sample size and falls when the sample disagrees:
    # p75/p25 above ~2x means the "market" is really two different markets.
    sample = min(1.0, 0.35 + 0.05 * min(count, 12))
    spread = (p75 / p25) if p25 > 0 else 2.5
    agreement = max(0.4, min(1.0, 1.6 - 0.3 * spread)) if spread > 1 else 1.0
    confidence = round(min(0.9, sample * agreement), 2)

    rationale = (
        f"Based on {count} published offers{what}{where}: the middle half charges "
        f"between {_amount(int(round(p25)), currency)} and {_amount(int(round(p75)), currency)} "
        f"(median {_amount(int(round(median)), currency)}). The suggested range keeps "
        f"a new listing inside that band while leaving room to price below or above it."
    )
    return PriceSuggestion(
        suggested_min_cents=low,
        suggested_max_cents=high,
        currency=currency,
        rationale=rationale,
        confidence=confidence,
        method="comparables",
        comparable_count=count,
    )
