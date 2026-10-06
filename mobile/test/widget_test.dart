import 'dart:convert';

import 'package:capacity_exchange/api/api_client.dart';
import 'package:capacity_exchange/api/token_store.dart';
import 'package:capacity_exchange/core/l10n.dart';
import 'package:capacity_exchange/data/repo.dart';
import 'package:capacity_exchange/main.dart';
import 'package:capacity_exchange/state/session.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

/// A transport double: it answers the documented §2 shapes over the real
/// [ApiClient], so the widget tree, the bearer header, the JSON parsing and the
/// session gate are all exercised — only the network is replaced.
class FakeApi extends http.BaseClient {
  final List<String> calls = [];

  /// When set, `/auth/login` answers with the §2 error envelope.
  bool rejectLogin = false;

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    final req = request as http.Request;
    final path = req.url.path.replaceFirst('/api/v1', '');
    calls.add('${req.method} $path');
    if (path == '/auth/login' && rejectLogin) {
      return http.StreamedResponse(
        Stream.value(
          utf8.encode(
            jsonEncode({
              'error': {
                'code': 'invalid_credentials',
                'message': 'Invalid email or password',
              },
            }),
          ),
        ),
        401,
        headers: {'content-type': 'application/json'},
      );
    }
    return _json(_answer(req, path));
  }

  Map<String, dynamic> _answer(http.Request req, String path) {
    if (path == '/auth/login') {
      final body = req.body.isEmpty
          ? <String, dynamic>{}
          : Map<String, dynamic>.from(jsonDecode(req.body) as Map);
      final email = body['email'] ?? '';
      return {
        'access_token': 'access-token',
        'refresh_token': 'refresh-token',
        'user': _user('$email'.startsWith('provider')),
      };
    }
    if (path == '/auth/me') return _user(false);
    return {'items': <Object>[], 'total': 0, 'limit': 20, 'offset': 0};
  }

  static Map<String, dynamic> _user(bool asProvider) => {
    'id': asProvider ? 'prov1' : 'cust1',
    'email': asProvider ? 'provider@example.com' : 'customer@example.com',
    'full_name': asProvider ? 'Priya Provider' : 'Cleo Customer',
    'roles': asProvider ? ['provider', 'org_admin'] : ['customer'],
    'active_org_id': asProvider ? 'org1' : null,
  };

  static http.StreamedResponse _json(Map<String, dynamic> body) =>
      http.StreamedResponse(
        Stream.value(utf8.encode(jsonEncode(body))),
        200,
        headers: {'content-type': 'application/json'},
      );
}

Future<void> _boot(WidgetTester tester, FakeApi transport) async {
  final api = ApiClient(
    httpClient: transport,
    tokenStore: InMemoryTokenStore(),
  );
  final session = Session(api);
  await session.restore();
  await tester.pumpWidget(
    CapacityExchangeApp(
      session: session,
      repo: Repo(api),
      locale: LocaleController(),
    ),
  );
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('an anonymous start lands on sign in', (tester) async {
    await _boot(tester, FakeApi());
    expect(find.text('Log in'), findsWidgets);
    expect(find.text('New here? Create an account'), findsOneWidget);
  });

  testWidgets('signing in goes through the API and opens the tabs', (
    tester,
  ) async {
    final api = FakeApi();
    await _boot(tester, api);

    await tester.enterText(
      find.widgetWithText(TextFormField, 'Email'),
      'customer@example.com',
    );
    await tester.enterText(
      find.widgetWithText(TextFormField, 'Password'),
      'sup3rsecret',
    );
    await tester.tap(find.widgetWithText(FilledButton, 'Log in'));
    await tester.pumpAndSettle();

    expect(api.calls, contains('POST /auth/login'));
    expect(find.text('Discover'), findsOneWidget);
    expect(find.text('Activity'), findsOneWidget);
    expect(find.text('Account'), findsOneWidget);
  });

  testWidgets('a provider gets the workspace as the first tab', (tester) async {
    final api = FakeApi();
    await _boot(tester, api);

    await tester.enterText(
      find.widgetWithText(TextFormField, 'Email'),
      'provider@example.com',
    );
    await tester.enterText(
      find.widgetWithText(TextFormField, 'Password'),
      'sup3rsecret',
    );
    await tester.tap(find.widgetWithText(FilledButton, 'Log in'));
    await tester.pumpAndSettle();

    // Named twice: the tab it sits on and the screen's own title bar.
    expect(find.text('Provider workspace'), findsWidgets);
    expect(api.calls, contains('GET /dashboard/provider'));
  });

  testWidgets('the language toggle switches the catalog and the direction', (
    tester,
  ) async {
    await _boot(tester, FakeApi());

    await tester.tap(find.byIcon(Icons.translate));
    await tester.pumpAndSettle();

    expect(find.text('ورود'), findsWidgets);
    expect(
      Directionality.of(tester.element(find.byType(Scaffold).first)),
      TextDirection.rtl,
    );
  });

  testWidgets('a rejected sign in quotes the server', (tester) async {
    final api = FakeApi()..rejectLogin = true;
    await _boot(tester, api);

    await tester.enterText(
      find.widgetWithText(TextFormField, 'Email'),
      'customer@example.com',
    );
    await tester.enterText(
      find.widgetWithText(TextFormField, 'Password'),
      'wrong-password',
    );
    await tester.tap(find.widgetWithText(FilledButton, 'Log in'));
    await tester.pumpAndSettle();

    expect(find.text('Invalid email or password'), findsOneWidget);
    expect(find.text('Discover'), findsNothing);
  });
}
