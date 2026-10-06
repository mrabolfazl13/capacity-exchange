import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/formatters.dart';
import '../core/l10n.dart';
import '../data/repo.dart';
import '../models/booking.dart';
import '../models/demand.dart';
import '../models/order.dart';
import '../models/envelope.dart';
import '../state/session.dart';
import '../widgets/common.dart';
import 'booking_detail.dart';
import 'demand_form.dart';

/// Everything with a status: bookings, invoices, and the demands waiting for a
/// provider to answer. Scoped by role — `mine` for a customer, `provider` for
/// an organization — because the server answers those two differently.
class ActivityScreen extends StatefulWidget {
  const ActivityScreen({super.key});

  @override
  State<ActivityScreen> createState() => _ActivityScreenState();
}

class _ActivityScreenState extends State<ActivityScreen>
    with SingleTickerProviderStateMixin {
  late final TabController _tabs;

  @override
  void initState() {
    super.initState();
    _tabs = TabController(length: 3, vsync: this);
  }

  @override
  void dispose() {
    _tabs.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context);
    return Scaffold(
      appBar: AppBar(
        title: Text(l.t('tab_activity')),
        bottom: TabBar(
          controller: _tabs,
          tabs: [
            Tab(text: l.t('my_bookings')),
            Tab(text: l.t('my_orders')),
            Tab(text: l.t('my_demands')),
          ],
        ),
      ),
      body: TabBarView(
        controller: _tabs,
        children: const [BookingsListTab(), OrdersListTab(), DemandsListTab()],
      ),
      floatingActionButton: context.watch<Session>().isProvider
          ? null
          : FloatingActionButton.extended(
              heroTag: 'new_demand',
              onPressed: () => Navigator.of(context).push(
                MaterialPageRoute<void>(
                  builder: (_) => const DemandFormScreen(),
                ),
              ),
              icon: const Icon(Icons.campaign_outlined),
              label: Text(l.t('post_demand')),
            ),
    );
  }
}

class BookingsListTab extends StatefulWidget {
  const BookingsListTab({super.key});

  @override
  State<BookingsListTab> createState() => _BookingsListTabState();
}

class _BookingsListTabState extends State<BookingsListTab> {
  String? _status;
  int _epoch = 0;

  static const _filters = [
    null,
    BookingStatuses.hold,
    BookingStatuses.confirmed,
    BookingStatuses.inProgress,
    BookingStatuses.completed,
  ];

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context);
    final repo = context.read<Repo>();
    final provider = context.watch<Session>().isProvider;
    return Column(
      children: [
        SizedBox(
          height: 52,
          child: ListView(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
            children: [
              for (final s in _filters)
                Padding(
                  padding: const EdgeInsets.only(right: 8),
                  child: ChoiceChip(
                    label: Text(s == null ? l.t('all') : Format.titleCase(s)),
                    selected: _status == s,
                    onSelected: (_) => setState(() {
                      _status = s;
                      _epoch++;
                    }),
                  ),
                ),
            ],
          ),
        ),
        Expanded(
          child: AsyncView<ListEnvelope<Booking>>(
            key: ValueKey('$_epoch${provider ? 'p' : 'c'}$_status'),
            loader: () => repo.bookings(
              mine: !provider,
              provider: provider,
              status: _status,
              limit: 30,
            ),
            builder: (context, page) => ListView.builder(
              padding: const EdgeInsets.only(bottom: 24),
              itemCount: page.items.length,
              itemBuilder: (context, i) => BookingTile(booking: page.items[i]),
            ),
            onEmpty: (context, page) => page.items.isEmpty
                ? EmptyView(
                    message: l.t('no_bookings'),
                    icon: Icons.event_busy_outlined,
                  )
                : null,
          ),
        ),
      ],
    );
  }
}

class BookingTile extends StatelessWidget {
  const BookingTile({super.key, required this.booking});

