import 'json.dart';

/// `disputes` (CONTRACTS §5.7). Customers/providers open them; support and
/// platform admins resolve them (`POST /disputes/{id}/resolve`).
class Dispute {
  const Dispute({
    required this.id,
    required this.bookingId,
    required this.kind,
    required this.status,
    required this.description,
    this.orgId,
    this.complainantId,
    this.resolutionNote,
    this.createdAt,
    this.resolvedAt,
  });

  final String id;
  final String bookingId;
  final String? orgId;
  final String? complainantId;

  /// quality | no_show | payment | damage | other
  final String kind;

  /// open | under_review | resolved_refund | resolved_partial |
  /// resolved_no_fault | closed
  final String status;
  final String description;
  final String? resolutionNote;
  final DateTime? createdAt;
  final DateTime? resolvedAt;

  bool get isOpen => status == 'open' || status == 'under_review';

  static Dispute fromJson(Map<String, dynamic> json) => Dispute(
        id: Json.strOr(json, 'id'),
        bookingId: Json.strOr(json, 'booking_id'),
        orgId: Json.str(json, 'org_id'),
        complainantId: Json.str(json, 'complainant_id'),
        kind: Json.strOr(json, 'kind', 'other'),
        status: Json.strOr(json, 'status', 'open'),
        description: Json.strOr(json, 'description'),
        resolutionNote: Json.str(json, 'resolution_note'),
        createdAt: Json.date(json, 'created_at'),
        resolvedAt: Json.date(json, 'resolved_at'),
      );
}
