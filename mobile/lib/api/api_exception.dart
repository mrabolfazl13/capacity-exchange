/// Typed error for the shared response envelope (docs/CONTRACTS.md §2):
/// `{ "error": { "code", "message", "details", "request_id" } }`
class ApiException implements Exception {
  ApiException({
    required this.statusCode,
    required this.code,
    required this.message,
    this.details,
    this.requestId,
  });

  /// HTTP status (0 = transport-level failure, never got a response).
  final int statusCode;

  /// Machine code from the envelope taxonomy (§2), e.g. `hold_expired`,
  /// `capacity_exceeded`, `network_error`.
  final String code;

  /// Human-readable message from the server.
  final String message;

  final Map<String, dynamic>? details;
  final String? requestId;

  bool get isUnauthorized => statusCode == 401;
  bool get isForbidden => statusCode == 403;
  bool get isNotFound => statusCode == 404;
  bool get isConflict => statusCode == 409;
  bool get isValidationError => code == 'validation_error';
  bool get isHoldExpired => code == 'hold_expired';
  bool get isCapacityExceeded => code == 'capacity_exceeded';
  bool get isNoAvailability => code == 'no_availability';
  bool get isRateLimited => statusCode == 429;
  bool get isDependencyUnavailable => statusCode == 503;

  int? get retryAfterSeconds {
    final v = details?['retry_after_seconds'];
    return v is int ? v : null;
  }

  /// 422/400 validation: `details.field_errors = [{field, message}]`
  Map<String, String> get fieldErrors {
    final raw = details?['field_errors'];
    if (raw is! List) return const {};
    final out = <String, String>{};
    for (final e in raw) {
      if (e is Map && e['field'] != null) {
        out['${e['field']}'] = '${e['message'] ?? 'invalid'}';
      }
    }
    return out;
  }

  @override
  String toString() => 'ApiException($statusCode $code: $message)';
}
