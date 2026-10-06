import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/formatters.dart';
import '../core/l10n.dart';
import '../data/repo.dart';
import '../models/activity.dart';
import '../models/dashboard.dart';
import '../models/envelope.dart';
import '../state/session.dart';
import '../widgets/common.dart';
import 'booking_detail.dart';
import 'messages.dart';

/// The account tab: who is signed in, the buyer-side numbers, and the two
/// inboxes that need no context to read — notifications and language.
class AccountScreen extends StatelessWidget {
  const AccountScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context);
    final session = context.watch<Session>();
    final user = session.user;
    return Scaffold(
      appBar: AppBar(title: Text(l.t('tab_account'))),
      body: ListView(
        padding: const EdgeInsets.only(bottom: 96),
        children: [
          if (user != null)
            SectionCard(
              title: user.fullName,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  InfoRow(label: l.t('email'), value: user.email),
                  if ((user.phone ?? '').isNotEmpty)
                    InfoRow(label: l.t('phone'), value: user.phone!),
                  InfoRow(
                    label: l.t('role'),
                    value: user.roles.map(Format.titleCase).join(', '),
                  ),
                  if (user.organization != null)
                    InfoRow(
                      label: l.t('organization'),
                      value: user.organization!.name,
                    ),
                ],
              ),
            ),
          if (session.isProvider)
            const _ProviderSummary()
          else
            const _CustomerSummary(),
          const NotificationsCard(),
          SectionCard(
            title: l.t('settings'),
            child: Column(
              children: [
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  leading: const Icon(Icons.translate),
                  title: Text(l.t('language')),
                  subtitle: Text(
                    l.locale.languageCode == 'fa'
                        ? 'فارسی (RTL)'
                        : 'English (LTR)',
                  ),
                  trailing: const Icon(Icons.chevron_right),
                  onTap: () => context.read<LocaleController>().toggle(),
                ),
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  leading: const Icon(Icons.logout),
                  title: Text(l.t('logout')),
                  onTap: () => context.read<Session>().logout(),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _CustomerSummary extends StatelessWidget {
  const _CustomerSummary();

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context);
    final repo = context.read<Repo>();
    return SectionCard(
      title: l.t('dashboard'),
      child: AsyncView<CustomerDashboard>(
        loader: repo.customerDashboard,
        builder: (context, d) => Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            InfoRow(
              label: l.t('active_bookings'),
              value: '${d.activeBookings}',
            ),
            InfoRow(
              label: l.t('spend'),
              value: Format.money(d.spendCents, d.currency),
            ),
            InfoRow(label: l.t('unread'), value: '${d.unreadNotifications}'),
            if (d.recentBookings.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text(
                l.t('upcoming'),
                style: Theme.of(context).textTheme.bodySmall,
              ),
              for (final b in d.recentBookings.take(3))
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  dense: true,
                  title: Text(
                    b.offerTitle,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                  subtitle: Text(
                    b.windowStart == null
                        ? Format.titleCase(b.status)
                        : '${Format.dateTime(b.windowStart!)} · ${Format.titleCase(b.status)}',
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                  onTap: () => Navigator.of(context).push(
                    MaterialPageRoute<void>(
                      builder: (_) => BookingDetailScreen(bookingId: b.id),
                    ),
                  ),
                ),
            ],
          ],
        ),
      ),
    );
  }
}

class _ProviderSummary extends StatelessWidget {
  const _ProviderSummary();

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context);
    final repo = context.read<Repo>();
    return SectionCard(
      title: l.t('dashboard'),
      child: AsyncView<ProviderDashboard>(
        loader: repo.providerDashboard,
        builder: (context, d) {
          final pct = d.isPercent
              ? d.utilization
              : (d.utilization * 100).clamp(0, 100);
          return Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              InfoRow(
                label: l.t('utilization'),
                value: '${pct.toStringAsFixed(0)}%',
              ),
              InfoRow(
                label: l.t('revenue'),
                value: Format.money(d.revenueCents, d.currency),
              ),
              InfoRow(
                label: l.t('upcoming'),
                value: '${d.upcomingBookings.length}',
              ),
            ],
          );
        },
      ),
    );
  }
}

/// The bell: what happened to this account's bookings, newest first, with the
/// unread badge cleared on the way past.
class NotificationsCard extends StatefulWidget {
  const NotificationsCard({super.key});

  @override
  State<NotificationsCard> createState() => _NotificationsCardState();
}

class _NotificationsCardState extends State<NotificationsCard> {
  int _epoch = 0;

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context);
    final repo = context.read<Repo>();
    return SectionCard(
      title: l.t('notifications'),
      trailing: TextButton(
        onPressed: () async {
          await repo.markAllNotificationsRead();
          if (mounted) setState(() => _epoch++);
        },
        child: Text(l.t('mark_all_read')),
      ),
      child: AsyncView<ListEnvelope<NotificationItem>>(
        key: ValueKey(_epoch),
        loader: () => repo.notifications(limit: 20),
        onEmpty: (context, page) => page.items.isEmpty
            ? Padding(
                padding: const EdgeInsets.symmetric(vertical: 8),
                child: Text(
                  l.t('no_notifications'),
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              )
            : null,
        builder: (context, page) => Column(
          children: [
            for (final n in page.items)
              ListTile(
                contentPadding: EdgeInsets.zero,
                dense: true,
                leading: Icon(
                  n.isRead ? Icons.check_circle_outline : Icons.circle,
                  size: n.isRead ? 20 : 10,
                  color: n.isRead
                      ? Theme.of(context).colorScheme.outline
                      : Theme.of(context).colorScheme.primary,
                ),
                title: Text(
                  n.title,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                ),
                subtitle: Text(
                  [
                    n.body,
                    if (n.createdAt != null) Format.relative(n.createdAt!),
                  ].where((s) => s.isNotEmpty).join(' · '),
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: Theme.of(context).textTheme.bodySmall,
                ),
                onTap: () => _open(n),
              ),
          ],
        ),
      ),
    );
  }

  /// A notification is a pointer, not a page: mark it read, then go wherever
  /// its `data` points — the booking, the thread, or nowhere.
  Future<void> _open(NotificationItem n) async {
    final context = this.context;
    await context.read<Repo>().markNotificationRead(n.id);
    if (!context.mounted) return;
    setState(() => _epoch++);
    final bookingId = n.bookingId;
    if (bookingId != null) {
      Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => BookingDetailScreen(bookingId: bookingId),
        ),
      );
      return;
    }
    final conversationId = n.conversationId;
    if (conversationId != null) {
      Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => ConversationScreen(conversationId: conversationId),
        ),
      );
    }
  }
}
