import 'package:flutter/material.dart';

/// Hand-rolled i18n: English + partial Farsi with RTL switch.
/// Untranslated keys fall back to English strings.
class AppLocalizations {
  const AppLocalizations(this.locale);

  final Locale locale;

  static const List<Locale> supported = [Locale('en'), Locale('fa')];

  static AppLocalizations of(BuildContext context) {
    return Localizations.of<AppLocalizations>(context, AppLocalizations) ??
        const AppLocalizations(Locale('en'));
  }

  bool get isFa => locale.languageCode == 'fa';

  static const Map<String, String> _en = {
    'app_name': 'Capacity Exchange',
    'tagline': 'Turn idle capacity into booked capacity',
    'tab_home': 'Home',
    'tab_discover': 'Discover',
    'tab_activity': 'Activity',
    'tab_messages': 'Messages',
    'tab_account': 'Account',
    'login': 'Log in',
    'register': 'Create account',
    'logout': 'Log out',
    'email': 'Email',
    'password': 'Password',
    'full_name': 'Full name',
    'phone': 'Phone (optional)',
    'i_am': 'I am a…',
    'role_customer': 'Customer',
    'role_provider': 'Provider (organization)',
    'org_name': 'Organization name',
    'have_account': 'Already have an account? Log in',
    'no_account': 'New here? Create an account',
    'search_offers': 'Search capacity offers',
    'filters': 'Filters',
    'sort': 'Sort',
    'sort_relevance': 'Relevance',
    'sort_price_asc': 'Price: low to high',
    'sort_price_desc': 'Price: high to low',
    'sort_newest': 'Newest',
    'sort_rating': 'Top rated',
    'city': 'City',
    'category': 'Category',
    'any_category': 'Any category',
    'date_from': 'From',
    'date_to': 'To',
    'min_quantity': 'Minimum quantity',
    'max_price': 'Max price / unit',
    'min_rating': 'Minimum rating',
    'booking_mode': 'Booking mode',
    'any_mode': 'Any',
    'mode_instant': 'Instant',
    'mode_request_confirm': 'Request & confirm',
    'apply': 'Apply',
    'reset': 'Reset',
    'close': 'Close',
    'retry': 'Retry',
    'load_more': 'Load more',
    'no_results': 'No offers match your search',
    'no_results_hint':
        'Try removing a filter or widening the date range.',
    'something_wrong': 'Something went wrong',
    'offline_hint': 'Check that the API server is running and reachable.',
    'book': 'Book',
    'details': 'Details',
    'availability': 'Availability',
    'free_windows': 'Free windows',
    'reviews': 'Reviews',
    'no_reviews': 'No reviews yet',
    'rating_none': 'No ratings yet',
    'provider': 'Provider',
    'quantity': 'Quantity',
    'total': 'Total',
    'window': 'Window',
    'place_hold': 'Place hold',
    'holding': 'Hold placed',
    'confirm_booking': 'Confirm',
    'cancel_booking': 'Cancel booking',
    'hold_expires': 'Hold expires in',
    'hold_expired': 'This hold has expired',
    'booking_confirmed': 'Booking confirmed',
    'awaiting_provider':
        'Awaiting provider confirmation. You will be notified.',
    'my_bookings': 'My bookings',
    'my_orders': 'My orders',
    'my_demands': 'My demands',
    'timeline': 'Status timeline',
    'cancel': 'Cancel',
    'accept': 'Accept',
    'decline': 'Decline',
    'start': 'Start',
    'complete': 'Complete',
    'mark_all_read': 'Mark all read',
    'notifications': 'Notifications',
    'no_notifications': 'No notifications yet',
    'no_messages': 'No conversations yet',
    'message_hint': 'Type a message…',
    'send': 'Send',
    'language': 'Language',
    'role': 'Role',
    'dashboard': 'Dashboard',
    'provider_actions': 'Provider quick actions',
    'open_demands': 'Open demands matched to you',
    'no_demands': 'No open demands right now',
    'active_bookings': 'Active bookings',
    'spend': 'Total spend',
    'unread': 'Unread',
    'recent_orders': 'Recent orders',
    'utilization': 'Utilization',
    'revenue': 'Revenue',
    'bookings_by_status': 'Bookings by status',
    'upcoming': 'Upcoming bookings',
    'top_offers': 'Top offers',
    'per_unit': 'per unit',
    'flat_price': 'flat price',
    'cancellation_policy': 'Cancellation policy',
    'min': 'min',
    'max': 'max',
    'all': 'All',
    'upcoming_tab': 'Upcoming',
    'past_tab': 'Past',
    'open': 'Open',
    'closed': 'Closed',
    'write_review': 'Write a review',
    'your_rating': 'Your rating',
    'comment': 'Comment (optional)',
    'submit': 'Submit',
    'server_says': 'Server says',
    'welcome': 'Welcome',
  };

