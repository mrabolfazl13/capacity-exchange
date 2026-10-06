import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_exception.dart';
import '../core/formatters.dart';
import '../core/l10n.dart';
import '../data/repo.dart';
import '../models/offer.dart';
import '../models/envelope.dart';
import '../models/query.dart';
import '../models/user.dart';
import '../widgets/common.dart';
import 'offer_detail.dart';

/// Search results. The filter set is the server's query string, not a client
/// view over a downloaded list: every change re-queries `GET /offers` so what
/// is on screen is what the marketplace actually holds (§8).
class DiscoverScreen extends StatefulWidget {
  const DiscoverScreen({super.key, this.initialQuery, this.initialCategoryId});

  final String? initialQuery;
  final String? initialCategoryId;

  @override
  State<DiscoverScreen> createState() => _DiscoverScreenState();
}

class _DiscoverScreenState extends State<DiscoverScreen> {
  late final TextEditingController _search;
  final _city = TextEditingController();
  late OfferFilters _filters;
  final _scroll = ScrollController();

  /// Bumping this restarts the query from page one; results is kept so an
  /// existing list does not flash away while the new one loads.
  int _epoch = 0;
  ListEnvelope<Offer>? _results;
  bool _loadingMore = false;
  Object? _moreError;

  @override
  void initState() {
    super.initState();
    _search = TextEditingController(text: widget.initialQuery ?? '');
    _filters = OfferFilters(
      q: widget.initialQuery,
      categoryId: widget.initialCategoryId,
    );
    _scroll.addListener(_onScroll);
  }

  @override
  void didUpdateWidget(DiscoverScreen old) {
    super.didUpdateWidget(old);
    if (old.initialQuery != widget.initialQuery ||
        old.initialCategoryId != widget.initialCategoryId) {
      _search.text = widget.initialQuery ?? '';
      _apply(
        _filters.copyWith(
          q: widget.initialQuery,
          categoryId: widget.initialCategoryId,
        ),
      );
    }
  }

  @override
  void dispose() {
    _scroll.removeListener(_onScroll);
    _scroll.dispose();
    _search.dispose();
    _city.dispose();
    super.dispose();
  }

  void _apply(OfferFilters next) => setState(() {
    _filters = next;
    _epoch++;
    _results = null;
    _moreError = null;
  });

  /// Paging is scroll-driven rather than a button hunt on a small screen.
  void _onScroll() {
    if (!_scroll.hasClients || _loadingMore) return;
    final current = _results;
    if (current == null || !current.hasMore) return;
    if (_scroll.position.pixels < _scroll.position.maxScrollExtent - 240) {
      return;
    }
    _loadMore();
  }

