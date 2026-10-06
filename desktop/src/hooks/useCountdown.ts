import { useEffect, useState } from 'react';

/** Ticking clock for countdowns. Returns Date.now() refreshed every `intervalMs`. */
export function useNow(intervalMs = 1000, enabled = true): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!enabled) return;
    const id = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs, enabled]);
  return now;
}

/**
 * Countdown to a booking hold's hold_expires_at (CONTRACTS §5.4).
 * Returns remaining ms and whether the hold is still alive.
 */
export function useHoldCountdown(holdExpiresAt: string | null | undefined) {
  const active = !!holdExpiresAt;
  const now = useNow(1000, active);
  if (!holdExpiresAt) return { remainingMs: null, expired: false, urgent: false };
  const remaining = new Date(holdExpiresAt).getTime() - now;
  return {
    remainingMs: Math.max(0, remaining),
    expired: remaining <= 0,
    urgent: remaining > 0 && remaining < 120_000,
  };
}

export function useDebouncedValue<T>(value: T, delayMs = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(id);
  }, [value, delayMs]);
  return debounced;
}
