// Persian catalog (partial). Missing keys fall back to English at runtime.

import type { MessageKey } from '@/i18n/index';

const fa: Partial<Record<MessageKey, string>> = {
  'app.name': 'صرافی ظرفیت',
  'app.tagline': 'ظرفیت بلااستفاده را به موجودی تبدیل کنید',

  'nav.marketplace': 'بازار',
  'nav.bookings': 'رزروهای من',
  'nav.demands': 'درخواست‌های من',
  'nav.messages': 'پیام‌ها',
  'nav.notifications': 'اعلان‌ها',
  'nav.dashboard': 'داشبورد',
  'nav.provider': 'فضای ارائه‌دهنده',
  'nav.admin': 'مدیریت',
  'nav.logout': 'خروج',
  'nav.language': 'زبان',

  'common.search': 'جستجو',
  'common.cancel': 'لغو',
  'common.save': 'ذخیره',
  'common.confirm': 'تأیید',
  'common.back': 'بازگشت',
  'common.next': 'بعدی',
  'common.loading': 'در حال بارگذاری…',
  'common.retry': 'تلاش دوباره',
  'common.status': 'وضعیت',
  'common.actions': 'عملیات',
  'common.quantity': 'تعداد',
  'common.price': 'قیمت',

  'auth.login': 'ورود',
  'auth.register': 'ایجاد حساب',
  'auth.email': 'ایمیل',
  'auth.password': 'رمز عبور',
  'auth.fullName': 'نام کامل',

  'market.title': 'بازار',
  'market.q': 'کلیدواژه',
  'market.category': 'دسته',
  'market.city': 'شهر',
  'market.country': 'کشور',

  'book.title': 'رزرو ظرفیت',
  'book.cancel': 'لغو رزرو',
  'book.myBookings': 'رزروهای من',

  'err.unauthorized': 'لطفاً وارد شوید.',
  'err.forbidden': 'مجوز انجام این کار را ندارید.',
  'err.not_found': 'پیدا نشد.',
  'err.network_error': 'امکان اتصال به سرور وجود ندارد.',
  'err.unknown': 'خطایی رخ داد.',
};

export default fa;
