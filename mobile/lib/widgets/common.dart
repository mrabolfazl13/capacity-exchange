import 'dart:async';

import 'package:flutter/material.dart';

import '../api/api_exception.dart';
import '../core/formatters.dart';
import '../core/l10n.dart';
import '../core/theme.dart';
import '../models/booking.dart';

/// Shared display pieces: the three load states, status chips, money and
/// dates. Nothing here knows about endpoints — data arrives as parsed models.
String t(BuildContext context, String key) =>
    AppLocalizations.of(context).t(key);

// --------------------------------------------------------------- load states

/// A card that says "nothing here" in the app's words, not an empty list.
class EmptyView extends StatelessWidget {
  const EmptyView({super.key, required this.message, this.icon, this.hint});

  final String message;
  final IconData? icon;
  final String? hint;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(28),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(
              icon ?? Icons.inbox_outlined,
              size: 40,
              color: theme.colorScheme.outline,
            ),
            const SizedBox(height: 12),
            Text(
              message,
              textAlign: TextAlign.center,
              style: theme.textTheme.titleMedium,
            ),
            if (hint != null) ...[
              const SizedBox(height: 6),
              Text(
                hint!,
                textAlign: TextAlign.center,
                style: theme.textTheme.bodySmall?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant,
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// Failures are quoted from the server: its message is the accurate one, and
/// the status code only decides the icon and the hint below it.
class ErrorView extends StatelessWidget {
  const ErrorView({super.key, required this.error, this.onRetry});

  final Object error;
  final VoidCallback? onRetry;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final api = error is ApiException ? error as ApiException : null;
    final isOffline = api != null && api.statusCode == 0;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(28),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(
              isOffline ? Icons.wifi_off : Icons.error_outline,
              size: 40,
              color: theme.colorScheme.error,
            ),
            const SizedBox(height: 12),
            Text(
              api?.message ?? '$error',
              textAlign: TextAlign.center,
              style: theme.textTheme.titleSmall,
            ),
            const SizedBox(height: 6),
            Text(
              t(context, isOffline ? 'offline_hint' : 'something_wrong'),
              textAlign: TextAlign.center,
              style: theme.textTheme.bodySmall?.copyWith(
                color: theme.colorScheme.onSurfaceVariant,
              ),
            ),
            if (onRetry != null) ...[
              const SizedBox(height: 16),
              OutlinedButton.icon(
                onPressed: onRetry,
                icon: const Icon(Icons.refresh),
                label: Text(t(context, 'retry')),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// Runs [loader] on mount and maps loading / error / empty / data onto widgets.
/// The app uses this everywhere instead of hand-rolled `FutureBuilder`s, so a
/// screen cannot silently forget the error case.
class AsyncView<T> extends StatefulWidget {
  const AsyncView({
    super.key,
    required this.loader,
    required this.builder,
    this.onEmpty,
  });

  final Future<T> Function() loader;
  final Widget Function(BuildContext context, T value) builder;
  final Widget? Function(BuildContext context, T value)? onEmpty;

  @override
  State<AsyncView<T>> createState() => _AsyncViewState<T>();
}

class _AsyncViewState<T> extends State<AsyncView<T>> {
  late Future<T> _future;

  @override
  void initState() {
    super.initState();
    _future = widget.loader();
  }

  void _reload() => setState(() => _future = widget.loader());

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<T>(
      future: _future,
      builder: (context, snapshot) {
        if (snapshot.connectionState != ConnectionState.done) {
          return const Center(child: CircularProgressIndicator());
        }
        if (snapshot.hasError) {
          return ErrorView(error: snapshot.error!, onRetry: _reload);
        }
        final value = snapshot.data as T;
        return widget.onEmpty?.call(context, value) ??
            widget.builder(context, value);
      },
    );
  }
}

// ------------------------------------------------------------------- display

/// One status, painted with the shared palette. The label is the server's own
/// token, title-cased — the client never renames a state.
class StatusChip extends StatelessWidget {
  const StatusChip({super.key, required this.status, this.color});

  final String status;
  final Color? color;

  @override
  Widget build(BuildContext context) {
    final c = color ?? StatusPalette.forStatus(status);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
        color: c.withValues(alpha: .14),
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: c.withValues(alpha: .5)),
      ),
      child: Text(
        Format.titleCase(status),
        style: Theme.of(context).textTheme.labelMedium
            ?.copyWith(color: c, fontWeight: FontWeight.w600),
      ),
    );
  }
}

/// Money in one place, so no screen divides by 100 by hand.
class MoneyText extends StatelessWidget {
  const MoneyText(this.cents, this.currency, {super.key, this.compact = false});

  final int cents;
  final String currency;
  final bool compact;

  @override
  Widget build(BuildContext context) => Text(
    compact
        ? Format.moneyCompact(cents, currency)
        : Format.money(cents, currency),
  );
}

/// The rating line a card shows: "no ratings yet" rather than a fake zero.
class RatingLine extends StatelessWidget {
  const RatingLine({super.key, required this.avg, required this.count});

  final double? avg;
  final int count;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    if (avg == null || count == 0) {
      return Text(
        t(context, 'rating_none'),
        style: theme.textTheme.bodySmall?.copyWith(
          color: theme.colorScheme.onSurfaceVariant,
        ),
      );
    }
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(Icons.star_rounded, size: 16, color: Colors.amber.shade700),
        const SizedBox(width: 4),
        Text(avg!.toStringAsFixed(1), style: theme.textTheme.bodyMedium),
        Text(
          ' ($count)',
          style: theme.textTheme.bodySmall?.copyWith(
            color: theme.colorScheme.onSurfaceVariant,
          ),
        ),
      ],
    );
  }
}