  static const Map<String, String> _fa = {
    'app_name': 'صرافی ظرفیت',
    'tagline': 'ظرفیت بی‌استفاده را به ظرفیت رزورشده تبدیل کنید',
    'tab_home': 'خانه',
    'tab_discover': 'کاوش',
    'tab_activity': 'فعالیت‌ها',
    'tab_messages': 'پیام‌ها',
    'tab_account': 'حساب',
    'login': 'ورود',
    'register': 'ایجاد حساب',
    'logout': 'خروج',
    'email': 'ایمیل',
    'password': 'رمز عبور',
    'full_name': 'نام کامل',
    'i_am': 'من…',
    'role_customer': 'مشتری',
    'role_provider': 'ارائه‌دهنده (سازمان)',
    'org_name': 'نام سازمان',
    'search_offers': 'جستجوی ظرفیت',
    'filters': 'فیلترها',
    'sort': 'مرتب‌سازی',
    'city': 'شهر',
    'category': 'دسته',
    'apply': 'اعمال',
    'reset': 'بازنشانی',
    'close': 'بستن',
    'retry': 'تلاش مجدد',
    'no_results': 'هیچ پیشنهادی یافت نشد',
    'something_wrong': 'خطایی رخ داد',
    'book': 'رزرو',
    'details': 'جزئیات',
    'availability': 'دسترس‌پذیری',
    'reviews': 'نظرات',
    'quantity': 'تعداد',
    'total': 'مجموع',
    'place_hold': 'ثبت نگه‌داشتن',
    'confirm_booking': 'تأیید',
    'cancel_booking': 'لغو رزرو',
    'hold_expires': 'نگه‌داشتن منقضی می‌شود در',
    'hold_expired': 'این نگه‌داشتن منقضی شده است',
    'booking_confirmed': 'رزرو تأیید شد',
    'my_bookings': 'رزروهای من',
    'my_orders': 'سفارش‌های من',
    'timeline': 'وضعیت',
    'cancel': 'لغو',
    'accept': 'پذیرش',
    'decline': 'رد',
    'start': 'آغاز',
    'complete': 'اتمام',
    'notifications': 'اعلان‌ها',
    'language': 'زبان',
    'dashboard': 'داشبورد',
    'active_bookings': 'رزروهای فعال',
    'spend': 'مجموع هزینه',
    'recent_orders': 'سفارش‌های اخیر',
    'utilization': 'نرخ استفاده',
    'revenue': 'درآمد',
    'per_unit': 'به ازای هر واحد',
    'min': 'حداقل',
    'max': 'حداکثر',
    'all': 'همه',
    'submit': 'ارسال',
    'welcome': 'خوش آمدید',
  };

  String t(String key) => _fa[key] ?? _en[key] ?? key;
}

class AppLocalizationsDelegate
    extends LocalizationsDelegate<AppLocalizations> {
  const AppLocalizationsDelegate();

  @override
  bool isSupported(Locale locale) =>
      AppLocalizations.supported.any((l) => l.languageCode == locale.languageCode);

  @override
  Future<AppLocalizations> load(Locale locale) async =>
      AppLocalizations(locale);

  @override
  bool shouldReload(covariant AppLocalizationsDelegate old) => false;
}

/// Holds the selected locale; switching to fa flips the app to RTL via
/// MaterialApp's built-in directionality.
class LocaleController extends ChangeNotifier {
  Locale _locale = const Locale('en');

  Locale get locale => _locale;
  bool get isFa => _locale.languageCode == 'fa';

  void setLocale(Locale locale) {
    if (!AppLocalizations.supported.contains(locale)) return;
    if (_locale == locale) return;
    _locale = locale;
    notifyListeners();
  }

  void toggle() =>
      setLocale(isFa ? const Locale('en') : const Locale('fa'));
}
