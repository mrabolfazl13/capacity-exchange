// Pure booking-domain helpers (state machine §5.5 + cancellation policy §5.6).
// Kept UI-free so they are directly unit-testable.

import type {
  Booking,
  BookingStatus,
  CancellationPolicyBand,
  Offer,
  User,
} from '@/types/api';

export type BookingAction =
  | 'pay'
  | 'confirm'
  | 'start'
  | 'complete'
  | 'cancel'
  | 'review'
  | 'dispute';

function isProviderSide(user: User, booking: Booking): boolean {
  return (
    !!user.active_org_id &&
    user.active_org_id === booking.org_id &&
    (user.roles.includes('provider') || user.roles.includes('org_admin'))
  );
}

function isCustomerSide(user: User, booking: Booking): boolean {
  return user.id === booking.customer_id;
}

/**
 * Allowed state actions for a booking given the viewer (§5.5 transitions + §4
 * RBAC: only the provider confirms/starts/completes; the customer cancels per
 * policy and reviews completed fulfillments).
 */
export function allowedBookingActions(booking: Booking, user: User | null): BookingAction[] {
  if (!user) return [];
  const actions: BookingAction[] = [];
  const provider = isProviderSide(user, booking);
  const customer = isCustomerSide(user, booking);
  const needsPayment = booking.payment_status === 'unpaid';

  switch (booking.status) {
    case 'hold': {
      if (provider) actions.push('confirm', 'cancel');
      if (customer) {
        actions.push('cancel');
        actions.push(needsPayment ? 'pay' : 'confirm');
      }
      break;
    }
    case 'draft': {
      if (provider || customer) actions.push('cancel');
      if (provider) actions.push('confirm');
      break;
    }
    case 'confirmed': {
      if (provider) actions.push('start');
      if (customer) actions.push('cancel');
      break;
    }
    case 'in_progress': {
      if (provider) actions.push('complete');
      break;
    }
    case 'completed': {
      if (customer) {
        if (booking.fulfillment && !booking.review_submitted) actions.push('review');
        actions.push('dispute');
      }
      break;
    }
    case 'cancelled':
    case 'expired':
    case 'disputed':
    default:
      break;
  }
  return actions;
}

/**
 * Cancellation refund preview: match offer.cancellation_policy bands on
 * hours-before-start (§5.6). Bands are [{hours_before, refund_pct}] with
 * hours_before descending; the first band whose hours_before <= hoursUntil
 * applies. Returns refund percentage 0..100.
 */
export function refundPctForCancellation(
  policy: CancellationPolicyBand[],
  windowStartIso: string,
  nowMs: number,
): number {
  if (!policy || policy.length === 0) return 0;
  const hoursUntil =
    (new Date(windowStartIso).getTime() - nowMs) / 3_600_000;
  const sorted = [...policy].sort((a, b) => b.hours_before - a.hours_before);
  for (const band of sorted) {
    if (hoursUntil >= band.hours_before) return band.refund_pct;
  }
  return 0;
}

/** Client-side sanity check before placing a hold; server revalidates (§5.5). */
export function validateHoldRequest(
  offer: Pick<Offer, 'min_quantity' | 'max_quantity' | 'min_duration_minutes' | 'max_duration_minutes'>,
  startIso: string,
  endIso: string,
  quantity: number,
): string[] {
  const errors: string[] = [];
  const start = new Date(startIso).getTime();
  const end = new Date(endIso).getTime();
  if (!Number.isFinite(start) || !Number.isFinite(end)) {
    errors.push('Invalid window dates');
    return errors;
  }
  if (end <= start) errors.push('End must be after start');
  if (start < Date.now() - 60_000) errors.push('Start cannot be in the past');
  if (!Number.isInteger(quantity) || quantity < 1) errors.push('Quantity must be at least 1');
  if (offer.min_quantity != null && quantity < offer.min_quantity) {
    errors.push(`Minimum quantity is ${offer.min_quantity}`);
  }
  if (offer.max_quantity != null && quantity > offer.max_quantity) {
    errors.push(`Maximum quantity is ${offer.max_quantity}`);
  }
  const minutes = (end - start) / 60_000;
  if (offer.min_duration_minutes != null && minutes < offer.min_duration_minutes) {
    errors.push(`Minimum duration is ${offer.min_duration_minutes} minutes`);
  }
  if (offer.max_duration_minutes != null && minutes > offer.max_duration_minutes) {
    errors.push(`Maximum duration is ${offer.max_duration_minutes} minutes`);
  }
  return errors;
}

/** Board column layout for the provider bookings board. */
export const PROVIDER_BOARD_COLUMNS: { title: string; statuses: BookingStatus[] }[] = [
  { title: 'Holds', statuses: ['hold', 'draft'] },
  { title: 'Confirmed', statuses: ['confirmed'] },
  { title: 'In progress', statuses: ['in_progress'] },
  { title: 'Done', statuses: ['completed', 'cancelled', 'expired', 'disputed'] },
];
