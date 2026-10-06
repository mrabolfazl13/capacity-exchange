import 'offer.dart' show CancellationBand;

import 'json.dart';

/// Booking lifecycle statuses (CONTRACTS §5.4) — the server owns the state
/// machine (§5.5). The client only names these tokens; the CTA rules that read
/// them live in `data/actions.dart`, and any wrong guess still ends in a
/// server 400 `invalid_state_transition`.
abstract final class BookingStatuses {
  static const draft = 'draft';
  static const hold = 'hold';
  static const confirmed = 'confirmed';
  static const inProgress = 'in_progress';
  static const completed = 'completed';
  static const cancelled = 'cancelled';
  static const expired = 'expired';
  static const disputed = 'disputed';
}

/// A booking row (holds are bookings in status `hold`, §5.4 — one table).
class Booking {
  const Booking({
    required this.id,
    required this.offerId,
    required this.definitionId,
    required this.orgId,
    required this.customerId,
    required this.status,
    required this.paymentStatus,
    required this.quantity,
    required this.unitAmountCents,
    required this.currency,
    required this.totalCents,
    this.windowStart,
    this.windowEnd,
    this.holdExpiresAt,
    this.requestFingerprint,
    this.offerTitle = '',
    this.orgName = '',
    this.unitLabel = '',
    this.resourceName = '',
    this.cancelReason,
    this.cancelledAt,
    this.confirmedAt,
    this.startedAt,
    this.completedAt,
    this.disputeOpenedAt,
    this.sourceMatchId,
    this.createdAt,
    this.meta = const {},
    this.cancellationPolicy = const [],
  });

  final String id;
  final String offerId;
  final String definitionId;
  final String orgId;
  final String customerId;
  final String status;
  final String paymentStatus;
  final DateTime? windowStart;
  final DateTime? windowEnd;
  final int quantity;
  final int unitAmountCents;
  final String currency;
  final int totalCents;

  /// Only meaningful while status == hold.
  final DateTime? holdExpiresAt;
  final String? requestFingerprint;

  /// Flattened display fields the list/detail endpoints may include.
  final String offerTitle;
  final String orgName;
  final String unitLabel;
  final String resourceName;

  final String? cancelReason;
  final DateTime? cancelledAt;
  final DateTime? confirmedAt;
  final DateTime? startedAt;
  final DateTime? completedAt;
  final DateTime? disputeOpenedAt;
  final String? sourceMatchId;
  final DateTime? createdAt;
  final Map<String, dynamic> meta;

  /// Server may echo the offer policy for the cancellation preview.
  final List<CancellationBand> cancellationPolicy;

  bool get isHold => status == BookingStatuses.hold;
  bool get isActive =>
      status == BookingStatuses.hold ||
      status == BookingStatuses.draft ||
      status == BookingStatuses.confirmed ||
      status == BookingStatuses.inProgress;

  bool get isPaid => paymentStatus == 'paid' || paymentStatus == 'not_required';

  /// Remaining hold time (never negative); null when not holding.
  Duration? holdRemaining([DateTime? now]) {
    final expires = holdExpiresAt;
    if (expires == null || !isHold) return null;
    final left = expires.difference(now ?? DateTime.now().toUtc());
    return left.isNegative ? Duration.zero : left;
  }

  static Booking fromJson(Map<String, dynamic> json) {
    final bands = Json.list(
      json,
      'cancellation_policy',
    ).map(CancellationBand.fromJson).toList(growable: false);
    return Booking(
      id: Json.strOr(json, 'id'),
      offerId: Json.strOr(json, 'offer_id'),
      definitionId: Json.strOr(json, 'definition_id'),
      orgId: Json.strOr(json, 'org_id'),
      customerId: Json.strOr(json, 'customer_id'),
      status: Json.strOr(json, 'status', BookingStatuses.hold),
      paymentStatus: Json.strOr(json, 'payment_status', 'unpaid'),
      windowStart: Json.date(json, 'window_start'),
      windowEnd: Json.date(json, 'window_end'),
      quantity: Json.intOr(json, 'quantity', 1),
      unitAmountCents: Json.intOr(json, 'unit_amount_cents'),
      currency: Json.strOr(json, 'currency', 'USD'),
      totalCents: Json.intOr(json, 'total_cents'),
      holdExpiresAt: Json.date(json, 'hold_expires_at'),
      requestFingerprint: Json.str(json, 'request_fingerprint'),
      offerTitle: Json.strOr(json, 'offer_title'),
      orgName: Json.strOr(json, 'org_name'),
      unitLabel: Json.strOr(json, 'unit_label'),
      resourceName: Json.strOr(json, 'resource_name'),
      cancelReason: Json.str(json, 'cancel_reason'),
      cancelledAt: Json.date(json, 'cancelled_at'),
      confirmedAt: Json.date(json, 'confirmed_at'),
      startedAt: Json.date(json, 'started_at'),
      completedAt: Json.date(json, 'completed_at'),
      disputeOpenedAt: Json.date(json, 'dispute_opened_at'),
      sourceMatchId: Json.str(json, 'source_match_id'),
      createdAt: Json.date(json, 'created_at'),
      meta: Json.map(json, 'meta'),
      cancellationPolicy: bands,
    );
  }
}

/// `booking_status_events` row (GET /bookings/{id}/timeline).
class BookingEvent {
  const BookingEvent({
    required this.id,
    required this.toStatus,
    this.fromStatus,
    this.reason,
    this.actorUserId,
    this.occurredAt,
  });

  final String id;
  final String toStatus;
  final String? fromStatus;
  final String? reason;
  final String? actorUserId;
  final DateTime? occurredAt;

  static BookingEvent fromJson(Map<String, dynamic> json) => BookingEvent(
    id: Json.strOr(json, 'id'),
    toStatus: Json.strOr(json, 'to_status'),
    fromStatus: Json.str(json, 'from_status'),
    reason: Json.str(json, 'reason'),
    actorUserId: Json.str(json, 'actor_user_id'),
    occurredAt: Json.date(json, 'occurred_at') ?? Json.date(json, 'created_at'),
  );
}

/// Cancellation band lives in models/offer.dart (`CancellationBand`); the
/// server echoes `offer.cancellation_policy` on bookings so the cancellation
/// preview needs no extra round trip.
extension CancellationBandPreview on List<CancellationBand> {
  /// Display-only preview: which band applies for the hours before start that
  /// remain right now. The server recomputes authoritatively on cancel (§5.6).
  CancellationBand? applyFor(int hoursBeforeStart) {
    if (isEmpty) return null;
    final sorted = [...this]
      ..sort((a, b) => b.hoursBefore.compareTo(a.hoursBefore));
    for (final band in sorted) {
      if (hoursBeforeStart >= band.hoursBefore) return band;
    }
    return sorted.last;
  }
}