  final Booking booking;

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context);
    final theme = Theme.of(context);
    final b = booking;
    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 5),
      child: ListTile(
        contentPadding: const EdgeInsets.fromLTRB(14, 10, 14, 12),
        title: Text(
          b.offerTitle.isEmpty ? l.t('booking') : b.offerTitle,
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
        ),
        subtitle: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (b.windowStart != null)
              Text(
                Format.dateTime(b.windowStart!),
                style: theme.textTheme.bodySmall,
              ),
            if (b.isHold) HoldCountdown(booking: b),
          ],
        ),
        trailing: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          crossAxisAlignment: CrossAxisAlignment.end,
          children: [
            StatusChip(status: b.status),
            const SizedBox(height: 4),
            MoneyText(b.totalCents, b.currency, compact: true),
          ],
        ),
        onTap: () => Navigator.of(context).push(
          MaterialPageRoute<void>(
            builder: (_) => BookingDetailScreen(bookingId: b.id),
          ),
        ),
      ),
    );
  }
}

class OrdersListTab extends StatelessWidget {
  const OrdersListTab({super.key});

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context);
    final repo = context.read<Repo>();
    final provider = context.watch<Session>().isProvider;
    return AsyncView<ListEnvelope<Order>>(
      loader: () => repo.orders(provider: provider, limit: 30),
      builder: (context, page) => ListView.builder(
        padding: const EdgeInsets.only(bottom: 24),
        itemCount: page.items.length,
        itemBuilder: (context, i) => _orderTile(context, page.items[i], l),
      ),
      onEmpty: (context, page) => page.items.isEmpty
          ? EmptyView(
              message: l.t('no_orders'),
              icon: Icons.receipt_long_outlined,
            )
          : null,
    );
  }

  Widget _orderTile(BuildContext context, Order o, AppLocalizations l) {
    final theme = Theme.of(context);
    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 5),
      child: ListTile(
        contentPadding: const EdgeInsets.fromLTRB(14, 10, 14, 12),
        title: Text(o.offerTitle.isEmpty ? o.number : o.offerTitle),
        subtitle: Text(
          [
            o.number,
            if (o.orgName.isNotEmpty) o.orgName,
            if (o.placedAt != null) Format.relative(o.placedAt!),
          ].join(' · '),
          style: theme.textTheme.bodySmall,
        ),
        trailing: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          crossAxisAlignment: CrossAxisAlignment.end,
          children: [
            StatusChip(status: o.paymentStatus),
            const SizedBox(height: 4),
            MoneyText(o.totalCents, o.currency, compact: true),
          ],
        ),
        onTap: o.bookingId == null
            ? null
            : () => Navigator.of(context).push(
                MaterialPageRoute<void>(
                  builder: (_) => BookingDetailScreen(bookingId: o.bookingId!),
                ),
              ),
      ),
    );
  }
}

class DemandsListTab extends StatelessWidget {
  const DemandsListTab({super.key});

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context);
    final repo = context.read<Repo>();
    final provider = context.watch<Session>().isProvider;
    return AsyncView<ListEnvelope<Demand>>(
      loader: () =>
          provider ? repo.inboundDemands(limit: 30) : repo.myDemands(limit: 30),
      builder: (context, page) => ListView.builder(
        padding: const EdgeInsets.only(bottom: 24),
        itemCount: page.items.length,
        itemBuilder: (context, i) => _demandTile(context, page.items[i], l),
      ),
      onEmpty: (context, page) => page.items.isEmpty
          ? EmptyView(message: l.t('no_demands'), icon: Icons.campaign_outlined)
          : null,
    );
  }

  Widget _demandTile(BuildContext context, Demand d, AppLocalizations l) {
    final theme = Theme.of(context);
    final budget = [
      if (d.budgetMinCents != null) Format.money(d.budgetMinCents!, d.currency),
      if (d.budgetMaxCents != null) Format.money(d.budgetMaxCents!, d.currency),
    ].join(' – ');
    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 5),
      child: ListTile(
        contentPadding: const EdgeInsets.fromLTRB(14, 10, 14, 12),
        title: Text(
          d.description,
          maxLines: 2,
          overflow: TextOverflow.ellipsis,
        ),
        subtitle: Text(
          [
            if (d.categoryLabel.isNotEmpty) d.categoryLabel,
            if ((d.city ?? '').isNotEmpty) d.city!,
            '${l.t('quantity')} ${d.quantity}',
            if (budget.isNotEmpty) budget,
          ].join(' · '),
          style: theme.textTheme.bodySmall,
        ),
        trailing: StatusChip(status: d.status),
        onTap: () => Navigator.of(context).push(
          MaterialPageRoute<void>(
            builder: (_) => DemandDetailScreen(demandId: d.id),
          ),
        ),
      ),
    );
  }
}
