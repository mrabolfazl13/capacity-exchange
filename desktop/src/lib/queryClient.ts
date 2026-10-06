import { QueryClient } from '@tanstack/react-query';
import { ApiError } from '@/api/client';

export function createAppQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 15_000,
        retry: (failureCount, error) => {
          // Never retry auth/permission/not-found/409 business conflicts.
          if (error instanceof ApiError) {
            if (error.status === 401 || error.status === 403) return false;
            if (error.status === 409) return false;
            if (error.status >= 400 && error.status < 500) return false;
          }
          return failureCount < 1;
        },
        refetchOnWindowFocus: false,
      },
      mutations: {
        retry: 0,
      },
    },
  });
}