/// A titled block, so detail screens stay scannable.
class SectionCard extends StatelessWidget {
  const SectionCard({
    super.key,
    required this.title,
    required this.child,
    this.trailing,
  });

  final String title;
  final Widget child;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(14, 12, 14, 14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    title,
                    style: Theme.of(context).textTheme.titleSmall,
                  ),
                ),
                ?trailing,
              ],
            ),
            const SizedBox(height: 10),
            child,
          ],
        ),
      ),
    );
  }
}

/// A labelled value row — the detail screens' answer to a table.
class InfoRow extends StatelessWidget {
  const InfoRow({super.key, required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 130,
            child: Text(
              label,
              style: theme.textTheme.bodySmall?.copyWith(
                color: theme.colorScheme.onSurfaceVariant,
              ),
            ),
          ),
          Expanded(
            child: Text(
              value,
              style: theme.textTheme.bodyMedium,
              textAlign: TextAlign.end,
            ),
          ),
        ],
      ),
    );
  }
}

/// A hold is a countdown. A hold that ran out silently is worse than one that
/// says so, so this ticks until it expires and then reports it once.
class HoldCountdown extends StatefulWidget {
  const HoldCountdown({super.key, required this.booking, this.onExpired});

  final Booking booking;
  final VoidCallback? onExpired;

  @override
  State<HoldCountdown> createState() => _HoldCountdownState();
}

class _HoldCountdownState extends State<HoldCountdown> {
  Timer? _timer;
  bool _fired = false;

  @override
  void initState() {
    super.initState();
    _timer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) setState(() {});
    });
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final left = widget.booking.holdRemaining();
    if (left == null) return const SizedBox.shrink();
    if (left <= Duration.zero) {
      if (!_fired) {
        _fired = true;
        WidgetsBinding.instance.addPostFrameCallback(
          (_) => widget.onExpired?.call(),
        );
      }
      return Text(
        t(context, 'hold_expired'),
        style: Theme.of(context).textTheme.bodySmall
            ?.copyWith(color: Theme.of(context).colorScheme.error),
      );
    }
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        const Icon(Icons.timer_outlined, size: 16),
        const SizedBox(width: 6),
        Text(
          '${t(context, 'hold_expires')} ${Format.countdown(left)}',
          style: Theme.of(context).textTheme.bodySmall,
        ),
      ],
    );
  }
}

// ------------------------------------------------------------------ feedback

/// Surface a server rejection in the server's own words.
void showError(BuildContext context, Object error) {
  final message = error is ApiException ? error.message : '$error';
  ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(SnackBar(content: Text(message)));
}

void showMessage(BuildContext context, String message) {
  ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(SnackBar(content: Text(message)));
}
