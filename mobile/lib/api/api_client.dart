import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import '../core/env.dart';
import 'api_exception.dart';
import 'secure_token_store.dart';
import 'token_store.dart';

/// Shared REST client: bearer auth + single-refresh-on-401 queue + error
/// envelope parsing per docs/CONTRACTS.md §2/§3.
///
/// No business rules live here — the client only transports and typed-maps.
class ApiClient {
  ApiClient({
    http.Client? httpClient,
    TokenStore? tokenStore,
    String? baseUrl,
    this.onSessionExpired,
  }) : _http = httpClient ?? http.Client(),
       _tokens = tokenStore ?? SecureTokenStore(),
       _baseUrl = _normalizeBase(baseUrl ?? Env.apiBaseUrl);

  final http.Client _http;
  final TokenStore _tokens;
  final String _baseUrl;

  /// Called when a refresh attempt fails and the session is gone.
  final void Function()? onSessionExpired;

  String? _accessToken;
  Completer<bool>? _refreshCompleter;

  TokenStore get tokenStore => _tokens;

  static String _normalizeBase(String base) {
    var b = base.trim();
    if (b.endsWith('/')) b = b.substring(0, b.length - 1);
    return b;
  }

  // ------------------------------------------------------------------ verbs

  Future<dynamic> get(
    String path, {
    Map<String, dynamic>? query,
    Map<String, String>? headers,
  }) => request('GET', path, query: query, headers: headers);

  Future<dynamic> post(
    String path, {
    Object? body,
    Map<String, dynamic>? query,
    Map<String, String>? headers,
    bool authenticated = true,
  }) => request(
    'POST',
    path,
    body: body,
    query: query,
    headers: headers,
    authenticated: authenticated,
  );

  Future<dynamic> patch(
    String path, {
    Object? body,
    Map<String, dynamic>? query,
    Map<String, String>? headers,
  }) => request('PATCH', path, body: body, query: query, headers: headers);

  Future<dynamic> delete(
    String path, {
    Map<String, dynamic>? query,
    Map<String, String>? headers,
  }) => request('DELETE', path, query: query, headers: headers);

  /// Core request. Throws [ApiException] on any non-2xx or transport failure.
  Future<dynamic> request(
    String method,
    String path, {
    Object? body,
    Map<String, dynamic>? query,
    Map<String, String>? headers,
    bool authenticated = true,
  }) async {
    var httpResp = await _send(
      method,
      path,
      body: body,
      query: query,
      headers: headers,
      authenticated: authenticated,
    );

    if (httpResp.statusCode == 401 && authenticated) {
      // One refresh attempt per request, deduplicated across concurrent calls.
      final refreshed = await _refreshSingleFlight();
      if (refreshed) {
        httpResp = await _send(
          method,
          path,
          body: body,
          query: query,
          headers: headers,
          authenticated: authenticated,
        );
      } else {
        _handleSessionGone();
      }
    }

    return _decodeOrThrow(httpResp);
  }

  // ------------------------------------------------------------------ send

  Future<http.Response> _send(
    String method,
    String path, {
    Object? body,
    Map<String, dynamic>? query,
    Map<String, String>? headers,
    bool authenticated = true,
  }) async {
    final uri = _uri(path, query);
    final reqHeaders = <String, String>{
      'Accept': 'application/json',
      if (body != null) 'Content-Type': 'application/json',
      ...?headers,
    };
    if (authenticated) {
      final token = _accessToken ??= await _tokens.readAccessToken();
      if (token != null && token.isNotEmpty) {
        reqHeaders['Authorization'] = 'Bearer $token';
      }
    }
    try {
      final request = http.Request(method, uri);
      request.headers.addAll(reqHeaders);
      if (body != null) request.body = jsonEncode(_stripNulls(body));
      final streamed = await _http.send(request).timeout(Env.apiTimeout);
      return await http.Response.fromStream(streamed).timeout(Env.apiTimeout);
    } on ApiException {
      rethrow;
    } on TimeoutException {
      throw ApiException(
        statusCode: 0,
        code: 'network_timeout',
        message:
            'The server did not respond in time. '
            'Check connectivity and that the API base URL is correct.',
      );
    } catch (e) {
      throw ApiException(
        statusCode: 0,
        code: 'network_error',
        message: 'Network error: $e',
      );
    }
  }

  Uri _uri(String path, Map<String, dynamic>? query) {
    final full = path.startsWith('http')
        ? Uri.parse(path)
        : Uri.parse('$_baseUrl${path.startsWith('/') ? path : '/$path'}');
    if (query == null || query.isEmpty) return full;
    final qp = <String, String>{};
    query.forEach((k, v) {
      if (v != null) qp[k] = '$v';
    });
    return full.replace(queryParameters: {...full.queryParameters, ...qp});
  }

  static Object _stripNulls(Object body) {
    if (body is Map<String, dynamic>) {
      return {
        for (final e in body.entries)
          if (e.value != null) e.key: e.value,
      };
    }
    return body;
  }

