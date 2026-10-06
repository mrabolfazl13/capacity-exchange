import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/formatters.dart';
import '../core/l10n.dart';
import '../core/theme.dart';
import '../data/actions.dart';
import '../data/repo.dart';
import '../models/booking.dart';
import '../models/order.dart';
import '../models/query.dart';
import '../state/session.dart';
import '../widgets/common.dart';
import 'messages.dart';

/// One booking in full: the row the server owns, its status history, and only
/// the verbs this viewer is allowed to use right now.
///
/// Every action re-reads the booking afterwards. A cancelled hold and an
/// expired one can look identical until the server says which, so the screen
/// never edits its own copy of the truth.
class BookingDetailScreen extends StatefulWidget {
  const BookingDetailScreen({super.key, required this.bookingId});

  final String bookingId;

  @override
  State<BookingDetailScreen> createState() => _BookingDetailScreenState();
}

class _BookingDetailScreenState extends State<BookingDetailScreen> {
  late Future<Booking> _future;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _future = _load();
  }

  Future<Booking> _load() => context.read<Repo>().booking(widget.bookingId);

  void _refresh() => setState(() => _future = _load());

  Future<void> _run(Future<void> Function() action) async {
    setState(() => _busy = true);
    try {
      await action();
    } catch (e) {
      if (mounted) showError(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _verb(Future<Booking> Function(Repo, String) verb) =>
      _run(() async => verb(context.read<Repo>(), widget.bookingId));

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context);
    return Scaffold(
      appBar: AppBar(
        title: Text(l.t('booking')),
        actions: [
          IconButton(onPressed: _refresh, icon: const Icon(Icons.refresh)),
        ],
      ),
      body: FutureBuilder<Booking>(
        future: _future,
        builder: (context, snapshot) {
          if (snapshot.hasError) {
            return ErrorView(error: snapshot.error!, onRetry: _refresh);
          }
          if (!snapshot.hasData) {
            return const Center(child: CircularProgressIndicator());
          }
          final booking = snapshot.data!;
          final repo = context.read<Repo>();
          final user = context.watch<Session>().user;
          final actions = allowedBookingActions(booking, user);
          return ListView(
            padding: const EdgeInsets.only(bottom: 32),
            children: [
              _summary(booking),
              if (actions.isNotEmpty) _actionsCard(booking, actions),
              SectionCard(
                title: l.t('timeline'),
                child: AsyncView<List<BookingEvent>>(
                  loader: () async =>
                      (await repo.bookingTimeline(widget.bookingId)).items,
                  builder: (context, events) => Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      for (final e in events)
                        ListTile(
                          dense: true,
                          contentPadding: EdgeInsets.zero,
                          leading: Icon(
                            Icons.circle,
                            size: 10,
                            color: StatusPalette.forStatus(e.toStatus),
                          ),
                          title: Text(Format.titleCase(e.toStatus)),
                          subtitle: Text(
                            [
                              if ((e.reason ?? '').isNotEmpty) e.reason!,
                              if (e.occurredAt != null)
                                Format.dateTime(e.occurredAt!),
                            ].join(' · '),
                          ),
                        ),
                    ],
                  ),
                ),
              ),
              _orderCard(booking),
            ],
          );
        },
      ),
    );
  }

  Widget _summary(Booking b) {
    final l = AppLocalizations.of(context);
    return SectionCard(
      title: b.offerTitle.isEmpty ? l.t('booking') : b.offerTitle,
      trailing: StatusChip(status: b.status),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (b.resourceName.isNotEmpty)
            InfoRow(label: l.t('resource'), value: b.resourceName),
          if (b.orgName.isNotEmpty)
            InfoRow(label: l.t('provider'), value: b.orgName),
          if (b.windowStart != null)
            InfoRow(
              label: l.t('window'),
              value:
                  '${Format.dateTime(b.windowStart!)}'
                  '${b.windowEnd == null ? '' : ' → ${Format.time(b.windowEnd!)}'}',
            ),
          InfoRow(
            label: l.t('quantity'),
            value: b.unitLabel.isEmpty
                ? '${b.quantity}'
                : '${b.quantity} ${b.unitLabel}',
          ),
          InfoRow(
            label: l.t('unit_price'),
            value: Format.money(b.unitAmountCents, b.currency),
          ),
          InfoRow(
            label: l.t('total'),
            value: Format.money(b.totalCents, b.currency),
          ),
          InfoRow(
            label: l.t('payment'),
            value: Format.titleCase(b.paymentStatus),
          ),
          if (b.isHold) HoldCountdown(booking: b, onExpired: _refresh),
          if ((b.cancelReason ?? '').isNotEmpty)
            InfoRow(label: l.t('cancel_reason'), value: b.cancelReason!),
        ],
      ),
    );
  }

  Widget _actionsCard(Booking booking, List<BookingAction> actions) {
    final l = AppLocalizations.of(context);
    return SectionCard(
      title: l.t('actions'),
      child: Wrap(
        spacing: 8,
        runSpacing: 8,
        children: [
          for (final action in actions)
            OutlinedButton.icon(
              onPressed: _busy ? null : () => _perform(action, booking),
              icon: Icon(_iconFor(action)),
              label: Text(l.t(_labelFor(action))),
            ),
        ],
      ),
    );
  }

  Future<void> _perform(BookingAction action, Booking booking) async {
    switch (action) {
      case BookingAction.confirm:
        return _verb((repo, id) => repo.confirmBooking(id));
      case BookingAction.start:
        return _verb((repo, id) => repo.startBooking(id));
      case BookingAction.complete:
        return _verb((repo, id) => repo.completeBooking(id));
      case BookingAction.cancel:
        return _cancelFlow(booking);
      case BookingAction.pay:
        return _payFlow(booking);
      case BookingAction.review:
        return _reviewFlow(booking);
      case BookingAction.dispute:
        return _disputeFlow(booking);
    }
  }

  static String _labelFor(BookingAction a) => switch (a) {
    BookingAction.pay => 'pay_now',
    BookingAction.confirm => 'confirm_booking',
    BookingAction.start => 'start',
    BookingAction.complete => 'complete',
    BookingAction.cancel => 'cancel_booking',
    BookingAction.review => 'write_review',
    BookingAction.dispute => 'raise_dispute',
  };

  static IconData _iconFor(BookingAction a) => switch (a) {
    BookingAction.pay => Icons.credit_card,
    BookingAction.confirm => Icons.check_circle_outline,
    BookingAction.start => Icons.play_circle_outline,
    BookingAction.complete => Icons.task_alt,
    BookingAction.cancel => Icons.cancel_outlined,
    BookingAction.review => Icons.star_border,
    BookingAction.dispute => Icons.gavel,
  };

  /// Cancellation is the one verb with a price, so the policy band that
  /// applies right now is shown before asking — the server still decides.
  Future<void> _cancelFlow(Booking booking) async {
    final l = AppLocalizations.of(context);
    final reasonCtrl = TextEditingController();
    final refund = refundPctForCancellation(
      booking.cancellationPolicy,
      booking.windowStart,
    );
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text(l.t('cancel_booking')),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              booking.cancellationPolicy.isEmpty
                  ? l.t('no_policy_on_booking')
                  : '${l.t('refund')}: $refund%',
            ),
            const SizedBox(height: 12),
            TextField(
              controller: reasonCtrl,
              maxLines: 3,
              decoration: InputDecoration(
                labelText: l.t('cancel_reason'),
                border: const OutlineInputBorder(),
              ),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: Text(l.t('cancel')),
          ),
          FilledButton(
            onPressed: () =>
                Navigator.of(dialogContext)
                    .pop(reasonCtrl.text.trim().length >= 3),
            child: Text(l.t('confirm_booking')),
          ),
        ],
      ),
    );
    final reason = reasonCtrl.text.trim();
    reasonCtrl.dispose();
    if (confirmed != true) return;
    await _run(() async {
      final reloaded = await context.read<Repo>().cancelBooking(
        booking.id,
        reason,
      );
      if (!mounted) return;
      setState(() => _future = Future.value(reloaded));
      showMessage(context, l.t('booking_cancelled'));
    });
  }

  Future<void> _payFlow(Booking booking) => _run(() async {
    final repo = context.read<Repo>();
    final order = await repo.createOrder(booking.id);
    if (order.needsPayment) await repo.pay(order);
    _refresh();
  });

  Future<void> _reviewFlow(Booking booking) async {
    final l = AppLocalizations.of(context);
    var rating = 5;
    final comment = TextEditingController();
    final submitted = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => StatefulBuilder(
        builder: (context, setDialog) => AlertDialog(
          title: Text(l.t('write_review')),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                l.t('your_rating'),
                style: Theme.of(context).textTheme.bodySmall,
              ),
              Row(
                children: [
                  for (var i = 1; i <= 5; i++)
                    IconButton(
                      onPressed: () => setDialog(() => rating = i),
                      icon: Icon(
                        i <= rating
                            ? Icons.star_rounded
                            : Icons.star_outline_rounded,
                        color: Colors.amber.shade700,
                      ),
                    ),
                ],
              ),
              TextField(
                controller: comment,
                maxLines: 3,
                decoration: InputDecoration(
                  labelText: l.t('comment'),
                  border: const OutlineInputBorder(),
                ),
              ),
            ],
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(dialogContext).pop(false),
              child: Text(l.t('cancel')),
            ),
            FilledButton(
              onPressed: () => Navigator.of(dialogContext).pop(true),
              child: Text(l.t('submit')),
            ),
          ],
        ),
      ),
    );
    if (submitted != true) {
      comment.dispose();
      return;
    }
    final text = comment.text.trim();
    comment.dispose();
    await _run(() async {
      await context.read<Repo>().postReview(
        bookingId: booking.id,
        rating: rating,
        comment: text.isEmpty ? null : text,
      );
      if (!mounted) return;
      showMessage(context, l.t('review_saved'));
      _refresh();
    });
  }

  Future<void> _disputeFlow(Booking booking) async {
    final l = AppLocalizations.of(context);
    var kind = 'quality';
    final description = TextEditingController();
    final submitted = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => StatefulBuilder(
        builder: (context, setDialog) => AlertDialog(
          title: Text(l.t('raise_dispute')),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              DropdownButton<String>(
                value: kind,
                isExpanded: true,
                items: [
                  for (final k in const [
                    'quality',
                    'no_show',
                    'payment',
                    'damage',
                    'other',
                  ])
                    DropdownMenuItem(
                      value: k,
                      child: Text(Format.titleCase(k)),
                    ),
                ],
                onChanged: (v) => setDialog(() => kind = v ?? kind),
              ),
              TextField(
                controller: description,
                maxLines: 4,
                decoration: InputDecoration(
                  labelText: l.t('describe_issue'),
                  border: const OutlineInputBorder(),
                ),
              ),
            ],
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(dialogContext).pop(false),
              child: Text(l.t('cancel')),
            ),
            FilledButton(
              onPressed: () =>
                  Navigator.of(dialogContext)
                      .pop(description.text.trim().length >= 10),
              child: Text(l.t('submit')),
            ),
          ],
        ),
      ),
    );
    final text = description.text.trim();
    description.dispose();
    if (submitted != true) return;
    await _run(() async {
      final repo = context.read<Repo>();
      await repo.openDispute(
        DisputeDraft(bookingId: booking.id, kind: kind, description: text),
      );
      final thread = await repo.startBookingThread(
        bookingId: booking.id,
        orgId: booking.orgId,
        body: text,
      );
      if (!mounted) return;
      _refresh();
      showMessage(context, l.t('dispute_opened'));
      Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => ConversationScreen(conversationId: thread.id),
        ),
      );
    });
  }

  /// An order exists only once invoiced; showing "no order" would be a lie
  /// before the customer has been charged, so the card appears with the order.
  Widget _orderCard(Booking booking) {
    final l = AppLocalizations.of(context);
    final repo = context.read<Repo>();
    return SectionCard(
      title: l.t('order'),
      child: AsyncView<List<Order>>(
        loader: () async {
          final page = await repo.orders(limit: 50);
          return page.items
              .where((o) => o.bookingId == booking.id)
              .toList(growable: false);
        },
        onEmpty: (context, orders) => orders.isEmpty
            ? Padding(
                padding: const EdgeInsets.symmetric(vertical: 8),
                child: Text(
                  l.t('not_invoiced_yet'),
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              )
            : null,
        builder: (context, orders) {
          final o = orders.first;
          return Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              InfoRow(label: l.t('order_number'), value: o.number),
              for (final line in o.lineItems)
                InfoRow(
                  label: '${line.description} × ${line.qty}',
                  value: Format.money(line.totalCents, o.currency),
                ),
              if (o.discountCents > 0)
                InfoRow(
                  label: l.t('discount'),
                  value: '-${Format.money(o.discountCents, o.currency)}',
                ),
              InfoRow(
                label: l.t('total'),
                value: Format.money(o.totalCents, o.currency),
              ),
              StatusChip(status: o.status),
            ],
          );
        },
      ),
    );
  }
}
