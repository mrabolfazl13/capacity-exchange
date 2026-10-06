import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/formatters.dart';
import '../core/l10n.dart';
import '../data/repo.dart';
import '../models/booking.dart';
import '../models/dashboard.dart';
import '../models/demand.dart';
import '../models/envelope.dart';
import '../models/offer.dart';
import '../widgets/common.dart';
import 'booking_detail.dart';

/// The provider's day, sized for a phone: what needs an answer right now, then
/// the numbers that say whether the week is full.
///
/// Quick actions post the same transitions the desktop board does — the mobile
/// client is a thinner view, not a different state machine.
class ProviderScreen extends StatefulWidget {
  const ProviderScreen({super.key});

  @override
  State<ProviderScreen> createState() => _ProviderScreenState();
}

class _ProviderScreenState extends State<ProviderScreen> {
  int _epoch = 0;
  bool _busy = false;

  void _refresh() => setState(() => _epoch++);

  /// Every quick action is one request plus a reload: the row on screen after
  /// the tap is the row the server wrote, never a locally guessed one.
  Future<void> _verb(Future<dynamic> Function(Repo) verb) async {
    setState(() => _busy = true);
    try {
      await verb(context.read<Repo>());
      _refresh();
    } catch (e) {
      if (mounted) showError(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context);
    final repo = context.read<Repo>();
    return Scaffold(
      appBar: AppBar(
        title: Text(l.t('provider_workspace')),
        actions: [
          IconButton(onPressed: _refresh, icon: const Icon(Icons.refresh)),
        ],
      ),
      body: ListView(
        key: ValueKey(_epoch),
        padding: const EdgeInsets.only(bottom: 32),
        children: [
          SectionCard(
            title: l.t('needs_action'),
            child: AsyncView<List<Booking>>(
              loader: () async =>
                  (await repo.bookings(mine: false, provider: true, limit: 30))
                      .items
                      .where(
                        (b) =>
                            b.status == BookingStatuses.hold ||
                            b.status == BookingStatuses.draft ||
                            b.status == BookingStatuses.confirmed ||
                            b.status == BookingStatuses.inProgress,
                      )
                      .toList(growable: false),
              onEmpty: (context, items) => items.isEmpty
                  ? Padding(
                      padding: const EdgeInsets.symmetric(vertical: 8),
                      child: Text(
                        l.t('nothing_pending'),
                        style: Theme.of(context).textTheme.bodySmall,
                      ),
                    )
                  : null,
              builder: (context, items) =>
                  Column(children: [for (final b in items) _pendingTile(b)]),
            ),
          ),
          SectionCard(
            title: l.t('match_inbox'),
            child: AsyncView<ListEnvelope<Match>>(
              loader: () => repo.providerMatches(status: 'suggested'),
              onEmpty: (context, page) => page.items.isEmpty
                  ? Padding(
                      padding: const EdgeInsets.symmetric(vertical: 8),
                      child: Text(
                        l.t('no_open_demands'),
                        style: Theme.of(context).textTheme.bodySmall,
                      ),
                    )
                  : null,
              builder: (context, page) =>
                  Column(children: [for (final m in page.items) _matchTile(m)]),
            ),
          ),
          _kpiCard(),
          SectionCard(
            title: l.t('my_offers'),
            child: AsyncView<ListEnvelope<Offer>>(
              loader: () => repo.providerOffers(),
              onEmpty: (context, page) => page.items.isEmpty
                  ? Padding(
                      padding: const EdgeInsets.symmetric(vertical: 8),
                      child: Text(
                        l.t('no_offers'),
                        style: Theme.of(context).textTheme.bodySmall,
                      ),
                    )
                  : null,
              builder: (context, page) =>
                  Column(children: [for (final o in page.items) _offerTile(o)]),
            ),
          ),
        ],
      ),
    );
  }

  Widget _pendingTile(Booking b) {
    final l = AppLocalizations.of(context);
    final theme = Theme.of(context);
    return ListTile(
      contentPadding: const EdgeInsets.symmetric(vertical: 4),
      title: Text(b.offerTitle, maxLines: 1, overflow: TextOverflow.ellipsis),
      subtitle: Text(
        [
          if (b.windowStart != null) Format.dateTime(b.windowStart!),
          Format.titleCase(b.status),
        ].join(' · '),
        style: theme.textTheme.bodySmall,
      ),
      trailing: Wrap(
        spacing: 4,
        children: [
          if (b.status == BookingStatuses.hold ||
              b.status == BookingStatuses.draft)
            _mini(
              label: l.t('accept'),
              icon: Icons.check,
              onPressed: _busy
                  ? null
                  : () => _verb((r) => r.confirmBooking(b.id)),
            ),
          if (b.status == BookingStatuses.confirmed)
            _mini(
              label: l.t('start'),
              icon: Icons.play_arrow,
              onPressed: _busy
                  ? null
                  : () => _verb((r) => r.startBooking(b.id)),
            ),
          if (b.status == BookingStatuses.inProgress)
            _mini(
              label: l.t('complete'),
              icon: Icons.task_alt,
              onPressed: _busy
                  ? null
                  : () => _verb((r) => r.completeBooking(b.id)),
            ),
          IconButton(
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute<void>(
                builder: (_) => BookingDetailScreen(bookingId: b.id),
              ),
            ),
            icon: const Icon(Icons.open_in_new, size: 18),
          ),
        ],
      ),
    );
  }

