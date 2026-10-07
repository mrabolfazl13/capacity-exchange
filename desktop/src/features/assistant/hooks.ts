// React Query wiring for `/ai/*` (CONTRACTS §8).
//
// These reads are advisory: they answer from data the platform already stores, and no
// call here writes a row or gates a booking. So they don't retry — a suggestion that
// cannot load leaves the screen as it was, which is the documented degradation.

import { useMutation, useQuery } from '@tanstack/react-query';
import { aiApi, type PriceSuggestInput } from '@/api/endpoints';

/** The provider's one-paragraph briefing. Org comes from the session, like the dashboard. */
export function useCopilot() {
  return useQuery({
    queryKey: ['ai', 'copilot'],
    queryFn: () => aiApi.copilot(),
    staleTime: 60_000,
    retry: false,
  });
}

/** Where published capacity went unused, over the window the dashboard is already showing. */
export function useUtilizationInsights(from?: string, to?: string) {
  return useQuery({
    queryKey: ['ai', 'utilization-insights', from ?? null, to ?? null],
    queryFn: () => aiApi.utilizationInsights(from, to),
    staleTime: 60_000,
    retry: false,
  });
}

/** Free text -> the structured filters the search route already accepts. */
export function useParseSearch() {
  return useMutation({ mutationFn: (text: string) => aiApi.parseSearch(text) });
}

/** Raw description -> a fill-in-the-gaps listing draft. Returned, never saved. */
export function useListingDraft() {
  return useMutation({
    mutationFn: ({ rawText, categoryKey }: { rawText: string; categoryKey?: string }) =>
      aiApi.draftListing(rawText, { category_key: categoryKey }),
  });
}

/** A price band from published comparables, for the wizard's pricing step. */
export function usePriceSuggestion() {
  return useMutation({ mutationFn: (input: PriceSuggestInput) => aiApi.priceSuggest(input) });
}
