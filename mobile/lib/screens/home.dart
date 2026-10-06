import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/formatters.dart';
import '../core/l10n.dart';
import '../data/repo.dart';
import '../models/dashboard.dart';
import '../models/user.dart';
import '../state/session.dart';
import '../widgets/common.dart';
import 'booking_detail.dart';
import 'demand_form.dart';
import 'discover.dart';

/// The buyer's landing: start a search, browse by category, or pick up the
/// booking that is already running. Every number here is the server's
/// dashboard answer (`GET /dashboard/customer`, §8) — nothing is recomputed.
class HomeScreen extends StatelessWidget {
  const HomeScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context);
    final fullName = context.watch<Session>().user?.fullName ?? '';
    final greeting = fullName.trim().split(RegExp(r'\s+')).first;
    return Scaffold(
      appBar: AppBar(
        title: Text(greeting.isEmpty ? l.t('app_name') : greeting),
      ),
      body: ListView(
        padding: const EdgeInsets.only(bottom: 96),
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 8, 12, 0),
            child: SearchBar(
              hintText: l.t('search_offers'),
              leading: const Icon(Icons.search),
              onTap: () => Navigator.of(context).push(
                MaterialPageRoute<void>(builder: (_) => const DiscoverScreen()),
              ),
            ),
          ),
          const _CategoryStrip(),
          const _NextBookingCard(),
          SectionCard(
            title: l.t('my_demands'),
            child: Column(
              children: [
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  leading: const Icon(Icons.campaign_outlined),
                  title: Text(l.t('post_demand')),
                  trailing: const Icon(Icons.chevron_right),
                  onTap: () => Navigator.of(context).push(
                    MaterialPageRoute<void>(
                      builder: (_) => const DemandFormScreen(),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// Catalog categories, straight from `GET /catalog/categories`, each one
/// opening a filtered search rather than a client-side slice of one.
class _CategoryStrip extends StatelessWidget {
  const _CategoryStrip();

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context);
    return AsyncView<List<Category>>(
      loader: context.read<Repo>().categories,
      onEmpty: (context, list) => list.isEmpty
          ? Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
              child: Text(
                l.t('no_offers'),
                style: Theme.of(context).textTheme.bodySmall,
              ),
            )
          : null,
      builder: (context, list) => SizedBox(
        height: 44,
        child: ListView(
          scrollDirection: Axis.horizontal,
          padding: const EdgeInsets.symmetric(horizontal: 12),
          children: [
            for (final c in list)
              Padding(
                padding: const EdgeInsets.only(right: 8),
                child: ActionChip(
                  label: Text(c.label),
                  onPressed: () => Navigator.of(context).push(
                    MaterialPageRoute<void>(
                      builder: (_) => DiscoverScreen(initialCategoryId: c.id),
                    ),
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

/// The booking that needs attention next, with its state and window, so the
/// countdown or the pending payment is one tap away.
class _NextBookingCard extends StatelessWidget {
  const _NextBookingCard();

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context);
    return SectionCard(
      title: l.t('upcoming'),
      child: AsyncView<CustomerDashboard>(
        loader: context.read<Repo>().customerDashboard,
        onEmpty: (context, d) => d.recentBookings.isEmpty
            ? Padding(
                padding: const EdgeInsets.symmetric(vertical: 8),
                child: Text(
                  l.t('no_bookings'),
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              )
            : null,
        builder: (context, d) => Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            InfoRow(
              label: l.t('active_bookings'),
              value: '${d.activeBookings}',
            ),
            if (d.unreadNotifications > 0)
              InfoRow(label: l.t('unread'), value: '${d.unreadNotifications}'),
            for (final b in d.recentBookings.take(2))
              ListTile(
                contentPadding: EdgeInsets.zero,
                dense: true,
                leading: const Icon(Icons.event_available_outlined),
                title: Text(
                  b.offerTitle.isEmpty ? l.t('booking') : b.offerTitle,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
                subtitle: Text(
                  [
                    if (b.windowStart != null) Format.dateTime(b.windowStart!),
                    Format.titleCase(b.status),
                    Format.money(b.totalCents, b.currency),
                  ].join(' · '),
                  style: Theme.of(context).textTheme.bodySmall,
                ),
                trailing: const Icon(Icons.chevron_right),
                onTap: () => Navigator.of(context).push(
                  MaterialPageRoute<void>(
                    builder: (_) => BookingDetailScreen(bookingId: b.id),
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}
