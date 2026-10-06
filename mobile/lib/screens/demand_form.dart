import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/formatters.dart';
import '../core/l10n.dart';
import '../data/repo.dart';
import '../models/demand.dart';
import '../models/envelope.dart';
import '../models/query.dart';
import '../models/user.dart';
import '../widgets/common.dart';

/// Post a demand: what is needed, when, and for how much. The matching engine
/// scores published offers against it server-side (§5.3), so the form asks for
/// what a score needs — category, window, quantity, budget — and nothing else.
class DemandFormScreen extends StatefulWidget {
  const DemandFormScreen({super.key});

  @override
  State<DemandFormScreen> createState() => _DemandFormScreenState();
}

class _DemandFormScreenState extends State<DemandFormScreen> {
  final _formKey = GlobalKey<FormState>();
  final _description = TextEditingController();
  final _city = TextEditingController();
  final _budgetMin = TextEditingController();
  final _budgetMax = TextEditingController();

  List<Category> _categories = const [];
  String? _categoryId;
  DateTime? _start;
  DateTime? _end;
  int _quantity = 1;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    context
        .read<Repo>()
        .categories()
        .then((items) {
          if (mounted) setState(() => _categories = items);
        })
        .catchError((Object _) {
          // A missing category list narrows the search, it must not block posting.
        });
  }

  @override
  void dispose() {
    _description.dispose();
    _city.dispose();
    _budgetMin.dispose();
    _budgetMax.dispose();
    super.dispose();
  }

  int? _cents(TextEditingController c) {
    final v = double.tryParse(c.text.replaceAll(',', '').trim());
    return v == null ? null : (v * 100).round();
  }

  Future<void> _submit() async {
    if (!_formKey.currentState!.validate()) return;
    setState(() => _busy = true);
    try {
      final created = await context.read<Repo>().postDemand(
        DemandDraft(
          description: _description.text,
          categoryId: _categoryId,
          city: _city.text,
          desiredStart: _start,
          desiredEnd: _end,
          quantity: _quantity,
          budgetMinCents: _cents(_budgetMin),
          budgetMaxCents: _cents(_budgetMax),
        ),
      );
      if (!mounted) return;
      Navigator.of(context).pushReplacement(
        MaterialPageRoute<void>(
          builder: (_) => DemandDetailScreen(demandId: created.id),
        ),
      );
    } catch (e) {
      if (mounted) showError(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context);
    return Scaffold(
      appBar: AppBar(title: Text(l.t('post_demand'))),
      body: Form(
        key: _formKey,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            TextFormField(
              controller: _description,
              minLines: 3,
              maxLines: 6,
              decoration: InputDecoration(
                labelText: l.t('describe_need'),
                border: const OutlineInputBorder(),
              ),
              validator: (v) => (v ?? '').trim().length < 10
                  ? l.t('err_describe_need')
                  : null,
            ),
            const SizedBox(height: 12),
            DropdownButtonFormField<String?>(
              initialValue: _categoryId,
              decoration: InputDecoration(
                labelText: l.t('category'),
                border: const OutlineInputBorder(),
              ),
              items: [
                DropdownMenuItem<String?>(
                  value: null,
                  child: Text(l.t('any_category')),
                ),
                for (final c in _categories)
                  DropdownMenuItem<String?>(value: c.id, child: Text(c.label)),
              ],
              onChanged: (v) => setState(() => _categoryId = v),
            ),
            const SizedBox(height: 12),
            TextFormField(
              controller: _city,
              decoration: InputDecoration(
                labelText: l.t('city'),
                border: const OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 12),
            Row(
              children: [
                Expanded(
                  child: OutlinedButton.icon(
                    onPressed: () async {
                      final picked = await showDatePicker(
                        context: context,
                        initialDate: _start ?? DateTime.now(),
                        firstDate: DateTime.now(),
                        lastDate: DateTime.now().add(const Duration(days: 365)),
                      );
                      if (picked != null) setState(() => _start = picked);
                    },
                    icon: const Icon(Icons.schedule),
                    label: Text(
                      _start == null ? l.t('date_from') : Format.date(_start!),
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: OutlinedButton.icon(
                    onPressed: () async {
                      final picked = await showDatePicker(
                        context: context,
                        initialDate: _end ?? _start ?? DateTime.now(),
                        firstDate: _start ?? DateTime.now(),
                        lastDate: DateTime.now().add(const Duration(days: 366)),
                      );
                      if (picked != null) setState(() => _end = picked);
                    },
                    icon: const Icon(Icons.schedule),
                    label: Text(
                      _end == null ? l.t('date_to') : Format.date(_end!),
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 12),
            Row(
              children: [
                Text(
                  l.t('quantity'),
                  style: Theme.of(context).textTheme.bodyMedium,
                ),
                const Spacer(),
                IconButton(
                  onPressed: () => setState(
                    () => _quantity = (_quantity - 1).clamp(1, 9999),
                  ),
                  icon: const Icon(Icons.remove_circle_outline),
                ),
                Text('$_quantity'),
                IconButton(
                  onPressed: () => setState(
                    () => _quantity = (_quantity + 1).clamp(1, 9999),
                  ),
                  icon: const Icon(Icons.add_circle_outline),
                ),
              ],
            ),
            const SizedBox(height: 12),
            Row(
              children: [
                Expanded(
                  child: TextFormField(
                    controller: _budgetMin,
                    keyboardType: const TextInputType.numberWithOptions(
                      decimal: true,
                    ),
                    decoration: InputDecoration(
                      labelText: l.t('budget_min'),
                      border: const OutlineInputBorder(),
                      prefixIcon: const Icon(Icons.attach_money),
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: TextFormField(
                    controller: _budgetMax,
                    keyboardType: const TextInputType.numberWithOptions(
                      decimal: true,
                    ),
                    decoration: InputDecoration(
                      labelText: l.t('budget_max'),
                      border: const OutlineInputBorder(),
                      prefixIcon: const Icon(Icons.attach_money),
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 20),
            FilledButton(
              onPressed: _busy ? null : _submit,
              child: Text(l.t('submit')),
            ),
          ],
        ),
      ),
    );
  }
}

/// A demand with the offers the engine scored against it.
class DemandDetailScreen extends StatefulWidget {
  const DemandDetailScreen({super.key, required this.demandId});

  final String demandId;

  @override
  State<DemandDetailScreen> createState() => _DemandDetailScreenState();
}

class _DemandDetailScreenState extends State<DemandDetailScreen> {
  late Future<Demand> _future;

  @override
  void initState() {
    super.initState();
    _future = context.read<Repo>().demand(widget.demandId);
  }

  void _retry() =>
      setState(() => _future = context.read<Repo>().demand(widget.demandId));

  Future<void> _verb(Future<void> Function(Repo) verb) async {
    final repo = context.read<Repo>();
    try {
      await verb(repo);
      _retry();
    } catch (e) {
      if (mounted) showError(context, e);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context);
    final repo = context.read<Repo>();
    return Scaffold(
      appBar: AppBar(
        title: Text(l.t('demand')),
        actions: [
          IconButton(
            onPressed: _retry,
            icon: const Icon(Icons.refresh_outlined),
          ),
        ],
      ),
      body: FutureBuilder<Demand>(
        future: _future,
        builder: (context, snapshot) {
          if (snapshot.hasError) {
            return ErrorView(error: snapshot.error!, onRetry: _retry);
          }
          if (!snapshot.hasData) {
            return const Center(child: CircularProgressIndicator());
          }
          final d = snapshot.data!;
          return ListView(
            padding: const EdgeInsets.only(bottom: 32),
            children: [
              SectionCard(
                title: d.categoryLabel.isEmpty
                    ? l.t('demand')
                    : d.categoryLabel,
                trailing: StatusChip(status: d.status),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Text(
                      d.description,
                      style: Theme.of(context).textTheme.bodyMedium,
                    ),
                    const SizedBox(height: 10),
                    if ((d.city ?? '').isNotEmpty)
                      InfoRow(label: l.t('city'), value: d.city!),
                    if (d.desiredStart != null)
                      InfoRow(
                        label: l.t('date_from'),
                        value: Format.date(d.desiredStart!),
                      ),
                    if (d.desiredEnd != null)
                      InfoRow(
                        label: l.t('date_to'),
                        value: Format.date(d.desiredEnd!),
                      ),
                    InfoRow(label: l.t('quantity'), value: '${d.quantity}'),
                    if (d.budgetMinCents != null || d.budgetMaxCents != null)
                      InfoRow(
                        label: l.t('budget'),
                        value: [
                          if (d.budgetMinCents != null)
                            Format.money(d.budgetMinCents!, d.currency),
                          if (d.budgetMaxCents != null)
                            Format.money(d.budgetMaxCents!, d.currency),
                        ].join(' – '),
                      ),
                    if (d.expiresAt != null)
                      InfoRow(
                        label: l.t('expires'),
                        value: Format.dateTime(d.expiresAt!),
                      ),
                  ],
                ),
              ),
              if (d.isOpen)
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 16),
                  child: Wrap(
                    spacing: 8,
                    children: [
                      OutlinedButton(
                        onPressed: () => _verb((r) => r.closeDemand(d.id)),
                        child: Text(l.t('close_demand')),
                      ),
                      OutlinedButton(
                        onPressed: () => _verb((r) => r.cancelDemand(d.id)),
                        child: Text(l.t('cancel')),
                      ),
                    ],
                  ),
                ),
              SectionCard(
                title: l.t('matched_offers'),
                child: AsyncView<ListEnvelope<Match>>(
                  loader: () => repo.demandMatches(d.id),
                  onEmpty: (context, page) => page.items.isEmpty
                      ? Padding(
                          padding: const EdgeInsets.symmetric(vertical: 8),
                          child: Text(
                            l.t('no_matches_yet'),
                            style: Theme.of(context).textTheme.bodySmall,
                          ),
                        )
                      : null,
                  builder: (context, page) => Column(
                    children: [for (final m in page.items) _matchTile(m)],
                  ),
                ),
              ),
            ],
          );
        },
      ),
    );
  }

  Widget _matchTile(Match m) {
    final theme = Theme.of(context);
    return ListTile(
      contentPadding: EdgeInsets.zero,
      title: Text(m.offerTitle, style: theme.textTheme.bodyLarge),
      subtitle: Text(
        [
          m.orgName,
          '${m.score.round()}% ${AppLocalizations.of(context).t('match_score')}',
          if (m.reasons.isNotEmpty) m.reasons.take(2).join(', '),
        ].where((s) => s.isNotEmpty).join(' · '),
        style: theme.textTheme.bodySmall,
      ),
      trailing: StatusChip(status: m.status),
    );
  }
}
