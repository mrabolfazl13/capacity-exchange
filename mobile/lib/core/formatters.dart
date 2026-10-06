import 'package:intl/intl.dart';

/// Formatting helpers per docs/CONTRACTS.md §1: money is always integer minor
/// units + ISO currency; timestamps are ISO-8601 UTC. Clients never do
/// business math beyond display (totals come from the server).
class Format {
  const Format._();

  /// Money: integer minor units + ISO currency, never floats in APIs.
  /// Display example: `1,250.00 USD`.
  static String money(int cents, String currency, {String locale = 'en'}) {
    final value = cents / 100; // display division only, not sent anywhere
    final symbol = NumberFormat.currency(name: currency, locale: locale).symbol;
    final formatted = NumberFormat.currency(
      locale: locale,
      symbol: symbol.isEmpty ? '$currency ' : symbol,
      name: currency,
    ).format(value);
    return formatted;
  }

  /// Compact money for dense cards: `1.2k USD`.
  static String moneyCompact(int cents, String currency) {
    final value = cents / 100;
    if (value >= 10000) {
      return '${NumberFormat.compact().format(value)} $currency';
    }
    return money(cents, currency);
  }

  static String dateTime(DateTime dt, {String pattern = 'EEE d MMM, HH:mm'}) {
    final utc = dt.toLocal();
    return DateFormat(pattern).format(utc);
  }

  static String date(DateTime dt, {String pattern = 'EEE d MMM'}) =>
      DateFormat(pattern).format(dt);

  static String time(DateTime dt) => DateFormat('HH:mm').format(dt.toLocal());

  static String relative(DateTime dt) {
    final diff = DateTime.now().difference(dt.toLocal());
    if (diff.inSeconds.abs() < 60) return 'just now';
    if (diff.inMinutes.abs() < 60) {
      return '${diff.inMinutes.abs()}m ${diff.isNegative ? 'from now' : 'ago'}';
    }
    if (diff.inHours.abs() < 24) {
      return '${diff.inHours.abs()}h ${diff.isNegative ? 'from now' : 'ago'}';
    }
    if (diff.inDays.abs() < 7) {
      return '${diff.inDays.abs()}d ${diff.isNegative ? 'from now' : 'ago'}';
    }
    return date(dt);
  }

  /// Duration as m:ss for countdowns.
  static String countdown(Duration d) {
    final minutes = d.inMinutes.remainder(60);
    final seconds = d.inSeconds.remainder(60);
    if (d.inHours >= 1) {
      return '${d.inHours}h ${minutes.toString().padLeft(2, '0')}m';
    }
    return '${minutes.toString().padLeft(2, '0')}:${seconds.toString().padLeft(2, '0')}';
  }

  /// ISO-8601 UTC with Z for API payloads (never naive strings).
  static String isoUtc(DateTime dt) {
    final u = dt.toUtc();
    String two(int v) => v.toString().padLeft(2, '0');
    return '${u.year}-${two(u.month)}-${two(u.day)}T${two(u.hour)}:${two(u.minute)}:${two(u.second)}Z';
  }

  /// Query date param (YYYY-MM-DD, local date).
  static String dateParam(DateTime dt) {
    String two(int v) => v.toString().padLeft(2, '0');
    return '${dt.year}-${two(dt.month)}-${two(dt.day)}';
  }

  static const List<String> dayNames = [
    'Mon',
    'Tue',
    'Wed',
    'Thu',
    'Fri',
    'Sat',
    'Sun',
  ];

  /// Day-of-week per contracts: 0 = Monday .. 6 = Sunday.
  static String dowName(int dow) =>
      (dow >= 0 && dow <= 6) ? dayNames[dow] : '?';

  static String titleCase(String s) => s
      .split(RegExp(r'[_\s]+'))
      .where((p) => p.isNotEmpty)
      .map((p) => '${p[0].toUpperCase()}${p.substring(1)}')
      .join(' ');
}
