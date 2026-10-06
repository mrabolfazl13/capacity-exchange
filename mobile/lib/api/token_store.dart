/// Token storage abstraction. Production uses flutter_secure_storage
/// (refresh token stored per CONTRACTS §3 / §12); tests inject the in-memory
/// implementation so no platform channel is needed.
abstract class TokenStore {
  Future<void> save({String? accessToken, String? refreshToken});
  Future<String?> readAccessToken();
  Future<String?> readRefreshToken();
  Future<void> clear();
}

class InMemoryTokenStore implements TokenStore {
  String? accessToken;
  String? refreshToken;

  @override
  Future<void> clear() async {
    accessToken = null;
    refreshToken = null;
  }

  @override
  Future<String?> readAccessToken() async => accessToken;

  @override
  Future<String?> readRefreshToken() async => refreshToken;

  @override
  Future<void> save({String? accessToken, String? refreshToken}) async {
    if (accessToken != null) this.accessToken = accessToken;
    if (refreshToken != null) this.refreshToken = refreshToken;
  }
}
