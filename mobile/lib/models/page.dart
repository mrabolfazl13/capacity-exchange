import 'json.dart';

/// List envelope: `{ items, total, limit, offset }` (docs/CONTRACTS.md §2).
class Page<T> {
  const Page({
    required this.items,
    required this.total,
    required this.limit,
    required this.offset,
  });

  final List<T> items;
  final int total;
  final int limit;
  final int offset;

  bool get hasMore => offset + items.length < total;

  static Page<T> fromJson<T>(
    dynamic json,
    T Function(Map<String, dynamic>) mapper,
  ) {
    if (json is List) {
      // Tolerate raw arrays if a server endpoint deviates.
      final items = json
          .whereType<Map>()
          .map((e) => mapper(Map<String, dynamic>.from(e)))
          .toList();
      return Page(items: items, total: items.length, limit: items.length, offset: 0);
    }
    if (json is! Map) {
      return const Page(items: [], total: 0, limit: 0, offset: 0);
    }
    final m = Map<String, dynamic>.from(json);
    return Page(
      items: Json.list(m, 'items').map(mapper).toList(growable: false),
      total: Json.intOr(m, 'total', 0),
      limit: Json.intOr(m, 'limit', 20),
      offset: Json.intOr(m, 'offset', 0),
    );
  }
}
