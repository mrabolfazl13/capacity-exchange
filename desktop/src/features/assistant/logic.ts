// Pure assistant-domain helpers: they turn an `/ai/*` payload into the values the
// screens already hold, so the mapping is unit-testable without a render or a server.
// Nothing here saves — the assistant is advisory and publishing stays an explicit
// act on the form (§12), which is why every function returns field values, not a POST.

import { centsToMajor } from '@/lib/format';
import type { CapacityCategory, ListingDraft, ListingDraftGap, ParsedQuery } from '@/types/api';

/**
 * The search form's filter boxes, as strings, ready to spread over the page's draft
 * state. `max_unit_cents` carries major units, because that is the box it fills —
 * SearchPage converts on submit so the wire value stays integer cents (§1).
 */
export interface ParsedFilters {
  category_id: string;
  city: string;
  country: string;
  from: string;
  to: string;
  min_quantity: string;
  max_unit_cents: string;
}

/** A calendar date for a filter box, from the service-time instant the parse returned. */
export function dateFromIso(value: string | null): string {
  if (!value) return '';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value.slice(0, 10);
  return d.toISOString().slice(0, 10);
}

export interface ParsedSearchMapping {
  filters: ParsedFilters;
  /**
   * The keyword box. Words the parser could not structure are appended rather than
   * dropped, so "with a forklift" still reaches full-text search instead of silently
   * vanishing from the query.
   */
  q: string;
  /** True when the read produced at least one structured filter. */
  applied: boolean;
}

function firstNonEmpty(current: string, next: string): string {
  return current.trim() ? current : next;
}

export function mappingFromParsedQuery(parsed: ParsedQuery, currentQ: string): ParsedSearchMapping {
  const filters: ParsedFilters = {
    // The server resolves category_key against the live catalog; a key it does not
    // know comes back with a null id, so the box simply stays on "all".
    category_id: parsed.category_id ?? '',
    city: parsed.city ?? '',
    country: parsed.country ?? '',
    from: dateFromIso(parsed.window_start),
    to: dateFromIso(parsed.window_end),
    min_quantity: parsed.quantity != null ? String(parsed.quantity) : '',
    max_unit_cents:
      parsed.budget_max_cents != null ? centsToMajor(parsed.budget_max_cents) : '',
  };

  const leftovers = parsed.unparsed_fragments.join(' ').trim();
  const applied = Object.values(filters).some((v) => v !== '');

  return {
    filters,
    q: firstNonEmpty(currentQ, leftovers),
    applied,
  };
}

/** One suggested recurring rule, without the wizard's local keys. */
export interface FillRule {
  dow: string;
  start_time: string;
  end_time: string;
  quantity: string;
}

/**
 * A listing draft expressed as wizard field values. Empty strings mean "the draft had
 * nothing to say" — the caller keeps whatever the provider typed instead of overwriting
 * a box with a blank.
 */
export interface ListingFill {
  categoryId: string;
  title: string;
  description: string;
  unitLabel: string;
  quantity: string;
  price: string;
  currency: string;
  rules: FillRule[];
  gaps: ListingDraftGap[];
}

export function fillFromListingDraft(
  draft: ListingDraft,
  categories: CapacityCategory[],
): ListingFill {
  // Only a category the picker actually offers can be preselected: an inactive or
  // unknown key must not put the wizard on a value its own dropdown cannot render.
  const usable = categories.find(
    (c) => c.is_active && (c.id === draft.category_id || c.key === draft.category_key),
  );

  const rules: FillRule[] = draft.suggested_availabilities.map((r) => ({
    dow: String(r.dow),
    start_time: r.start_time,
    end_time: r.end_time,
    quantity: String(r.quantity),
  }));

  // A single capacity unit for the whole listing: the busiest suggested day is the
  // honest default for min/max, since the smaller ones are just quiet days.
  const peak = rules.reduce((max, r) => Math.max(max, Number.parseInt(r.quantity, 10) || 0), 0);

  return {
    categoryId: usable?.id ?? '',
    title: draft.title.trim(),
    description: draft.description.trim(),
    unitLabel: draft.unit_label?.trim() ?? '',
    quantity: peak > 0 ? String(peak) : '',
    price:
      draft.suggested_unit_amount_cents != null
        ? centsToMajor(draft.suggested_unit_amount_cents)
        : '',
    currency: draft.currency ?? '',
    rules,
    gaps: draft.missing_fields ?? [],
  };
}

/** The gaps the draft cannot fill, in the order the wizard meets them. */
export const GAP_ORDER: ListingDraftGap[] = [
  'category_key',
  'availability',
  'unit_amount_cents',
  'location',
  'description',
];

export function sortGaps(gaps: ListingDraftGap[]): ListingDraftGap[] {
  return [...gaps].sort((a, b) => GAP_ORDER.indexOf(a) - GAP_ORDER.indexOf(b));
}