  Future<void> _loadMore() async {
    final repo = context.read<Repo>();
    final current = _results!;
    setState(() {
      _loadingMore = true;
      _moreError = null;
    });
    try {
      final next = await repo.searchOffers(
        _filters.copyWith(offset: current.offset + current.limit),
      );
      if (!mounted) return;
      setState(() {
        _results = ListEnvelope<Offer>(
          items: [...current.items, ...next.items],
          total: next.total,
          limit: next.limit,
          offset: next.offset,
        );
      });
    } catch (e) {
      if (mounted) setState(() => _moreError = e);
    } finally {
      if (mounted) setState(() => _loadingMore = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context);
    final repo = context.read<Repo>();
    return Scaffold(
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 8, 12, 4),
            child: Row(
              children: [
                Expanded(
                  child: SearchBar(
                    controller: _search,
                    hintText: l.t('search_offers'),
                    leading: const Padding(
                      padding: EdgeInsets.only(left: 12),
                      child: Icon(Icons.search),
                    ),
                    trailing: [
                      if (_filters.hasActiveFilters)
                        IconButton(
                          onPressed: () {
                            _search.clear();
                            _apply(
                              OfferFilters(
                                q: _search.text,
                                sort: _filters.sort,
                              ),
                            );
                          },
                          icon: const Icon(Icons.close),
                          tooltip: l.t('reset'),
                        ),
                    ],
                    onSubmitted: (v) =>
                        _apply(_filters.copyWith(q: v, offset: 0)),
                  ),
                ),
                IconButton(
                  onPressed: _openFilters,
                  icon: Badge(
                    isLabelVisible: _filters.activeFilterCount > 0,
                    label: Text('${_filters.activeFilterCount}'),
                    child: const Icon(Icons.tune),
                  ),
                  tooltip: l.t('filters'),
                ),
              ],
            ),
          ),
          Expanded(
            child: AsyncView<ListEnvelope<Offer>>(
              key: ValueKey(_epoch),
              loader: () async {
                final page = await repo.searchOffers(_filters);
                _results = page;
                return page;
              },
              builder: (context, page) => _list(page),
              onEmpty: (context, page) => page.items.isEmpty
                  ? EmptyView(
                      message: l.t('no_results'),
                      hint: l.t('no_results_hint'),
                      icon: Icons.search_off,
                    )
                  : null,
            ),
          ),
        ],
      ),
    );
  }

  Widget _list(ListEnvelope<Offer> page) {
    return ListView.builder(
      controller: _scroll,
      padding: const EdgeInsets.only(bottom: 96),
      itemCount: page.items.length + (_loadingMore ? 1 : 0),
      itemBuilder: (context, i) {
        if (i >= page.items.length) {
          return const Padding(
            padding: EdgeInsets.all(16),
            child: Center(child: CircularProgressIndicator()),
          );
        }
        final offer = page.items[i];
        return Column(
          children: [
            OfferCard(offer: offer),
            if (_moreError != null && i == page.items.length - 1)
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16),
                child: Row(
                  children: [
                    Expanded(
                      child: Text(
                        _moreError is ApiException
                            ? (_moreError as ApiException).message
                            : '$_moreError',
                        style: Theme.of(context).textTheme.bodySmall,
                      ),
                    ),
                    TextButton(
                      onPressed: _loadMore,
                      child: Text(AppLocalizations.of(context).t('retry')),
                    ),
                  ],
                ),
              ),
          ],
        );
      },
    );
  }

  Future<void> _openFilters() async {
    final l = AppLocalizations.of(context);
    final repo = context.read<Repo>();
    var categories = <Category>[];
    try {
      categories = await repo.categories();
    } catch (_) {
      // The sheet still works without the category list — the other filters
      // are independent, and a failed lookup must not block the rest.
    }
    if (!mounted) return;

    OfferFilters draft = _filters;
    _city.text = _filters.city ?? '';
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      builder: (sheetContext) => StatefulBuilder(
        builder: (context, setDraft) {
          return Padding(
            padding: const EdgeInsets.fromLTRB(16, 16, 16, 24),
            child: SingleChildScrollView(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Text(
                    l.t('filters'),
                    style: Theme.of(context).textTheme.titleLarge,
                  ),
                  const SizedBox(height: 14),
                  _label(l.t('category')),
                  DropdownButtonHideUnderline(
                    child: DropdownButton<String?>(
                      value: draft.categoryId,
                      isExpanded: true,
                      hint: Text(l.t('any_category')),
                      items: [
                        const DropdownMenuItem<String?>(
                          value: null,
                          child: Text('—'),
                        ),
                        ...categories.map(
                          (c) => DropdownMenuItem<String?>(
                            value: c.id,
                            child: Text(c.label),
                          ),
                        ),
                      ],
                      onChanged: (v) =>
                          setDraft(() => draft = draft.copyWith(categoryId: v)),
                    ),
                  ),
                  _label(l.t('city')),
                  TextField(
                    controller: _city,
                    onSubmitted: (v) =>
                        setDraft(() => draft = draft.copyWith(city: v)),
                    decoration: const InputDecoration(
                      border: OutlineInputBorder(),
                      isDense: true,
                    ),
                  ),
                  const SizedBox(height: 12),
                  _label(l.t('min_quantity')),
                  _stepper(
                    value: draft.minQuantity ?? 1,
                    onChanged: (v) => setDraft(
                      () => draft = draft.copyWith(
                        minQuantity: v <= 1 ? null : v,
                      ),
                    ),
                  ),
                  const SizedBox(height: 12),
                  _label(l.t('max_price')),
                  Slider(
                    value: ((draft.maxUnitCents ?? 0) / 100)
                        .clamp(0, 500)
                        .toDouble(),
                    min: 0,
                    max: 500,
                    divisions: 50,
                    label: draft.maxUnitCents == null
                        ? '—'
                        : Format.money(draft.maxUnitCents!, 'USD'),
                    onChanged: (v) => setDraft(
                      () => draft = draft.copyWith(
                        maxUnitCents: v <= 0 ? null : (v * 100).round(),
                      ),
                    ),
                  ),
                  const SizedBox(height: 12),
                  _label(l.t('window')),
                  Row(
                    children: [
                      Expanded(
                        child: _dateButton(
                          label: l.t('date_from'),
                          value: draft.from,
                          onPicked: (d) =>
                              setDraft(() => draft = draft.copyWith(from: d)),
                          sheetContext: sheetContext,
                        ),
                      ),
                      const SizedBox(width: 8),
                      Expanded(
                        child: _dateButton(
                          label: l.t('date_to'),
                          value: draft.to,
                          onPicked: (d) =>
                              setDraft(() => draft = draft.copyWith(to: d)),
                          sheetContext: sheetContext,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 12),
                  _label(l.t('booking_mode')),
                  SegmentedButton<String?>(
                    segments: [
                      ButtonSegment(value: null, label: Text(l.t('any_mode'))),
                      ButtonSegment(
                        value: 'instant',
                        label: Text(l.t('mode_instant')),
                      ),
                      ButtonSegment(
                        value: 'request_confirm',
                        label: Text(l.t('mode_request_confirm')),
                      ),
                    ],
                    selected: {draft.bookingMode},
                    showSelectedIcon: false,
                    onSelectionChanged: (s) => setDraft(
                      () => draft = draft.copyWith(bookingMode: s.first),
                    ),
                  ),
                  const SizedBox(height: 12),
                  _label(l.t('sort')),
                  SegmentedButton<String>(
                    segments: [
                      ButtonSegment(
                        value: 'relevance',
                        label: Text(l.t('sort_relevance')),
                      ),
                      ButtonSegment(
                        value: 'price_asc',
                        label: Text(l.t('sort_price_asc')),
                      ),
                      ButtonSegment(
                        value: 'rating',
                        label: Text(l.t('sort_rating')),
                      ),
                    ],
                    selected: {draft.sort},
                    showSelectedIcon: false,
                    onSelectionChanged: (s) =>
                        setDraft(() => draft = draft.copyWith(sort: s.first)),
                  ),
                  const SizedBox(height: 20),
                  Row(
                    children: [
                      Expanded(
                        child: OutlinedButton(
                          onPressed: () =>
                              setDraft(() => draft = draft.cleared()),
                          child: Text(l.t('reset')),
                        ),
                      ),
                      const SizedBox(width: 10),
                      Expanded(
                        child: FilledButton(
                          onPressed: () {
                            Navigator.of(sheetContext).pop();
                            _apply(draft.copyWith(city: _city.text, offset: 0));
                          },
                          child: Text(l.t('apply')),
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          );
        },
      ),
    );
  }

  static Widget _label(String text) => Builder(
    builder: (context) => Padding(
      padding: const EdgeInsets.only(bottom: 4, top: 8),
      child: Text(text, style: Theme.of(context).textTheme.bodySmall),
    ),
  );

  Widget _stepper({required int value, required ValueChanged<int> onChanged}) =>
      Row(
        children: [
          IconButton(
            onPressed: () => onChanged(value - 1),
            icon: const Icon(Icons.remove_circle_outline),
          ),
          Text('$value'),
          IconButton(
            onPressed: () => onChanged(value + 1),
            icon: const Icon(Icons.add_circle_outline),
          ),
        ],
      );

  Widget _dateButton({
    required String label,
    required DateTime? value,
    required ValueChanged<DateTime> onPicked,
    required BuildContext sheetContext,
  }) {
    return OutlinedButton(
      onPressed: () async {
        final picked = await showDatePicker(
          context: sheetContext,
          initialDate: value ?? DateTime.now(),
          firstDate: DateTime.now().subtract(const Duration(days: 1)),
          lastDate: DateTime.now().add(const Duration(days: 365)),
        );
        if (picked != null) onPicked(picked);
      },
      child: Text(value == null ? label : Format.date(value)),
    );
  }
}

/// One search result. Tapping opens the listing; booking happens there so the
/// quantity and window are chosen with the policy visible.
class OfferCard extends StatelessWidget {
  const OfferCard({super.key, required this.offer});

  final Offer offer;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final where = [
      if ((offer.line1 ?? '').isNotEmpty) offer.line1!,
      if ((offer.city ?? '').isNotEmpty) offer.city!,
    ].join(' · ');
    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 5),
      child: ListTile(
        contentPadding: const EdgeInsets.fromLTRB(14, 10, 14, 12),
        title: Text(offer.title, maxLines: 2, overflow: TextOverflow.ellipsis),
        subtitle: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SizedBox(height: 2),
            Text(
              [
                offer.orgName,
                if (offer.resourceName.isNotEmpty) offer.resourceName,
              ].where((s) => s.isNotEmpty).join(' · '),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: theme.textTheme.bodySmall,
            ),
            if (where.isNotEmpty)
              Text(
                where,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: theme.textTheme.bodySmall?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant,
                ),
              ),
            const SizedBox(height: 6),
            Row(
              children: [
                Expanded(
                  child: Wrap(
                    spacing: 6,
                    runSpacing: 4,
                    crossAxisAlignment: WrapCrossAlignment.center,
                    children: [
                      RatingLine(
                        avg: offer.ratingAvg,
                        count: offer.ratingCount,
                      ),
                      if (offer.isInstant)
                        StatusChip(status: 'instant', color: Colors.teal),
                      if ((offer.categoryLabel ?? '').isNotEmpty)
                        StatusChip(
                          status: offer.categoryLabel!,
                          color: theme.colorScheme.primary,
                        ),
                    ],
                  ),
                ),
              ],
            ),
          ],
        ),
        trailing: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          crossAxisAlignment: CrossAxisAlignment.end,
          children: [
            MoneyText(offer.unitAmountCents, offer.currency, compact: true),
            Text(
              offer.pricingLabel,
              style: theme.textTheme.bodySmall?.copyWith(
                color: theme.colorScheme.onSurfaceVariant,
              ),
            ),
          ],
        ),
        onTap: () => Navigator.of(context).push(
          MaterialPageRoute<void>(
            builder: (_) => OfferDetailScreen(offerId: offer.id),
          ),
        ),
      ),
    );
  }
}
