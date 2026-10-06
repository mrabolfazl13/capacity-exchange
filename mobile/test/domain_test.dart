import 'package:capacity_exchange/data/actions.dart';
import 'package:capacity_exchange/models/booking.dart';
import 'package:capacity_exchange/models/envelope.dart';
import 'package:capacity_exchange/models/offer.dart';
import 'package:capacity_exchange/models/user.dart';
import 'package:flutter_test/flutter_test.dart';

Booking _booking(String status, {String payment = 'unpaid'}) => Booking(
  id: 'b1',
  offerId: 'o1',
  definitionId: 'd1',
  orgId: 'org1',
  customerId: 'cust1',
  status: status,
  paymentStatus: payment,
  quantity: 1,
  unitAmountCents: 5000,
  currency: 'USD',
  totalCents: 5000,
);

const customer = User(
  id: 'cust1',
  email: 'c@example.com',
  fullName: 'Customer',
  roles: ['customer'],
);

const provider = User(
  id: 'prov1',
  email: 'p@example.com',
  fullName: 'Provider',
  roles: ['provider', 'org_admin'],
  activeOrgId: 'org1',
);

Offer _offer({int? minQty, int? maxQty, int? minMinutes, int? maxMinutes}) =>
    Offer(
      id: 'o1',
      title: 'Court',
      description: '',
      definitionId: 'd1',
      orgId: 'org1',
      pricingMode: 'per_quantity',
      unitAmountCents: 5000,
      currency: 'USD',
      bookingMode: 'instant',
      holdMinutes: 15,
      minQuantity: minQty,
      maxQuantity: maxQty,
      minDurationMinutes: minMinutes,
      maxDurationMinutes: maxMinutes,
    );

