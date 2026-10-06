import 'package:uuid/uuid.dart';

import '../api/api_client.dart';
import '../core/formatters.dart';
import '../models/activity.dart';
import '../models/booking.dart';
import '../models/capacity.dart';
import '../models/dashboard.dart';
import '../models/demand.dart';
import '../models/dispute.dart';
import '../models/offer.dart';
import '../models/order.dart';
import '../models/envelope.dart';
import '../models/query.dart';
import '../models/user.dart';

/// Typed reads and writes for the documented REST surface
/// (docs/CONTRACTS.md §8).
///
/// This is the only place the mobile app names an endpoint. It transports and
/// maps: no rule belongs here that the server also enforces, which is why
/// nothing validates a window, a price or a state transition — a rejected
/// request is the server's job and surfaces as an [ApiException] upstream.
///
/// Money stays in integer minor units and timestamps stay UTC end to end (§1).
class Repo {
  Repo(this.api);

  final ApiClient api;
  static const _uuid = Uuid();

  // ---------------------------------------------------------------- identity

  Future<User> me() async => User.fromJson(
    Map<String, dynamic>.from(await api.get('/auth/me') as Map),
  );

  Future<void> patchMe({String? fullName, String? phone, String? locale}) =>
      api.patch(
        '/auth/me',
        body: {
          'full_name': ?fullName,
          'phone': ?phone,
          'preferred_locale': ?locale,
        },
      );

  Future<List<Category>> categories() async {
    final json = await api.get(
      '/catalog/categories',
      query: {'active_only': true, 'limit': 100},
    );
    return ListEnvelope<Category>.fromJson(json, Category.fromJson).items;
  }

  Future<Organization> createOrganization(String name) async =>
      Organization.fromJson(
        Map<String, dynamic>.from(
          await api.post(
            '/organizations',
            body: {'name': name, 'slug': _slug(name)},
          ) as Map,
        ),
      );

  static String _slug(String name) => name
      .toLowerCase()
      .replaceAll(RegExp(r'[^a-z0-9]+'), '-')
      .replaceAll(RegExp(r'^-+|-+$'), '');

  // ---------------------------------------------------------------- discover

  Future<ListEnvelope<Offer>> searchOffers(OfferFilters filters) async {
    final json = await api.get('/offers', query: filters.toQuery());
    return ListEnvelope<Offer>.fromJson(json, Offer.fromJson);
  }

  Future<Offer> offer(String id) async => Offer.fromJson(
    Map<String, dynamic>.from(await api.get('/offers/$id') as Map),
  );

  /// Bookable windows already expanded by the capacity engine (§5.2). The
  /// route requires both bounds, and a wide range costs the server a scan, so
  /// the horizon is clamped to the fortnight a picker can actually show.
  Future<ListEnvelope<FreeWindow>> offerAvailability(
    String offerId, {
    required DateTime from,
    DateTime? to,
  }) async {
    final today = _day(DateTime.now());
    final start = _day(from).isBefore(today) ? today : _day(from);
    final horizon = start.add(const Duration(days: 14));
    final end = _day(to ?? horizon).isAfter(horizon)
        ? horizon
        : _day(to ?? horizon);
    final json = await api.get(
      '/offers/$offerId/availability',
      query: {'from': _date(start), 'to': _date(end)},
    );
    return ListEnvelope<FreeWindow>.fromJson(json, FreeWindow.fromJson);
  }

  Future<ListEnvelope<Review>> offerReviews(
    String offerId, {
    int limit = 20,
    int offset = 0,
  }) async {
    final json = await api.get(
      '/offers/$offerId/reviews',
      query: {'limit': limit, 'offset': offset},
    );
    return ListEnvelope<Review>.fromJson(json, Review.fromJson);
  }

  // ------------------------------------------------------------------ booking

