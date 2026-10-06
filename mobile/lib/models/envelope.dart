import 'json.dart';

/// The list envelope `{ items, total, limit, offset }` (docs/CONTRACTS.md §2) —
/// the same shape the desktop client calls `ListEnvelope<T>`.
///
/// Named to match the contract rather than `Page`, which Flutter already owns.
class ListEnvelope<T> {
  const ListEnvelope({
    required this.items,
    required this.total,
    required this.limit,
    required this.offset,
  });

  /// A bare array tolerated as a degenerate envelope: everything the server
  /// sent, in one page.
  factory ListEnvelope.single(List<T> items) => ListEnvelope(
    items: items,
    total: items.length,
    limit: items.length,
    offset: 0,
  );

  factory ListEnvelope.fromJson(
    dynamic json,
    T Function(Map<String, dynamic>) mapper,
  ) {
    if (json is List) {
      // Tolerate raw arrays if a server endpoint deviates.
      return ListEnvelope.single(
        json
            .whereType<Map>()
            .map((e) => mapper(Map<String, dynamic>.from(e)))
            .toList(growable: false),
      );
    }
    if (json is! Map) {
      return ListEnvelope<T>(items: const [], total: 0, limit: 0, offset: 0);
    }
    final m = Map<String, dynamic>.from(json);
    return ListEnvelope<T>(
      items: Json.list(m, 'items').map(mapper).toList(growable: false),
      total: Json.intOr(m, 'total', 0),
      limit: Json.intOr(m, 'limit', 20),
      offset: Json.intOr(m, 'offset', 0),
    );
  }

  final List<T> items;
  final int total;
  final int limit;
  final int offset;

  bool get hasMore => offset + items.length < total;
}
