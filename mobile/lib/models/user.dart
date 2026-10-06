import 'json.dart';

/// `users` + roles + active org (CONTRACTS §5.1 / §3 JWT claims).
class User {
  const User({
    required this.id,
    required this.email,
    required this.fullName,
    this.phone,
    this.preferredLocale = 'en',
    this.roles = const ['customer'],
    this.activeOrgId,
    this.organization,
  });

  final String id;
  final String email;
  final String fullName;
  final String? phone;
  final String preferredLocale;
  final List<String> roles;
  final String? activeOrgId;
  final Organization? organization;

  bool get isProvider => roles.contains('provider');
  bool get isOrgAdmin => roles.contains('org_admin');
  bool get isPlatformAdmin => roles.contains('platform_admin');

  /// Provider-side entry points need provider or org_admin.
  bool get hasProviderSide => isProvider || isOrgAdmin;

  static User fromJson(Map<String, dynamic> json) {
    final roles = Json.strings(json, 'roles');
    Organization? org;
    final orgJson = json['organization'];
    if (orgJson is Map) {
      org = Organization.fromJson(Map<String, dynamic>.from(orgJson));
    }
    return User(
      id: Json.strOr(json, 'id'),
      email: Json.strOr(json, 'email'),
      fullName: Json.strOr(json, 'full_name'),
      phone: Json.str(json, 'phone'),
      preferredLocale: Json.strOr(json, 'preferred_locale', 'en'),
      roles: roles.isEmpty ? const ['customer'] : roles,
      activeOrgId: Json.str(json, 'active_org_id') ?? org?.id,
      organization: org,
    );
  }
}

class Organization {
  const Organization({
    required this.id,
    required this.name,
    this.slug = '',
    this.currency = 'USD',
    this.timezone = 'UTC',
    this.country,
  });

  final String id;
  final String name;
  final String slug;
  final String currency;
  final String timezone;
  final String? country;

  static Organization fromJson(Map<String, dynamic> json) => Organization(
    id: Json.strOr(json, 'id'),
    name: Json.strOr(json, 'name'),
    slug: Json.strOr(json, 'slug'),
    currency: Json.strOr(json, 'currency', 'USD'),
    timezone: Json.strOr(json, 'timezone', 'UTC'),
    country: Json.str(json, 'country'),
  );
}

class Category {
  const Category({required this.id, required this.key, required this.label});

  final String id;
  final String key;
  final String label;

  static Category fromJson(Map<String, dynamic> json) => Category(
    id: Json.strOr(json, 'id'),
    key: Json.strOr(json, 'key'),
    label: Json.strOr(json, 'label', Json.strOr(json, 'key')),
  );
}
