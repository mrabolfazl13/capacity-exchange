import 'json.dart';
import 'offer.dart';

/// `capacity_resources` + `capacity_definitions` flattened for the provider
/// workspace (CONTRACTS §5.2).
class Resource {
  const Resource({
    required this.id,
    required this.orgId,
    required this.name,
    required this.categoryId,
    required this.capacityMode,
    required this.status,
    this.categoryKey = '',
    this.categoryLabel = '',
    this.description = '',
    this.city,
    this.country,
    this.line1,
    this.timezone = 'UTC',
    this.definitions = const [],
  });

  final String id;
  final String orgId;
  final String name;
  final String categoryId;
  final String categoryKey;
  final String categoryLabel;

  /// scheduled | quantity | open_ended
  final String capacityMode;

  /// draft | active | archived
  final String status;
  final String description;
  final String? city;
  final String? country;
  final String? line1;
  final String timezone;
  final List<CapacityDefinition> definitions;

  static Resource fromJson(Map<String, dynamic> json) {
    final address = Json.map(json, 'address');
    return Resource(
      id: Json.strOr(json, 'id'),
      orgId: Json.strOr(json, 'org_id'),
      name: Json.strOr(json, 'name'),
      categoryId: Json.strOr(json, 'category_id'),
      categoryKey: Json.strOr(json, 'category_key'),
      categoryLabel: Json.strOr(json, 'category_label'),
      capacityMode: Json.strOr(json, 'capacity_mode', 'quantity'),
      status: Json.strOr(json, 'status', 'draft'),
      description: Json.strOr(json, 'description'),
      city: Json.str(json, 'city') ?? _s(address['city']),
      country: Json.str(json, 'country') ?? _s(address['country']),
      line1: Json.str(json, 'line1') ?? _s(address['line1']),
      timezone: Json.strOr(json, 'timezone', 'UTC'),
      definitions:
          Json.list(json, 'definitions').map(CapacityDefinition.fromJson).toList(growable: false),
    );
  }

  static String? _s(dynamic v) => v == null ? null : '$v';
}

/// `capacity_definitions` — the bookable unit of a resource.
class CapacityDefinition {
  const CapacityDefinition({
    required this.id,
    required this.resourceId,
    required this.name,
    required this.unitLabel,
    required this.minQuantity,
    required this.maxQuantity,
    this.slotDurationMinutes,
    this.bufferBeforeMinutes = 0,
    this.bufferAfterMinutes = 0,
    this.isActive = true,
  });

  final String id;
  final String resourceId;
  final String name;
  final String unitLabel;
  final int minQuantity;
  final int maxQuantity;

  /// Required for `scheduled` capacity mode.
  final int? slotDurationMinutes;
  final int bufferBeforeMinutes;
  final int bufferAfterMinutes;
  final bool isActive;

  static CapacityDefinition fromJson(Map<String, dynamic> json) => CapacityDefinition(
        id: Json.strOr(json, 'id'),
        resourceId: Json.strOr(json, 'resource_id'),
        name: Json.strOr(json, 'name'),
        unitLabel: Json.strOr(json, 'unit_label'),
        minQuantity: Json.intOr(json, 'min_quantity', 1),
        maxQuantity: Json.intOr(json, 'max_quantity', 1),
        slotDurationMinutes: Json.intOrNull(json, 'slot_duration_minutes'),
        bufferBeforeMinutes: Json.intOr(json, 'buffer_before_minutes'),
        bufferAfterMinutes: Json.intOr(json, 'buffer_after_minutes'),
        isActive: Json.boolOr(json, 'is_active', true),
      );
}

/// An offer owned by the signed-in provider (same DTO as the marketplace
/// [Offer], with the provider-side status verbs available).
typedef ProviderOffer = Offer;
