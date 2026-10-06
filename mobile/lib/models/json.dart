/// Helpers for the shared DTO conventions (docs/CONTRACTS.md §1):
/// snake_case JSON, UUID strings, ISO-8601 UTC timestamps, integer minor-unit
/// money + ISO currency. Parsing is tolerant of missing optional fields so a
/// single absent field never crashes a screen.
library;

abstract final class Json {
  static String? str(Map<String, dynamic> m, String key) {
    final v = m[key];
    return v == null ? null : '$v';
  }

  static String strOr(
    Map<String, dynamic> m,
    String key, [
    String fallback = '',
  ]) {
    final v = m[key];
    return v == null ? fallback : '$v';
  }

  static int? intOrNull(Map<String, dynamic> m, String key) {
    final v = m[key];
    if (v is int) return v;
    if (v is double) return v.round();
    if (v is String) return int.tryParse(v);
    return null;
  }

  static int intOr(Map<String, dynamic> m, String key, [int fallback = 0]) =>
      intOrNull(m, key) ?? fallback;

  static double? doubleOrNull(Map<String, dynamic> m, String key) {
    final v = m[key];
    if (v is num) return v.toDouble();
    if (v is String) return double.tryParse(v);
    return null;
  }

  static bool boolOr(
    Map<String, dynamic> m,
    String key, [
    bool fallback = false,
  ]) {
    final v = m[key];
    if (v is bool) return v;
    if (v is num) return v != 0;
    if (v is String) return v == 'true' || v == '1';
    return fallback;
  }

  static DateTime? date(Map<String, dynamic> m, String key) {
    final v = m[key];
    if (v is String) return DateTime.tryParse(v)?.toUtc();
    return null;
  }

  static DateTime? time(Map<String, dynamic> m, String key, DateTime day) {
    final v = m[key];
    if (v is! String) return null;
    final parts = v.split(':');
    if (parts.length < 2) return null;
    final h = int.tryParse(parts[0]);
    final mi = int.tryParse(parts[1]);
    if (h == null || mi == null) return null;
    return DateTime(day.year, day.month, day.day, h, mi);
  }

  static Map<String, dynamic> map(Map<String, dynamic> m, String key) {
    final v = m[key];
    if (v is Map<String, dynamic>) return v;
    if (v is Map) return Map<String, dynamic>.from(v);
    return {};
  }

  static List<Map<String, dynamic>> list(Map<String, dynamic> m, String key) {
    final v = m[key];
    if (v is List) {
      return v
          .whereType<Map>()
          .map((e) => Map<String, dynamic>.from(e))
          .toList(growable: false);
    }
    return const [];
  }

  static List<String> strings(Map<String, dynamic> m, String key) {
    final v = m[key];
    if (v is List) return v.map((e) => '$e').toList(growable: false);
    return const [];
  }
}