  // ---------------------------------------------------------------- decode

  dynamic _decodeOrThrow(http.Response resp) {
    if (resp.statusCode == 204) return null;

    dynamic json;
    if (resp.body.isNotEmpty) {
      try {
        json = jsonDecode(utf8.decode(resp.bodyBytes));
      } catch (_) {
        json = null;
      }
    }

    if (resp.statusCode >= 200 && resp.statusCode < 300) {
      return json;
    }

    // Error envelope per §2.
    if (json is Map && json['error'] is Map) {
      final e = json['error'] as Map;
      throw ApiException(
        statusCode: resp.statusCode,
        code: '${e['code'] ?? 'internal_error'}',
        message: '${e['message'] ?? 'Unexpected server error'}',
        details: e['details'] is Map<String, dynamic>
            ? e['details'] as Map<String, dynamic>
            : (e['details'] is Map
                  ? Map<String, dynamic>.from(e['details'] as Map)
                  : null),
        requestId: e['request_id']?.toString(),
      );
    }
    // Non-envelope failure (proxy errors, FastAPI default detail, etc).
    var message = 'Request failed (${resp.statusCode})';
    var code = 'internal_error';
    if (json is Map && json['detail'] != null) {
      final d = json['detail'];
      message = d is String ? d : 'Validation failed';
      if (d is List && d.isNotEmpty) code = 'validation_error';
    }
    throw ApiException(
      statusCode: resp.statusCode,
      code: code,
      message: message,
    );
  }

  // --------------------------------------------------------------- refresh

  /// Single-flight refresh: concurrent 401s queue onto ONE /auth/refresh call.
  Future<bool> _refreshSingleFlight() {
    final existing = _refreshCompleter;
    if (existing != null) return existing.future;
    final completer = Completer<bool>();
    _refreshCompleter = completer;
    _performRefresh().then((ok) {
      _refreshCompleter = null;
      completer.complete(ok);
    });
    return completer.future;
  }

  Future<bool> _performRefresh() async {
    final refresh = await _tokens.readRefreshToken();
    if (refresh == null || refresh.isEmpty) return false;
    try {
      final resp = await _send(
        'POST',
        '/auth/refresh',
        body: {'refresh_token': refresh},
        authenticated: false,
      );
      if (resp.statusCode != 200 && resp.statusCode != 201) return false;
      final json = jsonDecode(utf8.decode(resp.bodyBytes));
      if (json is! Map) return false;
      final access = json['access_token']?.toString();
      final rotated = json['refresh_token']?.toString();
      if (access == null) return false;
      // Rotation: old refresh invalidated server-side; store the new pair.
      await _tokens.save(accessToken: access, refreshToken: rotated);
      _accessToken = access;
      return true;
    } catch (_) {
      return false;
    }
  }

  void _handleSessionGone() {
    _accessToken = null;
    unawaited(_tokens.clear());
    onSessionExpired?.call();
  }

  // -------------------------------------------------------------- sessions

  /// Login: POST /auth/login -> {access_token, refresh_token, user?}.
  Future<Map<String, dynamic>> login(String email, String password) async {
    final json = await post(
      '/auth/login',
      body: {'email': email, 'password': password},
      authenticated: false,
    );
    return _adoptSession(json);
  }

  Future<Map<String, dynamic>> registerCustomer({
    required String email,
    required String password,
    required String fullName,
    String? phone,
    String preferredLocale = 'en',
  }) async {
    final json = await post(
      '/auth/register',
      body: {
        'email': email,
        'password': password,
        'full_name': fullName,
        'phone': phone,
        'preferred_locale': preferredLocale,
      },
      authenticated: false,
    );
    return _adoptSession(json);
  }

  /// Provider registration helper: after the account exists, create the org
  /// (POST /organizations, CONTRACTS §8).
  Future<dynamic> createOrganization(Map<String, dynamic> payload) =>
      post('/organizations', body: payload);

  Future<void> logout() async {
    final refresh = await _tokens.readRefreshToken();
    try {
      await post('/auth/logout', body: {'refresh_token': refresh});
    } on ApiException {
      // Server-side revocation already happened or token invalid — clear anyway.
    } finally {
      await clearSession();
    }
  }

  Future<void> clearSession() async {
    _accessToken = null;
    await _tokens.clear();
  }

  Map<String, dynamic> _adoptSession(dynamic json) {
    if (json is! Map) {
      throw ApiException(
        statusCode: 500,
        code: 'internal_error',
        message: 'Malformed auth response from server.',
      );
    }
    final map = Map<String, dynamic>.from(json);
    final access = map['access_token']?.toString();
    final refresh = map['refresh_token']?.toString();
    if (access != null) {
      _accessToken = access;
      _tokens.save(accessToken: access, refreshToken: refresh);
    }
    return map;
  }
}