  /// `POST /bookings/hold` — the reservation window that keeps capacity aside.
  /// A fresh `request_fingerprint` per attempt is what lets the server tell a
  /// retry of the same request from a genuinely new one (§5.7), so one is
  /// generated here instead of being passed in.
  Future<Booking> placeHold({
    required String offerId,
    required DateTime windowStart,
    required DateTime windowEnd,
    required int quantity,
    String? idempotencyKey,
  }) async {
    final json = await api.post(
      '/bookings/hold',
      headers: {'Idempotency-Key': idempotencyKey ?? _uuid.v4()},
      body: {
        'offer_id': offerId,
        'window_start': _iso(windowStart),
        'window_end': _iso(windowEnd),
        'quantity': quantity,
        'request_fingerprint': _uuid.v4(),
      },
    );
    return Booking.fromJson(Map<String, dynamic>.from(json as Map));
  }

  /// Turn a held window into a booking. For `request_confirm` offers the
  /// result is still a request the provider has to accept (§5.5).
  Future<Booking> createBookingFromHold(String holdId) async {
    final json = await api.post(
      '/bookings',
      headers: {'Idempotency-Key': _uuid.v4()},
      body: {'hold_id': holdId},
    );
    return Booking.fromJson(Map<String, dynamic>.from(json as Map));
  }

  Future<ListEnvelope<Booking>> bookings({
    bool mine = true,
    bool provider = false,
    String? status,
    int limit = 20,
    int offset = 0,
  }) async {
    final json = await api.get(
      '/bookings',
      query: {
        if (mine) 'mine': true,
        if (provider) 'provider': true,
        if (status != null && status.isNotEmpty) 'status': status,
        'limit': limit,
        'offset': offset,
      },
    );
    return ListEnvelope<Booking>.fromJson(json, Booking.fromJson);
  }

  Future<Booking> booking(String id) async => Booking.fromJson(
    Map<String, dynamic>.from(await api.get('/bookings/$id') as Map),
  );

  Future<ListEnvelope<BookingEvent>> bookingTimeline(
    String id, {
    int limit = 30,
  }) async {
    final json = await api.get(
      '/bookings/$id/timeline',
      query: {'limit': limit, 'offset': 0},
    );
    return ListEnvelope<BookingEvent>.fromJson(json, BookingEvent.fromJson);
  }

  /// The state machine lives on the server (§5.5); these verbs only forward
  /// the request and hand back the reloaded booking.
  Future<Booking> confirmBooking(String id) =>
      _bookingVerb('/bookings/$id/confirm');
  Future<Booking> startBooking(String id) =>
      _bookingVerb('/bookings/$id/start');
  Future<Booking> completeBooking(String id) =>
      _bookingVerb('/bookings/$id/complete');

  Future<Booking> cancelBooking(String id, String reason) async {
    final json = await api.post(
      '/bookings/$id/cancel',
      headers: {'Idempotency-Key': _uuid.v4()},
      body: {'reason': reason},
    );
    return Booking.fromJson(Map<String, dynamic>.from(json as Map));
  }

  Future<Booking> _bookingVerb(String path) async {
    final json = await api.post(path, headers: {'Idempotency-Key': _uuid.v4()});
    return Booking.fromJson(Map<String, dynamic>.from(json as Map));
  }

  // ------------------------------------------------------- orders & payments

  /// Raise the invoice for a booking. Idempotent per booking server-side, so
  /// replaying after a dropped response returns the same order.
  Future<Order> createOrder(String bookingId, {String? couponCode}) async {
    final json = await api.post(
      '/orders',
      headers: {'Idempotency-Key': _uuid.v4()},
      body: {'booking_id': bookingId, 'coupon_code': couponCode},
    );
    return Order.fromJson(Map<String, dynamic>.from(json as Map));
  }

  Future<ListEnvelope<Order>> orders({
    bool provider = false,
    String? paymentStatus,
    int limit = 20,
    int offset = 0,
  }) async {
    final json = await api.get(
      '/orders',
      query: {
        if (provider) 'provider': true,
        if (paymentStatus != null && paymentStatus.isNotEmpty)
          'payment_status': paymentStatus,
        'limit': limit,
        'offset': offset,
      },
    );
    return ListEnvelope<Order>.fromJson(json, Order.fromJson);
  }

  Future<Order> order(String id) async => Order.fromJson(
    Map<String, dynamic>.from(await api.get('/orders/$id') as Map),
  );

