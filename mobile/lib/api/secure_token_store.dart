import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import 'token_store.dart';

/// flutter_secure_storage-backed refresh + access tokens
/// (docs/CONTRACTS.md §12: mobile uses flutter_secure_storage).
class SecureTokenStore implements TokenStore {
  SecureTokenStore({FlutterSecureStorage? storage})
    : _storage =
          storage ??
          const FlutterSecureStorage(
            aOptions: AndroidOptions(encryptedSharedPreferences: true),
          );

  final FlutterSecureStorage _storage;

  static const _kAccess = 'ce_access_token';
  static const _kRefresh = 'ce_refresh_token';

  @override
  Future<void> clear() async {
    await _storage.delete(key: _kAccess);
    await _storage.delete(key: _kRefresh);
  }

  @override
  Future<String?> readAccessToken() => _storage.read(key: _kAccess);

  @override
  Future<String?> readRefreshToken() => _storage.read(key: _kRefresh);

  @override
  Future<void> save({String? accessToken, String? refreshToken}) async {
    if (accessToken != null) {
      await _storage.write(key: _kAccess, value: accessToken);
    }
    if (refreshToken != null) {
      await _storage.write(key: _kRefresh, value: refreshToken);
    }
  }
}
