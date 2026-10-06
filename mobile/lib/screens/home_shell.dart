import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/l10n.dart';
import '../state/session.dart';
import 'account.dart';
import 'activity.dart';
import 'discover.dart';
import 'home.dart';
import 'messages.dart';
import 'provider.dart';

/// The app's frame: five tabs, kept alive so a search or a booking list does
/// not reload every time the user looks at messages and back.
///
/// Providers get the workspace as the first tab instead of the buyer landing —
/// same shell, different job to do first.
class HomeShell extends StatefulWidget {
  const HomeShell({super.key});

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  int _index = 0;

  static const _pages = <Widget>[
    HomeScreen(),
    DiscoverScreen(),
    ActivityScreen(),
    MessagesScreen(),
    AccountScreen(),
  ];

  static const _providerPages = <Widget>[
    ProviderScreen(),
    DiscoverScreen(),
    ActivityScreen(),
    MessagesScreen(),
    AccountScreen(),
  ];

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context);
    final isProvider = context.watch<Session>().isProvider;
    final pages = isProvider ? _providerPages : _pages;
    return Scaffold(
      body: IndexedStack(index: _index, children: pages),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _index,
        onDestinationSelected: (i) => setState(() => _index = i),
        destinations: [
          NavigationDestination(
            icon: Icon(isProvider ? Icons.storefront : Icons.home_outlined),
            selectedIcon: Icon(isProvider ? Icons.storefront : Icons.home),
            label: l.t(isProvider ? 'provider_workspace' : 'tab_home'),
          ),
          NavigationDestination(
            icon: const Icon(Icons.search_outlined),
            selectedIcon: const Icon(Icons.search),
            label: l.t('tab_discover'),
          ),
          NavigationDestination(
            icon: const Icon(Icons.receipt_long_outlined),
            selectedIcon: const Icon(Icons.receipt_long),
            label: l.t('tab_activity'),
          ),
          NavigationDestination(
            icon: const Icon(Icons.chat_bubble_outline),
            selectedIcon: const Icon(Icons.chat_bubble),
            label: l.t('tab_messages'),
          ),
          NavigationDestination(
            icon: const Icon(Icons.person_outline),
            selectedIcon: const Icon(Icons.person),
            label: l.t('tab_account'),
          ),
        ],
      ),
    );
  }
}
