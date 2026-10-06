import '../core/formatters.dart';

/// Search state for `GET /offers` — a 1:1 mapping onto the query params
/// documented in docs/CONTRACTS.md §8. No filtering happens on the client:
/// the map below is the only thing sent to the server.
class OfferFilters {
  const OfferFilters({
    this.q,
    this.categoryId,
    this.city,
    this.country,
    this.from,
    this.to,
    this.minQuantity,
    this.maxUnitCents,
    this.minRating,
    this.bookingMode,
    this.sort = 'relevance',
    this.limit = 20,
    this.offset = 0,
  });

  /// full-text query
  final String? q;
  final String? categoryId;
  final String? city;
  final String? country;
  final DateTime? from;
  final DateTime? to;
  final int? minQuantity;

  /// `max_unit_cents` — money stays in minor units end to end (§1)
  final int? maxUnitCents;
  final double? minRating;

  /// request_confirm | instant
  final String? bookingMode;

  /// relevance | price_asc | price_desc | newest | rating
  final String sort;
  final int limit;
  final int offset;

  static const List<String> sortOptions = [
    'relevance',
    'price_asc',
    'price_desc',
    'newest',
    'rating',
  ];

  int get activeFilterCount => [
    q,
    categoryId,
    city,
    country,
    from,
    to,
    minQuantity,
    maxUnitCents,
    minRating,
    bookingMode,
  ].where((v) => v != null && '$v'.isNotEmpty).length;

  bool get hasActiveFilters => activeFilterCount > 0;

  OfferFilters copyWith({
    Object? q = _unset,
    Object? categoryId = _unset,
    Object? city = _unset,
    Object? country = _unset,
    Object? from = _unset,
    Object? to = _unset,
    Object? minQuantity = _unset,
    Object? maxUnitCents = _unset,
    Object? minRating = _unset,
    Object? bookingMode = _unset,
    String? sort,
    int? limit,
    int? offset,
  }) {
    return OfferFilters(
      q: identical(q, _unset) ? this.q : q as String?,
      categoryId: identical(categoryId, _unset)
          ? this.categoryId
          : categoryId as String?,
      city: identical(city, _unset) ? this.city : city as String?,
      country: identical(country, _unset) ? this.country : country as String?,
      from: identical(from, _unset) ? this.from : from as DateTime?,
      to: identical(to, _unset) ? this.to : to as DateTime?,
      minQuantity: identical(minQuantity, _unset)
          ? this.minQuantity
          : minQuantity as int?,
      maxUnitCents: identical(maxUnitCents, _unset)
          ? this.maxUnitCents
          : maxUnitCents as int?,
      minRating: identical(minRating, _unset)
          ? this.minRating
          : minRating as double?,
      bookingMode: identical(bookingMode, _unset)
          ? this.bookingMode
          : bookingMode as String?,
      sort: sort ?? this.sort,
      limit: limit ?? this.limit,
      offset: offset ?? this.offset,
    );
  }

  /// Reset filters but keep the text query and paging shape.
  OfferFilters cleared() => OfferFilters(q: q, sort: sort, limit: limit);

  /// Exact wire payload for `GET /offers`.
  Map<String, dynamic> toQuery() {
    return {
      if (q != null && q!.trim().isNotEmpty) 'q': q!.trim(),
      if (categoryId != null && categoryId!.isNotEmpty)
        'category_id': categoryId,
      if (city != null && city!.trim().isNotEmpty) 'city': city!.trim(),
      if (country != null && country!.trim().isNotEmpty)
        'country': country!.trim(),
      if (from != null) 'from': Format.dateParam(from!),
      if (to != null) 'to': Format.dateParam(to!),
      if (minQuantity != null && minQuantity! > 0) 'min_quantity': minQuantity,
      if (maxUnitCents != null && maxUnitCents! > 0)
        'max_unit_cents': maxUnitCents,
      if (minRating != null && minRating! > 0) 'min_rating': minRating,
      if (bookingMode != null && bookingMode!.isNotEmpty)
        'booking_mode': bookingMode,
      'sort': sort,
      'limit': limit,
      'offset': offset,
    };
  }

  static const Object _unset = Object();
}

/// Draft for `POST /demands` (§5.3). Validation hints only; the server owns
/// constraints.
class DemandDraft {
  const DemandDraft({
    required this.description,
    this.categoryId,
    this.city,
    this.desiredStart,
    this.desiredEnd,
    this.quantity = 1,
    this.budgetMinCents,
    this.budgetMaxCents,
  });

  final String description;
  final String? categoryId;
  final String? city;
  final DateTime? desiredStart;
  final DateTime? desiredEnd;
  final int quantity;
  final int? budgetMinCents;
  final int? budgetMaxCents;

  Map<String, dynamic> toJson() => {
    'description': description.trim(),
    if (categoryId != null && categoryId!.isNotEmpty) 'category_id': categoryId,
    if (city != null && city!.trim().isNotEmpty)
      'address': {'city': city!.trim()},
    if (desiredStart != null) 'desired_start': Format.isoUtc(desiredStart!),
    if (desiredEnd != null) 'desired_end': Format.isoUtc(desiredEnd!),
    'quantity': quantity,
    if (budgetMinCents != null) 'budget_min_cents': budgetMinCents,
    if (budgetMaxCents != null) 'budget_max_cents': budgetMaxCents,
  };
}

/// Draft for `POST /disputes` (§5.7): kind is one of
/// quality | no_show | payment | damage | other.
class DisputeDraft {
  const DisputeDraft({
    required this.bookingId,
    required this.kind,
    required this.description,
  });

  final String bookingId;
  final String kind;
  final String description;

  Map<String, dynamic> toJson() => {
    'booking_id': bookingId,
    'kind': kind,
    'description': description.trim(),
  };
}
