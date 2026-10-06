import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:provider/provider.dart';

import 'api/api_client.dart';
import 'core/l10n.dart';
import 'core/theme.dart';
import 'data/repo.dart';
import 'screens/auth.dart';
import 'screens/home_shell.dart';
import 'state/session.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  final api = ApiClient();
  final session = Session(api);
  // Reads the stored token pair; the gate shows a splash until it answers.
  session.restore();
  runApp(
    CapacityExchangeApp(
      session: session,
      repo: Repo(api),
      locale: LocaleController(),
    ),
  );
}

class CapacityExchangeApp extends StatelessWidget {
  const CapacityExchangeApp({
    super.key,
    required this.session,
    required this.repo,
    required this.locale,
  });

  final Session session;
  final Repo repo;
  final LocaleController locale;

  @override
  Widget build(BuildContext context) {
    return MultiProvider(
      providers: [
        ChangeNotifierProvider.value(value: session),
        ChangeNotifierProvider.value(value: locale),
        Provider<Repo>.value(value: repo),
      ],
      child: const _Root(),
    );
  }
}

/// Rebuilds on locale or session changes: those two are the only things that
/// can swap the whole screen without a navigation push.
class _Root extends StatelessWidget {
  const _Root();

  @override
  Widget build(BuildContext context) {
    final locale = context.watch<LocaleController>().locale;
    return MaterialApp(
      title: 'Capacity Exchange',
      debugShowCheckedModeBanner: false,
      theme: AppTheme.light(),
      darkTheme: AppTheme.dark(),
      locale: locale,
      supportedLocales: AppLocalizations.supported,
      localizationsDelegates: const [
        AppLocalizationsDelegate(),
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      home: const _Gate(),
    );
  }
}

/// The only routing rule the app has: no identity yet → wait, no session →
/// sign in, otherwise the tabs.
class _Gate extends StatelessWidget {
  const _Gate();

  @override
  Widget build(BuildContext context) {
    final session = context.watch<Session>();
    if (session.restoring) return const _Splash();
    return session.signedIn ? const HomeShell() : const AuthScreen();
  }
}

class _Splash extends StatelessWidget {
  const _Splash();

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: const [
            Text('Capacity Exchange'),
            SizedBox(height: 16),
            CircularProgressIndicator(),
          ],
        ),
      ),
    );
  }
}
