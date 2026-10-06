import 'json.dart';

/// `orders` (CONTRACTS §5.6). Money is integer minor units throughout.
class Order {
  const Order({
    required this.id,
    required this.number,
    required this.buyerId,
    required this.providerOrgId,
    required this.subtotalCents,
    required this.discountCents,
    required this.commissionCents,
    required this.totalCents,
    required this.currency,
    required this.status,
    required this.paymentStatus,
    this.bookingId,
    this.lineItems = const [],
    this.placedAt,
    this.createdAt,
    this.offerTitle = '',
    this.orgName = '',
  });

  final String id;
  final String number;
  final String buyerId;
  final String providerOrgId;
  final String? bookingId;

  /// draft | placed | paid | fulfilled | cancelled | refunded
  final String status;

  /// not_required | unpaid | paid | refunded | partially_refunded
  final String paymentStatus;
  final int subtotalCents;
  final int discountCents;
  final int commissionCents;
  final int totalCents;
  final String currency;
  final List<LineItem> lineItems;
  final DateTime? placedAt;
  final DateTime? createdAt;

  /// Flattened display fields, when the API provides them.
  final String offerTitle;
  final String orgName;

  bool get needsPayment =>
      paymentStatus == 'unpaid' && (status == 'placed' || status == 'draft');
  bool get isCancelled => status == 'cancelled' || status == 'refunded';

  static Order fromJson(Map<String, dynamic> json) => Order(
    id: Json.strOr(json, 'id'),
    number: Json.strOr(json, 'number'),
    buyerId: Json.strOr(json, 'buyer_id'),
    providerOrgId: Json.strOr(json, 'provider_org_id'),
    bookingId: Json.str(json, 'booking_id'),
    status: Json.strOr(json, 'status', 'draft'),
    paymentStatus: Json.strOr(json, 'payment_status', 'unpaid'),
    subtotalCents: Json.intOr(json, 'subtotal_cents'),
    discountCents: Json.intOr(json, 'discount_cents'),
    commissionCents: Json.intOr(json, 'commission_cents'),
    totalCents: Json.intOr(json, 'total_cents'),
    currency: Json.strOr(json, 'currency', 'USD'),
    lineItems: Json.list(
      json,
      'line_items',
    ).map(LineItem.fromJson).toList(growable: false),
    placedAt: Json.date(json, 'placed_at'),
    createdAt: Json.date(json, 'created_at'),
    offerTitle: Json.strOr(json, 'offer_title'),
    orgName: Json.strOr(json, 'org_name'),
  );
}

class LineItem {
  const LineItem({
    required this.description,
    required this.qty,
    required this.unitCents,
    required this.totalCents,
  });

  final String description;
  final int qty;
  final int unitCents;
  final int totalCents;

  static LineItem fromJson(Map<String, dynamic> json) => LineItem(
    description: Json.strOr(json, 'description'),
    qty: Json.intOr(json, 'qty', 1),
    unitCents: Json.intOr(json, 'unit_cents'),
    totalCents: Json.intOr(json, 'total_cents'),
  );
}

/// `payments` (§5.6). `mock` provider confirms immediately.
class Payment {
  const Payment({
    required this.id,
    required this.orderId,
    required this.providerKey,
    required this.amountCents,
    required this.currency,
    required this.status,
    this.clientSecret,
    this.providerPaymentId,
    this.failureReason,
    this.confirmedAt,
    this.createdAt,
  });

  final String id;
  final String orderId;

  /// mock | bank_transfer | manual | stripe-like
  final String providerKey;

  /// created | pending | succeeded | failed | canceled | refunded
  final String status;
  final int amountCents;
  final String currency;
  final String? clientSecret;
  final String? providerPaymentId;
  final String? failureReason;
  final DateTime? confirmedAt;
  final DateTime? createdAt;

  bool get isSucceeded => status == 'succeeded';
  bool get isTerminal =>
      isSucceeded ||
      status == 'failed' ||
      status == 'canceled' ||
      status == 'refunded';

  static Payment fromJson(Map<String, dynamic> json) => Payment(
    id: Json.strOr(json, 'id'),
    orderId: Json.strOr(json, 'order_id'),
    providerKey: Json.strOr(json, 'provider_key', 'mock'),
    status: Json.strOr(json, 'status', 'created'),
    amountCents: Json.intOr(json, 'amount_cents'),
    currency: Json.strOr(json, 'currency', 'USD'),
    clientSecret: Json.str(json, 'client_secret'),
    providerPaymentId: Json.str(json, 'provider_payment_id'),
    failureReason: Json.str(json, 'failure_reason'),
    confirmedAt: Json.date(json, 'confirmed_at'),
    createdAt: Json.date(json, 'created_at'),
  );
}

/// `refunds` (§5.6) — created automatically by the server on cancellation
/// when the policy band refunds a portion.
class Refund {
  const Refund({
    required this.id,
    required this.paymentId,
    required this.orderId,
    required this.amountCents,
    required this.currency,
    required this.status,
    this.reason,
    this.processedAt,
  });

  final String id;
  final String paymentId;
  final String orderId;

  /// pending | succeeded | failed
  final String status;
  final int amountCents;
  final String currency;
  final String? reason;
  final DateTime? processedAt;

  static Refund fromJson(Map<String, dynamic> json) => Refund(
    id: Json.strOr(json, 'id'),
    paymentId: Json.strOr(json, 'payment_id'),
    orderId: Json.strOr(json, 'order_id'),
    status: Json.strOr(json, 'status', 'pending'),
    amountCents: Json.intOr(json, 'amount_cents'),
    currency: Json.strOr(json, 'currency', 'USD'),
    reason: Json.str(json, 'reason'),
    processedAt: Json.date(json, 'processed_at'),
  );
}
