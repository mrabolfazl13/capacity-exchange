import 'package:flutter/material.dart';

/// Marketplace brand theme (docs/UI_UX_SPEC.md: modern, polished, clear
/// hierarchy, information-dense without crowding).
class AppTheme {
  const AppTheme._();

  static const Color seed = Color(0xFF0B5FFF);

  static ThemeData light() {
    final scheme = ColorScheme.fromSeed(seedColor: seed);
    final base = ThemeData(
      useMaterial3: true,
      colorScheme: scheme,
      visualDensity: VisualDensity.adaptivePlatformDensity,
    );
    return base.copyWith(
      appBarTheme: AppBarTheme(
        centerTitle: false,
        backgroundColor: base.colorScheme.surface,
        foregroundColor: base.colorScheme.onSurface,
        elevation: 0,
        scrolledUnderElevation: 1,
        titleTextStyle: base.textTheme.titleLarge,
      ),
      cardTheme: CardThemeData(
        clipBehavior: Clip.antiAlias,
        elevation: 0,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(14),
          side: BorderSide(color: base.colorScheme.outlineVariant),
        ),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          minimumSize: const Size(48, 44),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(10),
          ),
        ),
      ),
      inputDecorationTheme: InputDecorationTheme(
        border: OutlineInputBorder(borderRadius: BorderRadius.circular(10)),
        isDense: true,
        contentPadding: const EdgeInsets.symmetric(
          horizontal: 12,
          vertical: 12,
        ),
      ),
      listTileTheme: const ListTileThemeData(
        contentPadding: EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      ),
      snackBarTheme: const SnackBarThemeData(
        behavior: SnackBarBehavior.floating,
      ),
    );
  }

  static ThemeData dark() {
    final scheme = ColorScheme.fromSeed(
      seedColor: seed,
      brightness: Brightness.dark,
    );
    final base = ThemeData(useMaterial3: true, colorScheme: scheme);
    return base.copyWith(
      appBarTheme: AppBarTheme(
        centerTitle: false,
        backgroundColor: base.colorScheme.surface,
        scrolledUnderElevation: 1,
      ),
      cardTheme: CardThemeData(
        clipBehavior: Clip.antiAlias,
        elevation: 0,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(14),
          side: BorderSide(color: base.colorScheme.outlineVariant),
        ),
      ),
      inputDecorationTheme: InputDecorationTheme(
        border: OutlineInputBorder(borderRadius: BorderRadius.circular(10)),
        isDense: true,
      ),
      snackBarTheme: const SnackBarThemeData(
        behavior: SnackBarBehavior.floating,
      ),
    );
  }
}

/// Status colors for booking/offer states (rendering only — the server is the
/// single source of truth for transitions).
class StatusPalette {
  const StatusPalette._();

  static Color forStatus(String status) {
    return switch (status) {
      'draft' => Colors.blueGrey,
      'hold' => Colors.amber.shade700,
      'confirmed' => Colors.green,
      'in_progress' => Colors.teal,
      'completed' => Colors.blue,
      'cancelled' => Colors.red,
      'expired' => Colors.grey,
      'disputed' => Colors.deepPurple,
      'published' => Colors.green,
      'paused' => Colors.amber.shade700,
      'closed' => Colors.grey,
      'open' => Colors.green,
      'matched' => Colors.blue,
      'paid' => Colors.green,
      'unpaid' => Colors.orange,
      'refunded' => Colors.blueGrey,
      _ => Colors.blueGrey,
    };
  }
}
