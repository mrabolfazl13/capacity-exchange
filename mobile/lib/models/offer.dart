import 'json.dart';
import 'page.dart';

/// Marketplace offer flattened for display (offers joined with
/// definition/resource/org, CONTRACTS §5.3).
class Offer {
  const Offer({
    required this.id,
    required this.title,
    required this.description,
    required this.definitionId,
    required this.orgId,
    required this.pricingMode,
    required this.unitAmountCents,
    required this.currency,
    required this.bookingMode,
    required this.holdMinutes,
    this.resourceId,
    this.categoryId,
    this.categoryLabel,
    this.orgName = '',
    this.resourceName = '',
    this.unitLabel = '',
    this.minQuantity,
    this.maxQuantity,
    this.slotDurationMinutes,
    this.minLeadTimeMinutes = 0,
    this.maxLeadTimeDays,
    this.minDurationMinutes,
    this.maxDurationMinutes,
    this.cancellationPolicy = const [],
    this.status = 'published',
    this.city,
    this.country,
    this.line1,
    this.ratingAvg,
    this.ratingCount = 0,
    this.photos = const [],
  });

  final String id;
  final String title;
  final String description;
  final String definitionId;
  final String orgId;
  final String? resourceId;
  final String? categoryId;
  final String? categoryLabel;

  /// per_unit_time | per_quantity | flat
  final String pricingMode;
  final int unitAmountCents;
  final String currency;

  /// request_confirm | instant
  final String bookingMode;
  final int holdMinutes;

  final String orgName;
  final String resourceName;
  final String unitLabel;
  final int? minQuantity;
  final int? maxQuantity;
  final int? slotDurationMinutes;
  final int minLeadTimeMinutes;
  final int? maxLeadTimeDays;
  final int? minDurationMinutes;
  final int? maxDurationMinutes;
  final List<CancellationBand> cancellationPolicy;
  final String status;
  final String? city;
  final String? country;
  final String? line1;
  final double? ratingAvg;
  final int ratingCount;
  final List<String> photos;

  bool get isInstant => bookingMode == 'instant';
  bool get isFlat => pricingMode == 'flat';

  String get pricingLabel {
    switch (pricingMode) {
      case 'flat':
        return 'flat';
      case 'per_quantity':
        return 'per $unitLabel';
      default:
        return 'per hour / unit';
    }
  }

  static Offer fromJson(Map<String, dynamic> json) {
    final address = Json.map(json, 'address');
    final bands = Json.list(json, 'cancellation_policy')
        .map(CancellationBand.fromJson)
        .toList(growable: false);
    final photos = Json.list(json, 'photos').map((p) => Json.strOr(p, 'url')).where((u) => u.isNotEmpty).toList(growable: false);
    return Offer(
      id: Json.strOr(json, 'id'),
      title: Json.strOr(json, 'title'),
      description: Json.strOr(json, 'description'),
      definitionId: Json.strOr(json, 'definition_id'),
      orgId: Json.strOr(json, 'org_id'),
      resourceId: Json.str(json, 'resource_id'),
      categoryId: Json.str(json, 'category_id'),
      categoryLabel: Json.str(json, 'category_label'),
      orgName: Json.strOr(json, 'org_name'),
      resourceName: Json.strOr(json, 'resource_name', Json.strOr(json, 'resource__name')),
      unitLabel: Json.strOr(json, 'unit_label'),
      pricingMode: Json.strOr(json, 'pricing_mode', 'per_quantity'),
      unitAmountCents: Json.intOr(json, 'unit_amount_cents'),
      currency: Json.strOr(json, 'currency', 'USD'),
      bookingMode: Json.strOr(json, 'booking_mode', 'request_confirm'),
      holdMinutes: Json.intOr(json, 'hold_minutes', 15),
      minQuantity: Json.intOrNull(json, 'min_quantity'),
      maxQuantity: Json.intOrNull(json, 'max_quantity'),
      slotDurationMinutes: Json.intOrNull(json, 'slot_duration_minutes'),
      minLeadTimeMinutes: Json.intOr(json, 'min_lead_time_minutes'),
      maxLeadTimeDays: Json.intOrNull(json, 'max_lead_time_days'),
      minDurationMinutes: Json.intOrNull(json, 'min_duration_minutes'),
      maxDurationMinutes: Json.intOrNull(json, 'max_duration_minutes'),
      cancellationPolicy: bands,
      status: Json.strOr(json, 'status', 'published'),
      city: Json.str(json, 'city') ?? (address['city']?.toString()),
      country: Json.str(json, 'country') ?? (address['country']?.toString()),
      line1: Json.str(json, 'line1') ?? (address['line1']?.toString()),
      ratingAvg: Json.doubleOrNull(json, 'rating_avg') ?? Json.doubleOrNull(json, 'avg_rating'),
      ratingCount: Json.intOr(json, 'rating_count') + Json.intOr(json, 'review_count'),
      photos: photos,
    );
  }
}

/// `offer.cancellation_policy` band: {hours_before, refund_pct}.
class CancellationBand {
  const CancellationBand({required this.hoursBefore, required this.refundPct});

  final int hoursBefore;
  final int refundPct;

  static CancellationBand fromJson(Map<String, dynamic> json) =>
      CancellationBand(
        hoursBefore: Json.intOr(json, 'hours_before'),
        refundPct: Json.intOr(json, 'refund_pct'),
      );
}

class Review {
  const Review({
    required this.id,
    required this.rating,
    this.comment,
    this.reviewerName = '',
    this.providerReply,
    this.createdAt,
  });

  final String id;
  final int rating;
  final String? comment;
  final String reviewerName;
  final String? providerReply;
  final DateTime? createdAt;

  static Review fromJson(Map<String, dynamic> json) => Review(
        id: Json.strOr(json, 'id'),
        rating: Json.intOr(json, 'rating', 0).clamp(0, 5),
        comment: Json.str(json, 'comment'),
        reviewerName: Json.strOr(json, 'reviewer_name'),
        providerReply: Json.str(json, 'provider_reply'),
        createdAt: Json.date(json, 'created_at'),
      );
}

/// `GET /availability/free` item: concrete free window for a definition.
class FreeWindow {
  const FreeWindow({
    required this.start,
    required this.end,
    required this.freeQuantity,
  });

  final DateTime start;
  final DateTime end;
  final int freeQuantity;

  static FreeWindow fromJson(Map<String, dynamic> json) => FreeWindow(
        start: Json.date(json, 'window_start') ?? DateTime.now(),
        end: Json.date(json, 'window_end') ?? DateTime.now(),
        freeQuantity: Json.intOr(json, 'free_quantity', 1),
      );

  static Page<FreeWindow> page(dynamic json) =>
      Page<FreeWindow>.fromJson(json, FreeWindow.fromJson);
}

/// Recurring availability rule for detail display (§5.2; dow 0=Monday).
class RecurringRule {
  const RecurringRule({
    required this.dow,
    required this.startTime,
    required this.endTime,
    required this.quantity,
  });

  final int dow;
  final String startTime;
  final String endTime;
  final int quantity;

  static RecurringRule fromJson(Map<String, dynamic> json) => RecurringRule(
        dow: Json.intOr(json, 'dow'),
        startTime: Json.strOr(json, 'start_time', '--:--'),
        endTime: Json.strOr(json, 'end_time', '--:--'),
        quantity: Json.intOr(json, 'quantity', 1),
      );
}