  Widget _mini({
    required String label,
    required IconData icon,
    VoidCallback? onPressed,
  }) => FilledButton.tonalIcon(
    onPressed: onPressed,
    icon: Icon(icon, size: 16),
    label: Text(label),
    style: FilledButton.styleFrom(
      visualDensity: VisualDensity.compact,
      padding: const EdgeInsets.symmetric(horizontal: 10),
    ),
  );

  Widget _matchTile(Match m) {
    final l = AppLocalizations.of(context);
    final theme = Theme.of(context);
    return ListTile(
      contentPadding: const EdgeInsets.symmetric(vertical: 4),
      title: Text(m.offerTitle, maxLines: 1, overflow: TextOverflow.ellipsis),
      subtitle: Text(
        [
          '${m.score.round()}% ${l.t('match_score')}',
          if (m.reasons.isNotEmpty) m.reasons.take(2).join(', '),
        ].join(' · '),
        style: theme.textTheme.bodySmall,
      ),
      trailing: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          IconButton(
            tooltip: l.t('decline'),
            onPressed: _busy ? null : () => _verb((r) => r.declineMatch(m.id)),
            icon: const Icon(Icons.close),
          ),
          IconButton(
            tooltip: l.t('accept'),
            onPressed: _busy ? null : () => _verb((r) => r.acceptMatch(m.id)),
            icon: const Icon(Icons.check_circle_outline),
          ),
        ],
      ),
    );
  }

  Widget _kpiCard() {
    final l = AppLocalizations.of(context);
    final repo = context.read<Repo>();
    return SectionCard(
      title: l.t('dashboard'),
      child: AsyncView<ProviderDashboard>(
        loader: () => repo.providerDashboard(),
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
                label: l.t('active_bookings'),
                value: '${d.bookingsByStatus['confirmed'] ?? 0}',
              ),
              LinearProgressIndicator(
                value: pct / 100,
                minHeight: 6,
                borderRadius: BorderRadius.circular(3),
              ),
            ],
          );
        },
      ),
    );
  }

  Widget _offerTile(Offer o) {
    final l = AppLocalizations.of(context);
    final repo = context.read<Repo>();
    final isPublished = o.status == 'published';
    return ListTile(
      contentPadding: const EdgeInsets.symmetric(vertical: 2),
      title: Text(o.title, maxLines: 1, overflow: TextOverflow.ellipsis),
      subtitle: Text(
        '${Format.money(o.unitAmountCents, o.currency)} · ${o.pricingLabel}',
        style: Theme.of(context).textTheme.bodySmall,
      ),
      trailing: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          StatusChip(status: o.status),
          IconButton(
            tooltip: isPublished ? l.t('pause_offer') : l.t('publish_offer'),
            onPressed: () async {
              try {
                if (isPublished) {
                  await repo.pauseOffer(o.id);
                } else {
                  await repo.publishOffer(o.id);
                }
                _refresh();
              } catch (e) {
                if (mounted) showError(context, e);
              }
            },
            icon: Icon(
              isPublished
                  ? Icons.pause_circle_outline
                  : Icons.play_circle_outline,
            ),
          ),
        ],
      ),
    );
  }
}