void main() {
  group('allowedBookingActions', () {
    test('mirrors the §5.5 transitions per side', () {
      expect(
        allowedBookingActions(_booking('hold'), customer),
        containsAll(<BookingAction>[BookingAction.cancel]),
      );
      expect(
        allowedBookingActions(_booking('hold'), customer),
        contains(BookingAction.pay),
      );
      expect(
        allowedBookingActions(_booking('hold', payment: 'paid'), customer),
        contains(BookingAction.confirm),
      );
      expect(
        allowedBookingActions(_booking('hold'), provider),
        containsAll(<BookingAction>[BookingAction.confirm]),
      );
      expect(
        allowedBookingActions(_booking('confirmed'), provider),
        equals([BookingAction.start]),
      );
      expect(
        allowedBookingActions(_booking('in_progress'), provider),
        equals([BookingAction.complete]),
      );
      expect(
        allowedBookingActions(_booking('completed'), customer),
        containsAll(<BookingAction>[
          BookingAction.review,
          BookingAction.dispute,
        ]),
      );
      expect(allowedBookingActions(_booking('cancelled'), customer), isEmpty);
      expect(allowedBookingActions(_booking('completed'), provider), isEmpty);
      expect(allowedBookingActions(_booking('hold'), null), isEmpty);
    });
  });

  group('refundPctForCancellation', () {
    const bands = [
      CancellationBand(hoursBefore: 24, refundPct: 100),
      CancellationBand(hoursBefore: 2, refundPct: 0),
    ];
    final start = DateTime.utc(2026, 3, 20, 12);

    test('picks the highest band still ahead of now (§5.6)', () {
      expect(
        refundPctForCancellation(
          bands,
          start,
          now: start.subtract(const Duration(hours: 30)),
        ),
        100,
      );
      expect(
        refundPctForCancellation(
          bands,
          start,
          now: start.subtract(const Duration(hours: 5)),
        ),
        0,
      );
      expect(refundPctForCancellation(const [], start), 0);
      expect(refundPctForCancellation(bands, null), 0);
    });
  });

  group('holdIssues', () {
    test('accepts a window inside the offer bounds', () {
      final start = DateTime.now().add(const Duration(days: 1));
      expect(
        holdIssues(
          offer: _offer(minQty: 1, maxQty: 10, minMinutes: 60, maxMinutes: 240),
          start: start,
          end: start.add(const Duration(hours: 2)),
          quantity: 2,
        ),
        isEmpty,
      );
    });

    test('reports each bound by its catalog key', () {
      final start = DateTime.now().add(const Duration(days: 1));
      expect(
        holdIssues(
          offer: _offer(minQty: 4, maxQty: 6),
          start: start,
          end: start.add(const Duration(hours: 1)),
          quantity: 9,
        ),
        contains('err_quantity_max'),
      );
      expect(
        holdIssues(
          offer: _offer(minQty: 4),
          start: start,
          end: start.add(const Duration(hours: 1)),
          quantity: 1,
        ),
        contains('err_quantity_min'),
      );
      expect(
        holdIssues(
          offer: _offer(maxMinutes: 30),
          start: start,
          end: start.add(const Duration(hours: 1)),
          quantity: 1,
        ),
        contains('err_duration_max'),
      );
      expect(
        holdIssues(
          offer: _offer(minMinutes: 120),
          start: start,
          end: start.add(const Duration(hours: 1)),
          quantity: 1,
        ),
        contains('err_duration_min'),
      );
      expect(
        holdIssues(offer: _offer(), start: start, end: start, quantity: 1),
        contains('err_end_after_start'),
      );
      expect(
        holdIssues(
          offer: _offer(),
          start: DateTime.now().subtract(const Duration(days: 1)),
          end: DateTime.now(),
          quantity: 1,
        ),
        contains('err_window_past'),
      );
    });
  });

  group('ListEnvelope', () {
    test('reads the §2 envelope', () {
      final page = ListEnvelope.fromJson({
        'items': [
          {'id': 'x'},
          {'id': 'y'},
        ],
        'total': 7,
        'limit': 2,
        'offset': 2,
      }, (m) => m['id'] as String);
      expect(page.items, ['x', 'y']);
      expect(page.total, 7);
      expect(page.hasMore, isTrue);
    });

    test('a bare array is one complete page', () {
      final page = ListEnvelope.fromJson([
        {'id': 'x'},
      ], (m) => m['id'] as String);
      expect(page.items, ['x']);
      expect(page.hasMore, isFalse);
    });

    test('anything else is an empty page, not a crash', () {
      final page = ListEnvelope.fromJson(null, (m) => m['id'] as String);
      expect(page.items, isEmpty);
      expect(page.hasMore, isFalse);
    });
  });

  group('Booking model', () {
    test('exposes the hold countdown as a non-negative duration', () {
      final expires = DateTime.now().add(const Duration(minutes: 3));
      final hold = Booking(
        id: 'b1',
        offerId: 'o1',
        definitionId: 'd1',
        orgId: 'org1',
        customerId: 'cust1',
        status: BookingStatuses.hold,
        paymentStatus: 'unpaid',
        quantity: 1,
        unitAmountCents: 0,
        currency: 'USD',
        totalCents: 0,
        holdExpiresAt: expires,
      );
      expect(hold.isHold, isTrue);
      expect(hold.holdRemaining()?.inMinutes, greaterThan(0));
      final past = Booking(
        id: 'b2',
        offerId: 'o1',
        definitionId: 'd1',
        orgId: 'org1',
        customerId: 'cust1',
        status: BookingStatuses.hold,
        paymentStatus: 'unpaid',
        quantity: 1,
        unitAmountCents: 0,
        currency: 'USD',
        totalCents: 0,
        holdExpiresAt: DateTime.now().subtract(const Duration(minutes: 1)),
      );
      expect(past.holdRemaining(), Duration.zero);
      final confirmed = Booking(
        id: 'b3',
        offerId: 'o1',
        definitionId: 'd1',
        orgId: 'org1',
        customerId: 'cust1',
        status: BookingStatuses.confirmed,
        paymentStatus: 'paid',
        quantity: 1,
        unitAmountCents: 0,
        currency: 'USD',
        totalCents: 0,
        holdExpiresAt: expires,
      );
      // A confirmed booking is no longer a hold, even with an expiry left set.
      expect(confirmed.holdRemaining(), isNull);
      expect(confirmed.isActive, isTrue);
      expect(confirmed.isPaid, isTrue);
    });
  });
}
