import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/formatters.dart';
import '../core/l10n.dart';
import '../data/actions.dart';
import '../data/repo.dart';
import '../models/booking.dart';
import '../models/offer.dart';
import '../models/order.dart';
import '../models/envelope.dart';
import '../state/session.dart';
import '../widgets/common.dart';
import 'booking_detail.dart';

/// Hold → confirm → pay, in the order the state machine allows (§5.4/§5.5).
///
/// The customer never sees a price they have to trust: the hold is answered
/// with the server's own `total_cents`, and payment only becomes possible once
/// an order exists. Each step re-reads the booking from the response, so what
/// is on screen is the row the server wrote, not a client-side guess.
class BookingFlowScreen extends StatefulWidget {
  const BookingFlowScreen({
    super.key,
    required this.offer,
    this.initialStart,
    this.initialEnd,
  });

  final Offer offer;
  final DateTime? initialStart;
  final DateTime? initialEnd;

  @override
  State<BookingFlowScreen> createState() => _BookingFlowScreenState();
}

enum _Stage { pick, holding, booking, paid }

class _BookingFlowScreenState extends State<BookingFlowScreen> {
  DateTime? _start;
  DateTime? _end;
  int _quantity = 1;

  Booking? _booking;
  Order? _order;
  Payment? _payment;
  bool _busy = false;
  _Stage _stage = _Stage.pick;

  @override
  void initState() {
    super.initState();
    _start = widget.initialStart;
    _end = widget.initialEnd;
    _quantity = widget.offer.minQuantity ?? 1;
  }

  Repo get _repo => context.read<Repo>();

  List<String> get _issues => _start == null || _end == null
      ? const ['err_pick_window']
      : holdIssues(
          offer: widget.offer,
          start: _start!,
          end: _end!,
          quantity: _quantity,
        );

  Future<void> _guard(Future<void> Function() action) async {
    setState(() => _busy = true);
    try {
      await action();
    } catch (e) {
      if (mounted) showError(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _placeHold() => _guard(() async {
    final booking = await _repo.placeHold(
      offerId: widget.offer.id,
      windowStart: _start!,
      windowEnd: _end!,
      quantity: _quantity,
    );
    if (!mounted) return;
    setState(() {
      _booking = booking;
      _stage = _Stage.holding;
    });
  });

  Future<void> _confirm() => _guard(() async {
    final booking = await _repo.createBookingFromHold(_booking!.id);
    if (!mounted) return;
    setState(() {
      _booking = booking;
      _stage = _Stage.booking;
    });
  });

  /// Invoice the booking and take the payment. A `not_required` order (a free
  /// or fully-discounted total) answers paid, so the step still completes.
  Future<void> _pay() => _guard(() async {
    final booking = _booking!;
    final order = await _repo.createOrder(booking.id);
    if (!mounted) return;
    setState(() => _order = order);
    if (order.needsPayment) {
      final payment = await _repo.pay(order);
      if (!mounted) return;
      setState(() => _payment = payment);
    }
    final reloaded = await _repo.booking(booking.id);
    if (!mounted) return;
    setState(() {
      _booking = reloaded;
      _stage = _Stage.paid;
    });
  });

  Future<void> _reloadBooking() => _guard(() async {
    final booking = await _repo.booking(_booking!.id);
    if (!mounted) return;
    setState(() => _booking = booking);
  });

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context);
    return Scaffold(
      appBar: AppBar(title: Text(l.t('book'))),
      body: ListView(
        padding: const EdgeInsets.only(bottom: 120),
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
            child: Text(
              widget.offer.title,
              style: Theme.of(context).textTheme.titleLarge,
            ),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: Text(
              '${widget.offer.orgName} · ${Format.money(widget.offer.unitAmountCents, widget.offer.currency)} / ${widget.offer.pricingLabel}',
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ),
          const SizedBox(height: 8),
          _windowCard(),
          _quantityCard(),
          if (_booking != null) _bookingCard(),
          if (_order != null) _orderCard(),
        ],
      ),
      bottomNavigationBar: _ctaBar(l),
    );
  }

  // -------------------------------------------------------------- window/qty

