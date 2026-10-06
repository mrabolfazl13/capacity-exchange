import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/formatters.dart';
import '../core/l10n.dart';
import '../data/repo.dart';
import '../models/offer.dart';
import '../models/envelope.dart';
import '../widgets/common.dart';
import 'booking_flow.dart';

/// A listing as the marketplace shows it: price and unit, who provides it,
/// when it is actually free, and what past customers said. Booking starts here
/// so the cancellation policy is on screen before anything is held.
class OfferDetailScreen extends StatefulWidget {
  const OfferDetailScreen({super.key, required this.offerId});

  final String offerId;

  @override
  State<OfferDetailScreen> createState() => _OfferDetailScreenState();
}

class _OfferDetailScreenState extends State<OfferDetailScreen> {
  /// Availability is fetched a fortnight at a time: a whole season would cost
  /// the server a scan and the phone a screenful of nothing.
  static const _horizon = Duration(days: 14);

  late Future<Offer> _offerFuture;
  Offer? _offer;
  DateTime _from = DateTime.now();
  int _windowsEpoch = 0;

  @override
  void initState() {
    super.initState();
    _offerFuture = _fetch();
  }

  Future<Offer> _fetch() {
    final repo = context.read<Repo>();
    return repo.offer(widget.offerId).then((offer) {
      if (mounted) setState(() => _offer = offer);
      return offer;
    });
  }

  void _retry() => setState(() {
    _offer = null;
    _offerFuture = _fetch();
  });