  Future<Payment> pay(Order order) async {
    final intent = await api.post(
      '/payments/intents',
      body: {'order_id': order.id, 'provider_key': 'mock'},
    );
    final pi = Payment.fromJson(Map<String, dynamic>.from(intent as Map));
    if (pi.isTerminal) return pi;
    final confirmed = await api.post(
      '/payments/${pi.id}/confirm',
      headers: {'Idempotency-Key': _uuid.v4()},
    );
    return Payment.fromJson(Map<String, dynamic>.from(confirmed as Map));
  }

  Future<ListEnvelope<Payment>> orderPayments(String orderId) async {
    final json = await api.get(
      '/orders/$orderId/payments',
      query: {'limit': 20},
    );
    return ListEnvelope<Payment>.fromJson(json, Payment.fromJson);
  }

  // ------------------------------------------------------- demands & matches

  Future<ListEnvelope<Demand>> myDemands({
    int limit = 20,
    int offset = 0,
  }) async {
    final json = await api.get(
      '/demands',
      query: {'mine': true, 'limit': limit, 'offset': offset},
    );
    return ListEnvelope<Demand>.fromJson(json, Demand.fromJson);
  }

  /// Open demands the matching engine scored against the caller's offers.
  Future<ListEnvelope<Demand>> inboundDemands({
    int limit = 20,
    int offset = 0,
  }) async {
    final json = await api.get(
      '/demands',
      query: {'provider': true, 'limit': limit, 'offset': offset},
    );
    return ListEnvelope<Demand>.fromJson(json, Demand.fromJson);
  }

  Future<Demand> demand(String id) async => Demand.fromJson(
    Map<String, dynamic>.from(await api.get('/demands/$id') as Map),
  );

  Future<Demand> postDemand(DemandDraft draft) async {
    final json = await api.post(
      '/demands',
      headers: {'Idempotency-Key': _uuid.v4()},
      body: draft.toJson(),
    );
    return Demand.fromJson(Map<String, dynamic>.from(json as Map));
  }

  /// A closed demand stops being scored against new offers; cancelling is the
  /// buyer's way of taking it down entirely (§5.3).
  Future<void> closeDemand(String id) => api.post('/demands/$id/close');
  Future<void> cancelDemand(String id) => api.post('/demands/$id/cancel');

  Future<ListEnvelope<Match>> demandMatches(
    String demandId, {
    int limit = 10,
  }) async {
    final json = await api.get(
      '/demands/$demandId/matches',
      query: {'limit': limit, 'offset': 0},
    );
    return ListEnvelope<Match>.fromJson(json, Match.fromJson);
  }

  Future<ListEnvelope<Match>> providerMatches({
    String? status,
    int limit = 20,
  }) async {
    final json = await api.get(
      '/matches',
      query: {
        'provider': true,
        if (status != null && status.isNotEmpty) 'status': status,
        'limit': limit,
        'offset': 0,
      },
    );
    return ListEnvelope<Match>.fromJson(json, Match.fromJson);
  }

  /// Accepting a proposal is what creates the draft booking the customer then
  /// confirms, so the answer belongs to the provider side (§5.3).
  Future<Match> acceptMatch(String id) => _matchVerb('/matches/$id/accept');
  Future<Match> declineMatch(String id) => _matchVerb('/matches/$id/reject');

  Future<Match> _matchVerb(String path) async {
    final json = await api.post(path);
    return Match.fromJson(Map<String, dynamic>.from(json as Map));
  }

  // --------------------------------------------------------- trust & support

  Future<Dispute> openDispute(DisputeDraft draft) async {
    final json = await api.post(
      '/disputes',
      headers: {'Idempotency-Key': _uuid.v4()},
      body: draft.toJson(),
    );
    return Dispute.fromJson(Map<String, dynamic>.from(json as Map));
  }

  Future<Review> postReview({
    required String bookingId,
    required int rating,
    String? comment,
  }) async {
    final json = await api.post(
      '/reviews',
      headers: {'Idempotency-Key': _uuid.v4()},
      body: {'booking_id': bookingId, 'rating': rating, 'comment': comment},
    );
    return Review.fromJson(Map<String, dynamic>.from(json as Map));
  }

