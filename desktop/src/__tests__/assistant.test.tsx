// The assistant is advisory, so its tests are about honesty rather than maths:
// a parsed sentence may only write the filters it actually read, a listing draft may
// only merge into boxes the provider left empty, and a briefing panel must show the
// money figure with the axis it was measured on (§8).

import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import {
  fillFromListingDraft,
  mappingFromParsedQuery,
  sortGaps,
} from '@/features/assistant/logic';
import { CopilotCard, QuietHoursCard } from '@/features/assistant/components/AssistantCards';
import { I18nProvider } from '@/i18n/index';
import type {
  CapacityCategory,
  CopilotBriefing,
  ListingDraft,
  ParsedQuery,
  UtilizationInsights,
} from '@/types/api';

function parsedQuery(overrides: Partial<ParsedQuery> = {}): ParsedQuery {
  return {
    raw_text: '',
    category_key: null,
    category_id: null,
    category_confidence: 0,
    city: null,
    country: null,
    quantity: null,
    unit: null,
    window_start: null,
    window_end: null,
    budget_min_cents: null,
    budget_max_cents: null,
    currency: null,
    constraints: [],
    confidence: 0,
    unparsed_fragments: [],
    ...overrides,
  };
}

function listingDraft(overrides: Partial<ListingDraft> = {}): ListingDraft {
  return {
    title: '',
    description: '',
    category_key: null,
    category_id: null,
    category_confidence: 0,
    attributes: {},
    suggested_availabilities: [],
    missing_fields: [],
    unit_label: null,
    suggested_unit_amount_cents: null,
    currency: null,
    ...overrides,
  };
}

function briefing(overrides: Partial<CopilotBriefing> = {}): CopilotBriefing {
  return {
    summary_text: 'Busiest resource: Court 3 at 71.4%.',
    next_7d_bookings: 4,
    at_risk_holds: [],
    top_idle_capacity: [],
    revenue_last_30d_cents: 123456,
    currency: 'USD',
    truncated: false,
    ...overrides,
  };
}

function insights(overrides: Partial<UtilizationInsights> = {}): UtilizationInsights {
  return {
    items: [],
    total: 0,
    idle_windows: [],
    demand_counts: [],
    truncated: false,
    window: { from: '2026-09-01T00:00:00Z', to: '2026-10-01T00:00:00Z' },
    ...overrides,
  };
}

const CATEGORIES: CapacityCategory[] = [
  { id: 'c-salon', key: 'salon', label: 'Salon', parent_id: null, is_active: true, attributes_schema: null },
  { id: 'c-cold', key: 'cold_storage', label: 'Cold storage', parent_id: null, is_active: false, attributes_schema: null },
];

function renderCard(ui: React.ReactElement) {
  return render(
    <I18nProvider>
      <MemoryRouter>{ui}</MemoryRouter>
    </I18nProvider>,
  );
}

describe('mappingFromParsedQuery', () => {
  it('writes only the filters the sentence actually carried', () => {
    const { filters, applied } = mappingFromParsedQuery(
      parsedQuery({ city: 'Berlin', quantity: 12, budget_max_cents: 20000 }),
      '',
    );
    expect(applied).toBe(true);
    expect(filters).toEqual({
      category_id: '',
      city: 'Berlin',
      country: '',
      from: '',
      to: '',
      min_quantity: '12',
      // The box the form displays in major units; the page converts on submit (§1).
      max_unit_cents: '200.00',
    });
  });

  it('turns a service-time window into calendar dates for the date boxes', () => {
    const { filters } = mappingFromParsedQuery(
      parsedQuery({
        window_start: '2026-10-09T13:00:00Z',
        window_end: '2026-10-09T17:00:00Z',
      }),
      '',
    );
    expect(filters.from).toBe('2026-10-09');
    expect(filters.to).toBe('2026-10-09');
  });

  it('keeps the buyer’s own keyword, and only falls back to the words it could not read', () => {
    expect(mappingFromParsedQuery(parsedQuery({ city: 'Riga' }), 'photographer').q).toBe(
      'photographer',
    );
    expect(
      mappingFromParsedQuery(
        parsedQuery({ city: 'Riga', unparsed_fragments: ['with a forklift'] }),
        '',
      ).q,
    ).toBe('with a forklift');
  });

  it('says it read nothing when the server resolved no filter at all', () => {
    const mapping = mappingFromParsedQuery(parsedQuery({ confidence: 0.2 }), 'hall');
    expect(mapping.applied).toBe(false);
    expect(Object.values(mapping.filters).every((v) => v === '')).toBe(true);
  });

  it('does not invent a category the catalog did not resolve', () => {
    expect(mappingFromParsedQuery(parsedQuery({ category_key: 'hall' }), '').filters.category_id).toBe('');
    expect(
      mappingFromParsedQuery(parsedQuery({ category_key: 'hall', category_id: 'c-salon' }), '')
        .filters.category_id,
    ).toBe('c-salon');
  });
});

