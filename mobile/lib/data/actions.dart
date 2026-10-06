import '../models/booking.dart';
import '../models/offer.dart';
import '../models/user.dart';

/// Which CTAs a booking may show.
///
/// The server owns the state machine (docs/CONTRACTS.md §5.5); this only
/// mirrors it so a screen does not offer a verb the caller cannot perform. A
/// wrong guess here is not a correctness problem — the route still answers 400
/// `invalid_state_transition` — but it is a confusing one, so the rules live in
/// one file and are unit-tested rather than inlined in widgets.
enum BookingAction { pay, confirm, start, complete, cancel, review, dispute }

bool _isProviderSide(User user, Booking booking) =>
    user.activeOrgId != null &&
    user.activeOrgId == booking.orgId &&
    user.hasProviderSide;

bool _isCustomerSide(User user, Booking booking) =>
    user.id == booking.customerId;

List<BookingAction> allowedBookingActions(Booking booking, User? user) {
  if (user == null) return const [];
  final provider = _isProviderSide(user, booking);
  final customer = _isCustomerSide(user, booking);
  final needsPayment = booking.paymentStatus == 'unpaid';

  switch (booking.status) {
    case BookingStatuses.hold:
      return [
        if (provider) ...[BookingAction.confirm, BookingAction.cancel],
        if (customer) ...[
          BookingAction.cancel,
          needsPayment ? BookingAction.pay : BookingAction.confirm,
        ],
      ];
    case BookingStatuses.draft:
      return [
        if (provider || customer) BookingAction.cancel,
        if (provider) BookingAction.confirm,
      ];
    case BookingStatuses.confirmed:
      return [
        if (provider) BookingAction.start,
        if (customer) BookingAction.cancel,
        if (customer) BookingAction.dispute,
      ];
    case BookingStatuses.inProgress:
      return [
        if (provider) BookingAction.complete,
        if (customer) BookingAction.dispute,
      ];
    case BookingStatuses.completed:
      // Whether a review already exists is the server's answer, so the CTA is
      // offered and a duplicate is rejected — never silently hidden.
      return [
        if (customer) ...[BookingAction.review, BookingAction.dispute],
      ];
    default:
      return const [];
  }
}

/// Cancellation refund preview (§5.6): the highest band whose
/// `hours_before` is still ahead of us. Display only — the server recomputes
/// authoritatively on cancel.
int refundPctForCancellation(
  List<CancellationBand> policy,
  DateTime? windowStart, {
  DateTime? now,
}) {
  if (policy.isEmpty || windowStart == null) return 0;
  final hoursUntil =
      windowStart
          .toUtc()
          .difference(now?.toUtc() ?? DateTime.now().toUtc())
          .inMinutes /
      60.0;
  final sorted = [...policy]
    ..sort((a, b) => b.hoursBefore.compareTo(a.hoursBefore));
  for (final band in sorted) {
    if (hoursUntil >= band.hoursBefore) return band.refundPct;
  }
  return 0;
}

/// Pre-flight checks on a hold request, returned as localization keys so the
/// message is written in the reader's language.
///
/// These are sanity bounds, not rules: the same offer limits are re-checked by
/// the server, and a hold that passes here can still be refused because
/// someone else took the window first.
List<String> holdIssues({
  required Offer offer,
  required DateTime start,
  required DateTime end,
  required int quantity,
  DateTime? now,
}) {
  final nowTs = now ?? DateTime.now();
  final errors = <String>[];
  if (!end.isAfter(start)) errors.add('err_end_after_start');
  if (start.isBefore(nowTs.subtract(const Duration(minutes: 1)))) {
    errors.add('err_window_past');
  }
  if (quantity < 1) errors.add('err_quantity_min');
  final min = offer.minQuantity;
  final max = offer.maxQuantity;
  if (min != null && quantity < min) errors.add('err_quantity_min');
  if (max != null && quantity > max) errors.add('err_quantity_max');
  final minutes = end.difference(start).inMinutes;
  final minDuration = offer.minDurationMinutes;
  final maxDuration = offer.maxDurationMinutes;
  if (minDuration != null && minutes < minDuration) {
    errors.add('err_duration_min');
  }
  if (maxDuration != null && minutes > maxDuration) {
    errors.add('err_duration_max');
  }
  return errors;
}