  void _openBooking(Offer offer, {DateTime? start, DateTime? end}) {
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => BookingFlowScreen(
          offer: offer,
          initialStart: start,
          initialEnd: end,
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context);
    return Scaffold(
      appBar: AppBar(title: Text(l.t('details'))),
      body: FutureBuilder<Offer>(
        future: _offerFuture,
        builder: (context, snapshot) {
          if (snapshot.hasError) {
            return ErrorView(error: snapshot.error!, onRetry: _retry);
          }
          final offer = _offer;
          if (offer == null) {
            return const Center(child: CircularProgressIndicator());
          }
          return ListView(
            padding: const EdgeInsets.only(bottom: 120),
            children: [
              _header(offer),
              if (offer.description.isNotEmpty)
                SectionCard(
                  title: l.t('description'),
                  child: Text(
                    offer.description,
                    style: Theme.of(context).textTheme.bodyMedium,
                  ),
                ),
              SectionCard(
                title: l.t('availability'),
                trailing: TextButton.icon(
                  onPressed: () => setState(() {
                    _from = _from.add(_horizon);
                    _windowsEpoch++;
                  }),
                  icon: const Icon(Icons.chevron_right, size: 18),
                  label: Text(l.t('next_fortnight')),
                ),
                child: _availability(offer),
              ),
              if (offer.cancellationPolicy.isNotEmpty) _policyCard(offer),
              _reviewsCard(),
            ],
          );
        },
      ),
      bottomNavigationBar: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: FilledButton.icon(
            onPressed: _offer == null ? null : () => _openBooking(_offer!),
            icon: const Icon(Icons.event_available),
            label: Text(l.t('book')),
          ),
        ),
      ),
    );
  }

  Widget _header(Offer offer) {
    final l = AppLocalizations.of(context);
    final theme = Theme.of(context);
    final where = [
      if ((offer.line1 ?? '').isNotEmpty) offer.line1!,
      if ((offer.city ?? '').isNotEmpty) offer.city!,
      if ((offer.country ?? '').isNotEmpty) offer.country!,
    ].join(' · ');
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(offer.title, style: theme.textTheme.headlineSmall),
          const SizedBox(height: 4),
          Text(
            [
              '${l.t('provider')}: ${offer.orgName}',
              if (offer.resourceName.isNotEmpty) offer.resourceName,
            ].where((s) => s.trim().isNotEmpty).join(' · '),
            style: theme.textTheme.bodyMedium,
          ),
          if (where.isNotEmpty)
            Text(
              where,
              style: theme.textTheme.bodySmall?.copyWith(
                color: theme.colorScheme.onSurfaceVariant,
              ),
            ),
          const SizedBox(height: 10),
          Row(
            children: [
              Expanded(
                child: Text.rich(
                  TextSpan(
                    style: theme.textTheme.titleLarge,
                    children: [
                      TextSpan(
                        text: Format.money(
                          offer.unitAmountCents,
                          offer.currency,
                        ),
                      ),
                      TextSpan(
                        text: ' / ${offer.pricingLabel}',
                        style: theme.textTheme.bodySmall?.copyWith(
                          color: theme.colorScheme.onSurfaceVariant,
                        ),
                      ),
                    ],
                  ),
                ),
              ),
              RatingLine(avg: offer.ratingAvg, count: offer.ratingCount),
            ],
          ),
          const SizedBox(height: 8),
          Wrap(
            spacing: 6,
            runSpacing: 6,
            children: [
              StatusChip(
                status: offer.isInstant ? 'instant' : 'request_confirm',
                color: offer.isInstant ? Colors.teal : Colors.blueGrey,
              ),
              if ((offer.categoryLabel ?? '').isNotEmpty)
                StatusChip(
                  status: offer.categoryLabel!,
                  color: theme.colorScheme.primary,
                ),
              if (offer.minQuantity != null)
                _constraint('${l.t('min')} ${offer.minQuantity}'),
              if (offer.maxQuantity != null)
                _constraint('${l.t('max')} ${offer.maxQuantity}'),
              if (offer.slotDurationMinutes != null)
                _constraint('${offer.slotDurationMinutes}m ${l.t('per_slot')}'),
              if (offer.minLeadTimeMinutes > 0)
                _constraint('${l.t('lead_time')} ${offer.minLeadTimeMinutes}m'),
              _constraint('${l.t('hold_minutes')} ${offer.holdMinutes}m'),
            ],
          ),
        ],
      ),
    );
  }

  Widget _constraint(String text) => Chip(
    label: Text(text, style: Theme.of(context).textTheme.labelSmall),
    visualDensity: VisualDensity.compact,
    materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
  );

  Widget _policyCard(Offer offer) {
    final l = AppLocalizations.of(context);
    final bands = [...offer.cancellationPolicy]
      ..sort((a, b) => b.hoursBefore.compareTo(a.hoursBefore));
    return SectionCard(
      title: l.t('cancellation_policy'),
      child: Column(
        children: [
          for (final band in bands)
            InfoRow(
              label: '${l.t('at_least')} ${band.hoursBefore}h',
              value: '${band.refundPct}% ${l.t('refund')}',
            ),
        ],
      ),
    );
  }

  Widget _reviewsCard() {
    final l = AppLocalizations.of(context);
    final theme = Theme.of(context);
    final repo = context.read<Repo>();
    return SectionCard(
      title: l.t('reviews'),
      child: AsyncView<ListEnvelope<Review>>(
        loader: () => repo.offerReviews(widget.offerId),
        onEmpty: (context, page) => page.items.isEmpty
            ? Padding(
                padding: const EdgeInsets.symmetric(vertical: 8),
                child: Text(
                  l.t('no_reviews'),
                  style: theme.textTheme.bodySmall,
                ),
              )
            : null,
        builder: (context, page) => Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [for (final r in page.items) _reviewTile(l, r)],
        ),
      ),
    );
  }

  Widget _reviewTile(AppLocalizations l, Review r) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              for (var i = 1; i <= 5; i++)
                Icon(
                  i <= r.rating
                      ? Icons.star_rounded
                      : Icons.star_outline_rounded,
                  size: 16,
                  color: Colors.amber.shade700,
                ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  r.reviewerName.isEmpty ? l.t('anonymous') : r.reviewerName,
                  style: theme.textTheme.bodySmall,
                ),
              ),
              if (r.createdAt != null)
                Text(
                  Format.relative(r.createdAt!),
                  style: theme.textTheme.bodySmall?.copyWith(
                    color: theme.colorScheme.onSurfaceVariant,
                  ),
                ),
            ],
          ),
          if ((r.comment ?? '').isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 2),
              child: Text(r.comment!, style: theme.textTheme.bodyMedium),
            ),
          if ((r.providerReply ?? '').isNotEmpty)
            Container(
              margin: const EdgeInsets.only(top: 6),
              padding: const EdgeInsets.all(8),
              decoration: BoxDecoration(
                color: theme.colorScheme.surfaceContainerHighest,
                borderRadius: BorderRadius.circular(8),
              ),
              child: Text(
                '${l.t('provider')}: ${r.providerReply}',
                style: theme.textTheme.bodySmall,
              ),
            ),
        ],
      ),
    );
  }

  Widget _availability(Offer offer) {
    final l = AppLocalizations.of(context);
    final theme = Theme.of(context);
    final repo = context.read<Repo>();
    return AsyncView<ListEnvelope<FreeWindow>>(
      key: ValueKey(_windowsEpoch),
      loader: () => repo.offerAvailability(offer.id, from: _from),
      onEmpty: (context, page) => page.items.isEmpty
          ? Padding(
              padding: const EdgeInsets.symmetric(vertical: 8),
              child: Text(
                _from.isAfter(DateTime.now())
                    ? l.t('no_windows_later')
                    : l.t('no_windows'),
                style: theme.textTheme.bodySmall,
              ),
            )
          : null,
      builder: (context, page) => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Padding(
            padding: const EdgeInsets.only(bottom: 4),
            child: Text(
              '${Format.date(_from)} → ${Format.date(_from.add(_horizon))}',
              style: theme.textTheme.bodySmall?.copyWith(
                color: theme.colorScheme.onSurfaceVariant,
              ),
            ),
          ),
          for (final w in page.items.take(12))
            Card(
              margin: const EdgeInsets.symmetric(vertical: 4),
              child: ListTile(
                dense: true,
                title: Text(
                  '${Format.dateTime(w.start)} → ${Format.time(w.end)}',
                ),
                subtitle: Text('${w.freeQuantity} ${offer.unitLabel}'),
                trailing: const Icon(Icons.chevron_right),
                onTap: () => _openBooking(offer, start: w.start, end: w.end),
              ),
            ),
          if (page.items.length > 12)
            Text(
              '+${page.items.length - 12} ${l.t('more_windows')}',
              style: theme.textTheme.bodySmall,
            ),
        ],
      ),
    );
  }
}