describe('fillFromListingDraft', () => {
  it('preselects only a category the wizard can actually offer', () => {
    expect(fillFromListingDraft(listingDraft({ category_id: 'c-salon' }), CATEGORIES).categoryId).toBe(
      'c-salon',
    );
    expect(
      fillFromListingDraft(listingDraft({ category_key: 'cold_storage', category_id: 'c-cold' }), CATEGORIES)
        .categoryId,
    ).toBe('');
    expect(fillFromListingDraft(listingDraft({ category_id: 'nope' }), CATEGORIES).categoryId).toBe('');
  });

  it('takes the busiest suggested day as the unit quantity, and prices in major units', () => {
    const fill = fillFromListingDraft(
      listingDraft({
        title: '12-person Meeting room',
        description: 'Downtown room with a projector.',
        unit_label: 'hour',
        suggested_unit_amount_cents: 6000,
        currency: 'EUR',
        suggested_availabilities: [
          { dow: 0, start_time: '09:00', end_time: '18:00', quantity: 12, source_phrase: 'twelve seats' },
          { dow: 1, start_time: '09:00', end_time: '14:00', quantity: 6, source_phrase: null },
        ],
      }),
      CATEGORIES,
    );
    expect(fill.quantity).toBe('12');
    expect(fill.price).toBe('60.00');
    expect(fill.currency).toBe('EUR');
    expect(fill.unitLabel).toBe('hour');
    expect(fill.rules).toHaveLength(2);
    expect(fill.rules[0]).toEqual({ dow: '0', start_time: '09:00', end_time: '18:00', quantity: '12' });
  });

  it('carries the boxes it could not fill, in the order the wizard meets them', () => {
    const fill = fillFromListingDraft(
      listingDraft({ missing_fields: ['location', 'unit_amount_cents'] }),
      CATEGORIES,
    );
    expect(sortGaps(fill.gaps)).toEqual(['unit_amount_cents', 'location']);
  });
});

describe('CopilotCard', () => {
  it('shows the briefing and labels the money figure as collected', () => {
    renderCard(<CopilotCard briefing={briefing()} />);
    expect(screen.getByText('Busiest resource: Court 3 at 71.4%.')).toBeInTheDocument();
    expect(screen.getByText(/Collected in the last 30 days/)).toHaveTextContent('$1,234.56');
    expect(screen.getByText('Suggestion')).toBeInTheDocument();
  });

  it('lists an expiring hold as a row the provider can open', () => {
    renderCard(
      <CopilotCard
        briefing={
          briefing({
            at_risk_holds: [
              {
                booking_id: 'b-1',
                offer_title: 'Court 3, evening',
                hold_expires_at: '2026-10-07T18:30:00Z',
                window_start: '2026-10-09T17:00:00Z',
              },
            ],
          })
        }
      />,
    );
    expect(screen.getByText('Holds about to expire')).toBeInTheDocument();
    expect(screen.getByText('Court 3, evening')).toBeInTheDocument();
    expect(screen.getByRole('link')).toHaveAttribute('href', '/bookings/b-1');
  });

  it('admits when the window held more rows than it shows', () => {
    renderCard(<CopilotCard briefing={briefing({ truncated: true })} />);
    expect(screen.getByText(/More rows fit the window/)).toBeInTheDocument();
  });
});

describe('QuietHoursCard', () => {
  it('names the weekday from the catalog and reports how often it was offered', () => {
    renderCard(
      <QuietHoursCard
        insights={
          insights({
            idle_windows: [
              {
                definition_id: 'd-1',
                dow: 4,
                start_time: '08:00',
                end_time: '11:00',
                occurrences: 3,
                note: 'Friday morning sat empty.',
              },
            ],
          })
        }
      />,
    );
    expect(screen.getByText('Friday 08:00–11:00')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
    expect(screen.getByText('Friday morning sat empty.')).toBeInTheDocument();
  });

  it('says there is no pattern yet rather than showing an empty table', () => {
    renderCard(<QuietHoursCard insights={insights()} />);
    expect(screen.getByText(/No empty-hour pattern/)).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });
});
