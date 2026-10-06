// Minimal hand-rolled i18n layer (no extra deps).
// - Catalogs: en (complete), fa (partial; missing keys fall back to en).
// - Direction switch: fa renders rtl via document.documentElement.dir.
// - t(key, params) supports {param} interpolation.

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import en from '@/i18n/locales/en';
import fa from '@/i18n/locales/fa';

export type Locale = 'en' | 'fa';
export type MessageKey = keyof typeof en;
type Catalog = Partial<Record<MessageKey, string>>;

const catalogs: Record<Locale, Catalog> = { en, fa };

export const LOCALES: { value: Locale; label: string; dir: 'ltr' | 'rtl' }[] = [
  { value: 'en', label: 'English', dir: 'ltr' },
  { value: 'fa', label: 'فارسی', dir: 'rtl' },
];

const LOCALE_STORAGE_KEY = 'cx.locale';

export function dirFor(locale: Locale): 'ltr' | 'rtl' {
  return locale === 'fa' ? 'rtl' : 'ltr';
}

export interface TranslateFn {
  (key: MessageKey, params?: Record<string, string | number>): string;
}

interface I18nValue {
  locale: Locale;
  dir: 'ltr' | 'rtl';
  setLocale: (l: Locale) => void;
  t: TranslateFn;
}

const I18nContext = createContext<I18nValue | null>(null);

export function translate(
  locale: Locale,
  key: MessageKey,
  params?: Record<string, string | number>,
): string {
  const raw = catalogs[locale][key] ?? en[key] ?? String(key);
  if (!params) return raw;
  return raw.replace(/\{(\w+)\}/g, (_m, p: string) =>
    params[p] !== undefined ? String(params[p]) : `{${p}}`,
  );
}

function initialLocale(): Locale {
  try {
    const stored = localStorage.getItem(LOCALE_STORAGE_KEY);
    if (stored === 'en' || stored === 'fa') return stored;
  } catch {
    /* noop */
  }
  return 'en';
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(initialLocale);
  const dir = dirFor(locale);

  const setLocale = useCallback((l: Locale) => {
    setLocaleState(l);
    try {
      localStorage.setItem(LOCALE_STORAGE_KEY, l);
    } catch {
      /* noop */
    }
  }, []);

  const t = useCallback<TranslateFn>(
    (key, params) => translate(locale, key, params),
    [locale],
  );

  const value = useMemo<I18nValue>(
    () => ({ locale, dir, setLocale, t }),
    [locale, dir, setLocale, t],
  );

  return (
    <I18nContext.Provider value={value}>
      <div dir={dir} className="dir-root">
        {children}
      </div>
    </I18nContext.Provider>
  );
}

export function useI18n(): I18nValue {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error('useI18n must be used inside I18nProvider');
  return ctx;
}
