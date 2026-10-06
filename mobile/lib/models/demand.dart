import 'json.dart';

/// `demands` (CONTRACTS §5.3): a customer-posted request for capacity.
class Demand {
  const Demand({
    required this.id,
    required this.customerId,
    required this.description,
    required this.status,
    required this.quantity,
    this.orgId,
    this.categoryId,
    this.categoryLabel = '',
    this.city,
    this.desiredStart,
    this.desiredEnd,
    this.budgetMinCents,
    this.budgetMaxCents,
    this.currency = 'USD',
    this.expiresAt,
    this.createdAt,
  });

  final String id;
  final String customerId;
  final String? orgId;
  final String? categoryId;
  final String categoryLabel;
  final String description;
  final String? city;
  final DateTime? desiredStart;
  final DateTime? desiredEnd;
  final int quantity;
  final int? budgetMinCents;
  final int? budgetMaxCents;
  final String currency;

  /// open | matched | closed | cancelled
  final String status;
  final DateTime? expiresAt;
  final DateTime? createdAt;

  bool get isOpen => status == 'open' || status == 'matched';

  static Demand fromJson(Map<String, dynamic> json) {
    final address = Json.map(json, 'address');
    return Demand(
      id: Json.strOr(json, 'id'),
      customerId: Json.strOr(json, 'customer_id'),
      orgId: Json.str(json, 'org_id'),
      categoryId: Json.str(json, 'category_id'),
      categoryLabel: Json.strOr(json, 'category_label'),
      description: Json.strOr(json, 'description'),
      city: Json.str(json, 'city') ?? address['city']?.toString(),
      desiredStart: Json.date(json, 'desired_start'),
      desiredEnd: Json.date(json, 'desired_end'),
      quantity: Json.intOr(json, 'quantity', 1),
      budgetMinCents: Json.intOrNull(json, 'budget_min_cents'),
      budgetMaxCents: Json.intOrNull(json, 'budget_max_cents'),
      currency: Json.strOr(json, 'currency', 'USD'),
      status: Json.strOr(json, 'status', 'open'),
      expiresAt: Json.date(json, 'expires_at'),
      createdAt: Json.date(json, 'created_at'),
    );
  }
}

/// `matches` (§5.3): an offer suggested against a demand. Providers
/// accept/decline; accepting creates the booking server-side.
class Match {
  const Match({
    required this.id,
    required this.demandId,
    required this.offerId,
    required this.status,
    required this.score,
    required this.reasons,
    this.offerTitle = '',
    this.orgName = '',
    this.unitAmountCents = 0,
    this.currency = 'USD',
    this.providerActionAt,
    this.createdAt,
  });

  final String id;
  final String demandId;
  final String offerId;

  /// suggested | accepted | declined | expired
  final String status;

  /// 0..100 relevance score from the matching engine (display only).
  final double score;
  final List<String> reasons;
  final String offerTitle;
  final String orgName;
  final int unitAmountCents;
  final String currency;
  final DateTime? providerActionAt;
  final DateTime? createdAt;

  bool get isSuggested => status == 'suggested';

  static Match fromJson(Map<String, dynamic> json) => Match(
        id: Json.strOr(json, 'id'),
        demandId: Json.strOr(json, 'demand_id'),
        offerId: Json.strOr(json, 'offer_id'),
        status: Json.strOr(json, 'status', 'suggested'),
        score: Json.doubleOrNull(json, 'score') ?? 0,
        reasons: Json.strings(json, 'reasons'),
        offerTitle: Json.strOr(json, 'offer_title'),
        orgName: Json.strOr(json, 'org_name'),
        unitAmountCents: Json.intOr(json, 'unit_amount_cents'),
        currency: Json.strOr(json, 'currency', 'USD'),
        providerActionAt: Json.date(json, 'provider_action_at'),
        createdAt: Json.date(json, 'created_at'),
      );
}
