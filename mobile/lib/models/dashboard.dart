import 'booking.dart';
import 'json.dart';
import 'offer.dart';
import 'order.dart';

/// `GET /dashboard/customer` (CONTRACTS §8): active bookings, spend,
/// unread notifications, recent orders. All numbers come from the server —
/// the client never recomputes aggregates.
class CustomerDashboard {
  const CustomerDashboard({
    required this.activeBookings,
    required this.spendCents,
    required this.currency,
    required this.unreadNotifications,
    required this.recentOrders,
    required this.recentBookings,
  });

  final int activeBookings;
  final int spendCents;
  final String currency;
  final int unreadNotifications;
  final List<Order> recentOrders;
  final List<Booking> recentBookings;

  static CustomerDashboard empty() => const CustomerDashboard(
        activeBookings: 0,
        spendCents: 0,
        currency: 'USD',
        unreadNotifications: 0,
        recentOrders: [],
        recentBookings: [],
      );

  static CustomerDashboard fromJson(Map<String, dynamic> json) => CustomerDashboard(
        activeBookings: Json.intOr(json, 'active_bookings'),
        spendCents: Json.intOr(json, 'spend_cents') + Json.intOr(json, 'total_spend_cents'),
        currency: Json.strOr(json, 'currency', 'USD'),
        unreadNotifications: Json.intOr(json, 'unread_notifications'),
        recentOrders:
            Json.list(json, 'recent_orders').map(Order.fromJson).toList(growable: false),
        recentBookings:
            Json.list(json, 'recent_bookings')
                .map(Booking.fromJson)
                .toList(growable: false),
      );
}

/// `GET /dashboard/provider?from&to`: utilization, bookings_by_status,
/// revenue_cents, upcoming_bookings, top_offers.
class ProviderDashboard {
  const ProviderDashboard({
    required this.utilization,
    this.isPercent = false,
    required this.bookingsByStatus,
    required this.revenueCents,
    required this.currency,
    required this.upcomingBookings,
    required this.topOffers,
  });

  /// 0..1 ratio when the server sends a fraction, otherwise a percent; the
  /// view renders whatever scale arrives (`ProviderDashboard.isPercent`).
  final double utilization;
  final bool isPercent;
  final Map<String, int> bookingsByStatus;
  final int revenueCents;
  final String currency;
  final List<Booking> upcomingBookings;
  final List<Offer> topOffers;

  static ProviderDashboard empty() => const ProviderDashboard(
        utilization: 0,
        isPercent: false,
        bookingsByStatus: {},
        revenueCents: 0,
        currency: 'USD',
        upcomingBookings: [],
        topOffers: [],
      );

  static ProviderDashboard fromJson(Map<String, dynamic> json) {
    final byStatus = <String, int>{};
    final rawStatus = json['bookings_by_status'];
    if (rawStatus is Map) {
      rawStatus.forEach((k, v) {
        if (v is int) {
          byStatus['$k'] = v;
        } else if (v is num) {
          byStatus['$k'] = v.round();
        } else {
          byStatus['$k'] = int.tryParse('$v') ?? 0;
        }
      });
    } else if (rawStatus is List) {
      for (final e in rawStatus.whereType<Map>()) {
        final m = Map<String, dynamic>.from(e);
        final key = Json.strOr(m, 'status');
        if (key.isNotEmpty) byStatus[key] = Json.intOr(m, 'count');
      }
    }
    final util = Json.doubleOrNull(json, 'utilization') ??
        Json.doubleOrNull(json, 'utilization_pct');
    return ProviderDashboard(
      utilization: util ?? 0,
      isPercent: json['utilization_pct'] != null,
      bookingsByStatus: byStatus,
      revenueCents: Json.intOr(json, 'revenue_cents'),
      currency: Json.strOr(json, 'currency', 'USD'),
      upcomingBookings:
          Json.list(json, 'upcoming_bookings').map(Booking.fromJson).toList(growable: false),
      topOffers: Json.list(json, 'top_offers').map(Offer.fromJson).toList(growable: false),
    );
  }
}
