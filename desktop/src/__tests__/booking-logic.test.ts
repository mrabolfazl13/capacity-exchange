// The booking domain's two decision functions, tested without a DOM: which actions a
// viewer may take (§5.5 + §4) and what a cancellation refunds (§5.6). These are the rules
// the screens render buttons from, so a wrong answer here is a button the server rejects.

import { describe, expect, it } from 'vitest';
import {
  PROVIDER_BOARD_COLUMNS,
  allowedBookingActions,
  refundPctForCancellation,
  validateHoldRequest,
  type BookingAction,
} from '@/features/booking/logic';
import type { Booking, BookingStatus, RoleKey, User } from '@/types/api';

function user(id: string, roles: RoleKey[], activeOrgId: string | null): User {
  return {
    id,
    tenant_id: 't1',
    email: `${id}@example.test`,
    phone: null,
    full_name: id,
    preferred_locale: 'en',
    is_active: true,
    last_login_at: null,
    created_at: '2026-01-01T00:00:00Z',
    roles,
    active_org_id: activeOrgId,
  };
}

const PROVIDER = user('u2', ['provider'], 'o1');
const CUSTOMER = user('c1', [], null);
const ORG_ADMIN_OTHER = user('u3', ['org_admin'], 'o2');

function booking(over: Partial<Booking> = {}): Booking {
  return {
    id: 'b1',
    status: 'hold',
    org_id: 'o1',
    customer_id: 'c1',
    payment_status: 'unpaid',
    fulfillment: null,
    review_submitted: false,
    ...over,
  } as Booking;
}

function actions(status: BookingStatus, over: Partial<Booking> = {}, user: User): BookingAction[] {
  return allowedBookingActions(booking({ status, ...over }), user);
}

describe('allowedBookingActions', () => {
  it('offers nothing without a session', () => {
    expect(allowedBookingActions(booking(), null)).toEqual([]);
  });

  it('asks the provider to confirm a hold and lets them withdraw it', () => {
    expect(actions('hold', {}, PROVIDER)).toEqual(['confirm', 'cancel']);
  });

  it('sends an unpaid customer hold to payment before confirmation', () => {
    expect(actions('hold', { payment_status: 'unpaid' }, CUSTOMER)).toEqual(['cancel', 'pay']);
  });

  it('offers confirmation once the customer has already paid', () => {
    expect(actions('hold', { payment_status: 'paid' }, CUSTOMER)).toEqual(['cancel', 'confirm']);
  });

  it('treats a converted draft the same way for both sides', () => {
    expect(actions('draft', {}, PROVIDER)).toEqual(['cancel', 'confirm']);
    expect(actions('draft', {}, CUSTOMER)).toEqual(['cancel']);
  });

  it('gives a booking in another organization no actions at all', () => {
    expect(actions('draft', {}, ORG_ADMIN_OTHER)).toEqual([]);
    expect(actions('confirmed', {}, ORG_ADMIN_OTHER)).toEqual([]);
  });

  it('runs the service window from the provider side only', () => {
    expect(actions('confirmed', {}, PROVIDER)).toEqual(['start']);
    expect(actions('confirmed', {}, CUSTOMER)).toEqual(['cancel']);
    expect(actions('in_progress', {}, PROVIDER)).toEqual(['complete']);
    expect(actions('in_progress', {}, CUSTOMER)).toEqual([]);
  });

  it('stops offering cancellation once the service has started', () => {
    // §5.6 refunds are keyed on hours-before-start; after the window opens the customer has
    // a claim route, not a cancel route.
    expect(actions('in_progress', {}, CUSTOMER)).toEqual([]);
  });

  it('offers review once, then only dispute', () => {
    const done = {
      status: 'completed' as BookingStatus,
      fulfillment: { id: 'f1' } as Booking['fulfillment'],
    };
    expect(actions('completed', done, CUSTOMER)).toEqual(['review', 'dispute']);
    expect(actions('completed', { ...done, review_submitted: true }, CUSTOMER)).toEqual(['dispute']);
    expect(actions('completed', { fulfillment: null }, CUSTOMER)).toEqual(['dispute']);
  });

  it('leaves a closed booking with nothing to do', () => {
    for (const status of ['cancelled', 'expired', 'disputed'] as BookingStatus[]) {
      expect(actions(status, {}, PROVIDER)).toEqual([]);
      expect(actions(status, {}, CUSTOMER)).toEqual([]);
    }
  });
});