  Widget _windowCard() {
    final l = AppLocalizations.of(context);
    final theme = Theme.of(context);
    return SectionCard(
      title: l.t('window'),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (_start != null && _end != null)
            Text(
              '${Format.dateTime(_start!)} → ${Format.time(_end!)}',
              style: theme.textTheme.titleSmall,
            ),
          if (_start == null)
            Padding(
              padding: const EdgeInsets.only(bottom: 6),
              child: Text(
                l.t('err_pick_window'),
                style: theme.textTheme.bodySmall,
              ),
            ),
          TextButton.icon(
            onPressed: _busy ? null : _pickWindow,
            icon: const Icon(Icons.schedule),
            label: Text(l.t('choose_window')),
          ),
        ],
      ),
    );
  }

  /// Window options come from the capacity engine, not a calendar guess: a
  /// free window that excludes buffers and other bookings is the only honest
  /// choice list to show.
  Future<void> _pickWindow() async {
    final l = AppLocalizations.of(context);
    final repo = context.read<Repo>();
    ListEnvelope<FreeWindow>? page;
    Object? error;
    try {
      page = await repo.offerAvailability(
        widget.offer.id,
        from: DateTime.now(),
      );
    } catch (e) {
      error = e;
    }
    if (!mounted) return;
    if (error != null) {
      showError(context, error);
      return;
    }
    final items = page!.items;
    if (items.isEmpty) {
      showMessage(context, l.t('no_windows'));
      return;
    }
    final picked = await showModalBottomSheet<FreeWindow>(
      context: context,
      builder: (sheetContext) => SafeArea(
        child: ListView(
          padding: const EdgeInsets.symmetric(vertical: 12),
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
              child: Text(
                l.t('free_windows'),
                style: Theme.of(sheetContext).textTheme.titleMedium,
              ),
            ),
            for (final w in items)
              ListTile(
                leading: const Icon(Icons.access_time),
                title: Text(Format.dateTime(w.start)),
                subtitle: Text(
                  '→ ${Format.time(w.end)} · ${w.freeQuantity} ${widget.offer.unitLabel}',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
                onTap: () => Navigator.of(sheetContext).pop(w),
              ),
          ],
        ),
      ),
    );
    if (picked == null || !mounted) return;
    setState(() {
      _start = picked.start;
      _end = picked.end;
    });
  }

  Widget _quantityCard() {
    final l = AppLocalizations.of(context);
    final min = widget.offer.minQuantity ?? 1;
    final max = widget.offer.maxQuantity ?? 9999;
    void step(int by) =>
        setState(() => _quantity = (_quantity + by).clamp(min, max));
    return SectionCard(
      title: l.t('quantity'),
      child: Row(
        children: [
          IconButton(
            onPressed: _busy || _quantity <= min ? null : () => step(-1),
            icon: const Icon(Icons.remove_circle_outline),
          ),
          Text('$_quantity', style: Theme.of(context).textTheme.titleMedium),
          IconButton(
            onPressed: _busy || _quantity >= max ? null : () => step(1),
            icon: const Icon(Icons.add_circle_outline),
          ),
          const Spacer(),
          Text(
            widget.offer.unitLabel.isEmpty
                ? '$min–$max'
                : widget.offer.unitLabel,
            style: Theme.of(context).textTheme.bodySmall,
          ),
        ],
      ),
    );
  }

  // ----------------------------------------------------------------- results

  Widget _bookingCard() {
    final l = AppLocalizations.of(context);
    final b = _booking!;
    return SectionCard(
      title: l.t('your_booking'),
      trailing: StatusChip(status: b.status),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          InfoRow(
            label: l.t('window'),
            value: b.windowStart == null
                ? '—'
                : Format.dateTime(b.windowStart!),
          ),
          InfoRow(label: l.t('quantity'), value: '${b.quantity}'),
          InfoRow(
            label: l.t('total'),
            value: Format.money(b.totalCents, b.currency),
          ),
          InfoRow(
            label: l.t('payment'),
            value: Format.titleCase(b.paymentStatus),
          ),
          if (b.isHold) HoldCountdown(booking: b, onExpired: _reloadBooking),
          if (b.status == BookingStatuses.draft ||
              b.status == BookingStatuses.hold)
            Padding(
              padding: const EdgeInsets.only(top: 6),
              child: Text(l.t('awaiting_provider')),
            ),
        ],
      ),
    );
  }

  Widget _orderCard() {
    final l = AppLocalizations.of(context);
    final o = _order!;
    return SectionCard(
      title: l.t('order'),
      trailing: StatusChip(status: o.paymentStatus),
      child: Column(
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
          if (_payment != null)
            InfoRow(
              label: l.t('payment'),
              value: Format.titleCase(_payment!.status),
            ),
        ],
      ),
    );
  }

  // --------------------------------------------------------------------- cta

  Widget _ctaBar(AppLocalizations l) {
    final issues = _stage == _Stage.pick ? _issues : const <String>[];
    final user = context.watch<Session>().user;
    Widget? button;
    if (_stage == _Stage.pick) {
      button = FilledButton(
        onPressed: issues.isEmpty && !_busy ? _placeHold : null,
        child: Text(l.t('place_hold')),
      );
    } else if (_stage == _Stage.holding) {
      button = FilledButton(
        onPressed: !_busy ? _confirm : null,
        child: Text(l.t('confirm_booking')),
      );
    } else if (_stage == _Stage.booking) {
      final actions = allowedBookingActions(_booking!, user);
      button = actions.contains(BookingAction.pay)
          ? FilledButton(
              onPressed: !_busy ? _pay : null,
              child: Text(l.t('pay_now')),
            )
          : FilledButton(onPressed: _viewBooking, child: Text(l.t('done')));
    } else if (_stage == _Stage.paid) {
      button = FilledButton(
        onPressed: _viewBooking,
        child: Text(l.t('view_booking')),
      );
    }

    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 12),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (issues.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: Column(
                  children: [
                    for (final key in issues)
                      Text(
                        l.t(key),
                        style: Theme.of(context).textTheme.bodySmall?.copyWith(
                          color: Theme.of(context).colorScheme.error,
                        ),
                      ),
                  ],
                ),
              ),
            if (button != null)
              SizedBox(height: 48, child: Center(child: button)),
          ],
        ),
      ),
    );
  }

  void _viewBooking() {
    Navigator.of(context).pushReplacement(
      MaterialPageRoute<void>(
        builder: (_) => BookingDetailScreen(bookingId: _booking!.id),
      ),
    );
  }
}
