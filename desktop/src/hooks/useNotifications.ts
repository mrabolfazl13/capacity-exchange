// Notifications: SSE stream (CONTRACTS §1 GET /notifications/stream) with
// automatic fallback to polling GET /notifications?unread=true when EventSource
// is unavailable (Tauri WebView edge cases, tests) or the stream errors.

import { useEffect, useRef } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api/client';
import { notificationApi } from '@/api/endpoints';

const POLL_MS = 30_000;

export function useNotifications() {
  const qc = useQueryClient();

  // Polling fallback always armed (§1: clients not consuming SSE must poll).
  const listQuery = useQuery({
    queryKey: ['notifications'],
    queryFn: () => notificationApi.list(false, 50, 0),
    refetchInterval: POLL_MS,
  });

  const connectedRef = useRef(false);

  // Invalidate the list whenever the stream pushes an event.
  useEffect(() => {
    const token = api.getAccessToken();
    if (!token || typeof EventSource === 'undefined') return;
    let es: EventSource | null = null;
    try {
      es = new EventSource(notificationApi.streamUrl(token));
      es.onopen = () => {
        connectedRef.current = true;
      };
      es.onmessage = () => {
        void qc.invalidateQueries({ queryKey: ['notifications'] });
      };
      es.onerror = () => {
        connectedRef.current = false;
        es?.close();
      };
    } catch {
      connectedRef.current = false;
    }
    return () => {
      connectedRef.current = false;
      es?.close();
    };
  }, [qc]);

  const unread = (listQuery.data?.items ?? []).filter((n) => !n.read_at).length;

  return { ...listQuery, unread, live: connectedRef.current };
}
