// Maps CONTRACTS §2 error envelopes to user-facing messages via the i18n
// catalog (err.<code> keys). Unknown codes fall back to the server message.

import { ApiError } from '@/api/client';
import type { MessageKey, TranslateFn } from '@/i18n/index';

const KNOWN_CODES = new Set([
  'network_error',
  'unauthorized',
  'token_expired',
  'token_invalid',
  'session_revoked',
  'forbidden',
  'not_member',
  'ownership_required',
  'role_required',
  'tenant_mismatch',
  'not_found',
  'conflict',
  'no_availability',
  'capacity_exceeded',
  'hold_expired',
  'already_rated',
  'duplicate_request',
  'invalid_credentials',
  'gone',
  'rate_limited',
  'internal_error',
  'dependency_unavailable',
  'validation_error',
  'range_mismatch',
  'invalid_state_transition',
  'unprocessable',
]);

export function isKnownErrorCode(code: string): boolean {
  return KNOWN_CODES.has(code);
}

/** Human message for any thrown value, using the §2 taxonomy first. */
export function describeError(err: unknown, t: TranslateFn): string {
  if (err instanceof ApiError) {
    if (err.code === 'rate_limited') {
      const retry = err.details['retry_after_seconds'];
      const base = t('err.rate_limited');
      return typeof retry === 'number'
        ? `${base} (${retry}s)`
        : base;
    }
    if (err.code === 'validation_error') {
      const fe = err.fieldErrors;
      if (fe.length > 0) {
        return `${t('err.validation_error')} ${fe
          .map((f) => `${f.field}: ${f.message}`)
          .join(' · ')}`;
      }
    }
    if (isKnownErrorCode(err.code)) {
      return t(`err.${err.code}` as MessageKey);
    }
    return err.message;
  }
  if (err instanceof Error) return err.message;
  return t('err.unknown');
}

/** Booking-domain 409 codes that need dedicated screens in the hold flow. */
export const HOLD_CONFLICT_CODES = [
  'no_availability',
  'capacity_exceeded',
  'hold_expired',
  'duplicate_request',
] as const;

export function holdConflictCode(err: unknown): string | null {
  if (err instanceof ApiError && err.status === 409 && (HOLD_CONFLICT_CODES as readonly string[]).includes(err.code)) {
    return err.code;
  }
  return null;
}
