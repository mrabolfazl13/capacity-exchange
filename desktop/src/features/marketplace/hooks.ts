import { useQuery } from '@tanstack/react-query';
import { capacityApi, catalogApi, offerApi } from '@/api/endpoints';
import type { OfferSearchParams } from '@/types/api';

export function useCategories() {
  return useQuery({
    queryKey: ['categories'],
    queryFn: () => catalogApi.categories(),
    staleTime: 5 * 60_000,
  });
}

export function useOfferSearch(params: OfferSearchParams, enabled = true) {
  return useQuery({
    queryKey: ['offers', 'search', params],
    queryFn: ({ signal }) => offerApi.search(params, signal),
    enabled,
    placeholderData: (prev) => prev,
  });
}

export function useOffer(id: string | undefined) {
  return useQuery({
    queryKey: ['offers', id],
    queryFn: () => offerApi.get(id as string),
    enabled: !!id,
  });
}

export function useOfferReviews(offerId: string | undefined) {
  return useQuery({
    queryKey: ['offers', offerId, 'reviews'],
    queryFn: () => offerApi.reviews(offerId as string, 20, 0),
    enabled: !!offerId,
  });
}

/** Server-expanded free windows for an offer's definition (§8). */
export function useFreeWindows(
  definitionId: string | undefined,
  from: string,
  to: string,
) {
  return useQuery({
    queryKey: ['availability', 'free', definitionId, from, to],
    queryFn: () => capacityApi.freeWindows(definitionId as string, from, to),
    enabled: !!definitionId,
  });
}