  Future<ListEnvelope<Review>> reviewsWritten({int limit = 20}) async {
    final json = await api.get(
      '/reviews',
      query: {'limit': limit, 'offset': 0},
    );
    return ListEnvelope<Review>.fromJson(json, Review.fromJson);
  }

  // ------------------------------------------------------ inbox & messaging

  Future<ListEnvelope<NotificationItem>> notifications({
    bool unreadOnly = false,
    int limit = 30,
    int offset = 0,
  }) async {
    final json = await api.get(
      '/notifications',
      query: {if (unreadOnly) 'unread': true, 'limit': limit, 'offset': offset},
    );
    return ListEnvelope<NotificationItem>.fromJson(
      json,
      NotificationItem.fromJson,
    );
  }

  Future<int> unreadNotificationCount() async {
    final page = await notifications(unreadOnly: true, limit: 1);
    return page.total;
  }

  Future<void> markNotificationRead(String id) =>
      api.post('/notifications/$id/read');

  Future<void> markAllNotificationsRead() =>
      api.post('/notifications/read-all');

  Future<ListEnvelope<Conversation>> conversations({
    bool provider = false,
    int limit = 30,
    int offset = 0,
  }) async {
    final json = await api.get(
      '/conversations',
      query: {if (provider) 'provider': true, 'limit': limit, 'offset': offset},
    );
    return ListEnvelope<Conversation>.fromJson(json, Conversation.fromJson);
  }

  Future<Conversation> startBookingThread({
    required String bookingId,
    required String orgId,
    required String body,
  }) async {
    final json = await api.post(
      '/conversations',
      body: {
        'kind': 'booking',
        'ref_id': bookingId,
        'org_id': orgId,
        'initial_body': body,
      },
    );
    return Conversation.fromJson(Map<String, dynamic>.from(json as Map));
  }

  Future<ListEnvelope<Message>> messages(
    String conversationId, {
    int limit = 100,
  }) async {
    final json = await api.get(
      '/conversations/$conversationId/messages',
      query: {'limit': limit, 'offset': 0},
    );
    return ListEnvelope<Message>.fromJson(json, Message.fromJson);
  }

  Future<Message> sendMessage(String conversationId, String body) async {
    final json = await api.post(
      '/conversations/$conversationId/messages',
      body: {'body': body},
    );
    return Message.fromJson(Map<String, dynamic>.from(json as Map));
  }

  // ------------------------------------------------------------- dashboards

  Future<CustomerDashboard> customerDashboard() async =>
      CustomerDashboard.fromJson(
        Map<String, dynamic>.from(await api.get('/dashboard/customer') as Map),
      );

  Future<ProviderDashboard> providerDashboard({
    DateTime? from,
    DateTime? to,
  }) async {
    final now = DateTime.now().toUtc();
    final start = from ?? now.subtract(const Duration(days: 30));
    final end = to ?? now.add(const Duration(days: 30));
    final json = await api.get(
      '/dashboard/provider',
      query: {'from': _date(start), 'to': _date(end)},
    );
    return ProviderDashboard.fromJson(Map<String, dynamic>.from(json as Map));
  }

  // ------------------------------------------------------------ provider side

  Future<ListEnvelope<Resource>> myResources({int limit = 50}) async {
    final json = await api.get(
      '/capacities',
      query: {'limit': limit, 'offset': 0},
    );
    return ListEnvelope<Resource>.fromJson(json, Resource.fromJson);
  }

  Future<ListEnvelope<Offer>> providerOffers({
    String? status,
    int limit = 50,
  }) async {
    final json = await api.get(
      '/offers',
      query: {
        'mine': true,
        if (status != null && status.isNotEmpty) 'status': status,
        'limit': limit,
        'offset': 0,
      },
    );
    return ListEnvelope<Offer>.fromJson(json, Offer.fromJson);
  }

  Future<void> pauseOffer(String id) => api.post('/offers/$id/pause');
  Future<void> publishOffer(String id) => api.post('/offers/$id/publish');

  // ------------------------------------------------------------------ wiring

  static String _iso(DateTime dt) => Format.isoUtc(dt);

  static String _date(DateTime dt) => Format.dateParam(dt);

  static DateTime _day(DateTime dt) => DateTime(dt.year, dt.month, dt.day);
}
