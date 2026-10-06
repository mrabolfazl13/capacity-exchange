/// Compile-time environment configuration.
///
/// Build with `--dart-define=API_BASE_URL=http://10.0.2.2:8000/api/v1`
/// (Android emulator default) or
/// `--dart-define=API_BASE_URL=http://127.0.0.1:8000/api/v1` for the desktop
/// runner / physical device on the same LAN.
class Env {
  const Env._();

  static const String apiBaseUrl = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'http://10.0.2.2:8000/api/v1',
  );

  /// Request timeout for API calls.
  static const Duration apiTimeout = Duration(seconds: 20);

  /// Polling interval for unread-notification badge (SSE fallback per
  /// docs/CONTRACTS.md §1).
  static const Duration notificationPollInterval = Duration(seconds: 30);
}
