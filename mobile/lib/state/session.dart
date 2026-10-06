import 'package:flutter/foundation.dart';

import '../api/api_client.dart';
import '../api/api_exception.dart';
import '../models/user.dart';

/// The signed-in identity, and the only place that knows a session exists.
///
/// The refresh token lives in secure storage (`SecureTokenStore`); this object
/// holds the parsed `/auth/me` answer so role-gated UI has something to read
/// before the first request resolves. Nothing here decides permissions — the
/// server does — this only decides which screens are worth showing.
class Session extends ChangeNotifier {
  Session([ApiClient? api]) : _client = api;

  ApiClient? _client;

  ApiClient get api => _client ??= ApiClient(onSessionExpired: _onExpired);

  User? _user;
  bool _restoring = true;
  String? _notice;

  User? get user => _user;
  bool get restoring => _restoring;
  bool get signedIn => _user != null;
  String? get notice => _notice;

  bool get isProvider => _user?.hasProviderSide ?? false;
  bool get isPlatformAdmin => _user?.isPlatformAdmin ?? false;
  String? get activeOrgId => _user?.activeOrgId;

  void _onExpired() {
    _user = null;
    _notice = 'session_expired';
    notifyListeners();
  }

  /// The account behind the current token (§3).
  Future<User> me() async {
    final json = await api.get('/auth/me');
    return User.fromJson(Map<String, dynamic>.from(json as Map));
  }

  void clearNotice() {
    if (_notice == null) return;
    _notice = null;
    notifyListeners();
  }

  /// Reads the stored token and, when present, the account behind it.
  /// A failure here is not fatal: the login screen is a valid place to land.
  Future<void> restore() async {
    try {
      final access = await api.tokenStore.readAccessToken();
      if (access != null && access.isNotEmpty) {
        _user = await me();
      }
    } on ApiException catch (e) {
      // 401 means the stored pair is dead; anything else is a connectivity
      // problem we should say so about rather than bounce to a login screen.
      _user = null;
      if (e.statusCode != 401 && e.statusCode != 0) _notice = e.message;
    } catch (_) {
      _user = null;
    } finally {
      _restoring = false;
      notifyListeners();
    }
  }

  /// Returns null on success, or the message to show. The server's own words
  /// come back verbatim because they are the accurate ones.
  Future<String?> login(String email, String password) async {
    try {
      final session = await api.login(email.trim(), password);
      await _adopt(session);
      return null;
    } on ApiException catch (e) {
      return e.message;
    }
  }

  Future<String?> register({
    required String email,
    required String password,
    required String fullName,
    String? phone,
    String? organizationName,
  }) async {
    try {
      final session = await api.registerCustomer(
        email: email.trim(),
        password: password,
        fullName: fullName.trim(),
        phone: phone?.trim().isEmpty ?? true ? null : phone!.trim(),
      );
      await _adopt(session);
      // A provider signs up by owning an organization; the account alone has
      // no tenant to list capacity under (§4 tenancy).
      final org = organizationName?.trim() ?? '';
      if (org.isNotEmpty) {
        await api.createOrganization({'name': org, 'slug': _slug(org)});
        _user = await me();
      }
      return null;
    } on ApiException catch (e) {
      return e.message;
    }
  }

  Future<void> logout() async {
    await api.logout();
    _user = null;
    notifyListeners();
  }

  Future<void> _adopt(Map<String, dynamic> session) async {
    final payload = session['user'];
    if (payload is Map) {
      _user = User.fromJson(Map<String, dynamic>.from(payload));
    } else {
      _user = await me();
    }
    _notice = null;
    _restoring = false;
    notifyListeners();
  }

  static String _slug(String name) => name
      .toLowerCase()
      .replaceAll(RegExp(r'[^a-z0-9]+'), '-')
      .replaceAll(RegExp(r'^-+|-+$'), '');
}
