// The catalog is the contract between a pure domain rule and the words a user reads.
// These three properties are what make that safe: every key resolves, parameters land in the
// sentence, and a Persian screen flips direction instead of relying on a stylesheet.

import { describe, expect, it } from 'vitest';
import { dirFor, translate, type MessageKey } from '@/i18n/index';
import en from '@/i18n/locales/en';
import fa from '@/i18n/locales/fa';
import { PROVIDER_BOARD_COLUMNS, validateHoldRequest } from '@/features/booking/logic';

describe('translate', () => {
  it('interpolates the parameters a rule carries', () => {
    expect(translate('en', 'book.check.minQuantity', { min: 4 })).toBe(
      'This listing books a minimum of 4.',
    );
    expect(translate('en', 'book.refundPct', { pct: 80, hours: 48 })).toContain('80%');
  });

  it('leaves an unmatched placeholder intact rather than writing "undefined"', () => {
    expect(translate('en', 'book.check.minQuantity', {})).toBe('This listing books a minimum of {min}.');
  });

  it('falls back to English for a key the Persian catalog has not translated yet', () => {
    const untranslated = (Object.keys(en) as MessageKey[]).find((k) => !(k in fa));
    expect(untranslated).toBeDefined();
    expect(translate('fa', untranslated as MessageKey)).toBe(en[untranslated as MessageKey]);
  });

  it('keeps every Persian entry pointing at a key the English catalog declares', () => {
    const orphan = Object.keys(fa).find((k) => !(k in en));
    expect(orphan).toBeUndefined();
  });
});

describe('dirFor', () => {
  it('renders Persian right-to-left', () => {
    expect(dirFor('fa')).toBe('rtl');
    expect(dirFor('en')).toBe('ltr');
  });
});

describe('catalog coverage of the domain rules', () => {
  it('resolves every hold-check key the validator can return', () => {
    const future = (h: number) => new Date(Date.now() + h * 3_600_000).toISOString();
    const offer = { min_quantity: 2, max_quantity: 8, min_duration_minutes: 60, max_duration_minutes: 240 };
    const issues = [
      ...validateHoldRequest(offer, 'nonsense', future(3), 2),
      ...validateHoldRequest(offer, future(3), future(2), 2),
      ...validateHoldRequest(offer, future(1), future(3), 9),
      ...validateHoldRequest(offer, future(2), future(20), 2),
    ];
    expect(issues.length).toBeGreaterThan(0);
    for (const issue of issues) {
      const text = translate('en', issue.key, issue.params);
      expect(text).not.toBe(issue.key);
      expect(text).not.toMatch(/\{\w+\}/);
    }
  });

  it('names the provider board columns with keys that exist', () => {
    for (const column of PROVIDER_BOARD_COLUMNS) {
      expect(translate('en', column.title)).not.toBe(column.title);
    }
  });
});