describe('refundPctForCancellation', () => {
  const NOW = Date.UTC(2026, 0, 1, 0, 0, 0);
  const hoursAfter = (h: number) => new Date(NOW + h * 3_600_000).toISOString();
  const bands = [
    { hours_before: 2, refund_pct: 0 },
    { hours_before: 48, refund_pct: 100 },
    { hours_before: 24, refund_pct: 50 },
  ];

  it('refunds nothing when the listing declares no policy', () => {
    expect(refundPctForCancellation([], hoursAfter(60), NOW)).toBe(0);
  });

  it('applies the most generous band the window still qualifies for', () => {
    // Unsorted input: the order the provider typed the bands must not change the answer.
    expect(refundPctForCancellation(bands, hoursAfter(60), NOW)).toBe(100);
    expect(refundPctForCancellation(bands, hoursAfter(30), NOW)).toBe(50);
    expect(refundPctForCancellation(bands, hoursAfter(12), NOW)).toBe(0);
  });

  it('includes the band boundary itself', () => {
    expect(refundPctForCancellation(bands, hoursAfter(48), NOW)).toBe(100);
    expect(refundPctForCancellation(bands, hoursAfter(24), NOW)).toBe(50);
  });

  it('refunds nothing after the window has started', () => {
    expect(refundPctForCancellation(bands, hoursAfter(-1), NOW)).toBe(0);
  });
});

describe('validateHoldRequest', () => {
  const offer = {
    min_quantity: 2,
    max_quantity: 8,
    min_duration_minutes: 60,
    max_duration_minutes: 240,
  };
  const inTwoHours = new Date(Date.now() + 2 * 3_600_000).toISOString();
  const inThreeHours = new Date(Date.now() + 3 * 3_600_000).toISOString();
  const keys = (errors: { key: string }[]) => errors.map((e) => e.key);

  it('accepts a window that satisfies every limit', () => {
    expect(validateHoldRequest(offer, inTwoHours, inThreeHours, 2)).toEqual([]);
  });

  it('names a window that runs backwards', () => {
    // A negative length is also under the minimum duration; both are reported, and the
    // ordering of the two is the point of listing them, not guessing at the first.
    expect(keys(validateHoldRequest(offer, inThreeHours, inTwoHours, 2))).toEqual([
      'book.check.endAfterStart',
      'book.check.minDuration',
    ]);
  });

  it('stops a window that has already begun', () => {
    const past = new Date(Date.now() - 3_600_000).toISOString();
    expect(keys(validateHoldRequest(offer, past, inThreeHours, 2))).toContain('book.check.notPast');
  });

  it('returns one error for a window it cannot parse', () => {
    expect(validateHoldRequest(offer, 'not-a-date', inThreeHours, 2)).toEqual([
      { key: 'book.check.invalidWindow' },
    ]);
  });

  it('reports the listing bounds with their values, not a bare rule', () => {
    expect(validateHoldRequest(offer, inTwoHours, inThreeHours, 1)).toEqual([
      { key: 'book.check.minQuantity', params: { min: 2 } },
    ]);
    expect(validateHoldRequest(offer, inTwoHours, inThreeHours, 9)).toEqual([
      { key: 'book.check.maxQuantity', params: { max: 8 } },
    ]);
    expect(validateHoldRequest(offer, inTwoHours, inThreeHours, 1.5)).toEqual([
      { key: 'book.check.quantityAtLeastOne' },
      { key: 'book.check.minQuantity', params: { min: 2 } },
    ]);
  });

  it('checks the window length against the duration limits', () => {
    const inTenHours = new Date(Date.now() + 10 * 3_600_000).toISOString();
    expect(keys(validateHoldRequest(offer, inTwoHours, inTenHours, 2))).toEqual([
      'book.check.maxDuration',
    ]);
    const inTwoHoursTen = new Date(Date.now() + 2 * 3_600_000 + 10 * 60_000).toISOString();
    expect(keys(validateHoldRequest(offer, inTwoHours, inTwoHoursTen, 2))).toEqual([
      'book.check.minDuration',
    ]);
  });

  it('says nothing when the offer declares no limits', () => {
    const bare = {
      min_quantity: null,
      max_quantity: null,
      min_duration_minutes: null,
      max_duration_minutes: null,
    };
    expect(validateHoldRequest(bare, inTwoHours, inThreeHours, 1)).toEqual([]);
  });
});

describe('PROVIDER_BOARD_COLUMNS', () => {
  it('gives every booking status exactly one column', () => {
    // A status missing from the board disappears from the provider's view of their own
    // bookings; a status in two columns gets counted twice.
    const listed = PROVIDER_BOARD_COLUMNS.flatMap((c) => c.statuses);
    const all: BookingStatus[] = [
      'draft',
      'hold',
      'confirmed',
      'in_progress',
      'completed',
      'cancelled',
      'expired',
      'disputed',
    ];
    expect([...listed].sort()).toEqual([...all].sort());
    expect(new Set(listed).size).toBe(listed.length);
    expect(PROVIDER_BOARD_COLUMNS.map((c) => c.title)).toEqual([
      'prov.colHolds',
      'prov.colConfirmed',
      'prov.colInProgress',
      'prov.colDone',
    ]);
  });
});
