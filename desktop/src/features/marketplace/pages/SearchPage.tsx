import { useMemo, useState, type FormEvent } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useI18n } from '@/i18n/index';
import { useCategories, useOfferSearch } from '@/features/marketplace/hooks';
import { OfferCard } from '@/features/marketplace/components/OfferCard';
import { InputField, SelectField } from '@/components/ui/Field';
import { Button } from '@/components/ui/Button';
import { EmptyState, ErrorPanel, SkeletonCard } from '@/components/ui/States';
import { Pagination } from '@/components/ui/Pagination';
import { majorToCents } from '@/lib/format';
import { offerSearchQuery } from '@/api/endpoints';
import type { OfferSearchParams } from '@/types/api';

const PAGE_SIZE = 20;

export function SearchPage() {
  const { t } = useI18n();
  const [searchParams, setSearchParams] = useSearchParams();
  const categoriesQuery = useCategories();

  const [draft, setDraft] = useState(() => ({
    q: searchParams.get('q') ?? '',
    category_id: searchParams.get('category_id') ?? '',
    city: searchParams.get('city') ?? '',
    country: searchParams.get('country') ?? '',
    from: searchParams.get('from') ?? '',
    to: searchParams.get('to') ?? '',
    min_quantity: searchParams.get('min_quantity') ?? '',
    max_unit_cents: searchParams.get('max_unit_cents') ?? '',
    booking_mode: searchParams.get('booking_mode') ?? '',
    sort: searchParams.get('sort') ?? 'relevance',
  }));

  const params: OfferSearchParams = useMemo(
    () => ({
      q: draft.q || undefined,
      category_id: draft.category_id || undefined,
      city: draft.city || undefined,
      country: draft.country || undefined,
      from: draft.from || undefined,
      to: draft.to || undefined,
      min_quantity: draft.min_quantity ? Number(draft.min_quantity) : undefined,
      max_unit_cents: draft.max_unit_cents
        ? majorToCents(draft.max_unit_cents)
        : undefined,
      booking_mode:
        draft.booking_mode === 'instant' || draft.booking_mode === 'request_confirm'
          ? draft.booking_mode
          : undefined,
      sort: (draft.sort || 'relevance') as OfferSearchParams['sort'],
      limit: PAGE_SIZE,
      offset: Number(searchParams.get('offset') ?? 0) || 0,
    }),
    [draft, searchParams],
  );

  const search = useOfferSearch(params);

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    const sp = new URLSearchParams();
    for (const [k, v] of Object.entries(offerSearchQuery({ ...params, limit: undefined, offset: undefined }))) {
      if (v !== undefined && v !== null && v !== '') sp.set(k, String(v));
    }
    setSearchParams(sp);
  }

  function clearAll() {
    setDraft({
      q: '',
      category_id: '',
      city: '',
      country: '',
      from: '',
      to: '',
      min_quantity: '',
      max_unit_cents: '',
      booking_mode: '',
      sort: 'relevance',
    });
    setSearchParams(new URLSearchParams());
  }

  const items = search.data?.items ?? [];
  const total = search.data?.total ?? 0;
  const offset = params.offset ?? 0;

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>{t('market.title')}</h1>
          <span className="subtitle">{t('market.subtitle')}</span>
        </div>
      </div>

      <form className="card grid form-grid" onSubmit={onSubmit} aria-label={t('market.title')}>
        <InputField
          label={t('market.q')}
          value={draft.q}
          placeholder={t('market.qPlaceholder')}
          onChange={(e) => setDraft({ ...draft, q: e.target.value })}
        />
        <SelectField
          label={t('market.category')}
          value={draft.category_id}
          placeholder={t('common.all')}
          onChange={(e) => setDraft({ ...draft, category_id: e.target.value })}
          options={(categoriesQuery.data?.items ?? []).map((c) => ({
            value: c.id,
            label: c.label,
          }))}
        />
        <InputField
          label={t('market.city')}
          value={draft.city}
          onChange={(e) => setDraft({ ...draft, city: e.target.value })}
        />
        <InputField
          label={t('market.country')}
          value={draft.country}
          maxLength={2}
          placeholder="DE"
          onChange={(e) => setDraft({ ...draft, country: e.target.value })}
        />
        <InputField
          label={t('market.dateFrom')}
          type="date"
          value={draft.from}
          onChange={(e) => setDraft({ ...draft, from: e.target.value })}
        />
        <InputField
          label={t('market.dateTo')}
          type="date"
          value={draft.to}
          onChange={(e) => setDraft({ ...draft, to: e.target.value })}
        />
        <InputField
          label={t('market.minQuantity')}
          type="number"
          min={1}
          value={draft.min_quantity}
          onChange={(e) => setDraft({ ...draft, min_quantity: e.target.value })}
        />
        <InputField
          label={t('market.maxPrice')}
          type="number"
          min={0}
          step="0.01"
          value={draft.max_unit_cents}
          onChange={(e) => setDraft({ ...draft, max_unit_cents: e.target.value })}
        />
        <SelectField
          label={t('market.bookingMode')}
          value={draft.booking_mode}
          placeholder={t('common.all')}
          onChange={(e) => setDraft({ ...draft, booking_mode: e.target.value })}
          options={[
            { value: 'instant', label: t('market.instant') },
            { value: 'request_confirm', label: t('market.requestConfirm') },
          ]}
        />
        <SelectField
          label={t('market.sort')}
          value={draft.sort}
          onChange={(e) => setDraft({ ...draft, sort: e.target.value })}
          options={[
            { value: 'relevance', label: t('market.sortRelevance') },
            { value: 'price_asc', label: t('market.sortPriceAsc') },
            { value: 'price_desc', label: t('market.sortPriceDesc') },
            { value: 'newest', label: t('market.sortNewest') },
            { value: 'rating', label: t('market.sortRating') },
          ]}
        />
        <div className="row row-tight" style={{ alignItems: 'flex-end' }}>
          <Button type="submit" variant="primary" loading={search.isFetching}>
            {t('common.search')}
          </Button>
          <Button type="button" variant="ghost" onClick={clearAll}>
            {t('common.clear')}
          </Button>
        </div>
      </form>

      {search.isError ? (
        <ErrorPanel message={t('err.network_error')} onRetry={() => search.refetch()} retryLabel={t('common.retry')} />
      ) : null}

      {search.isLoading ? (
        <div className="grid grid-cards">
          {Array.from({ length: 6 }, (_, i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
      ) : null}

      {search.data && items.length === 0 ? (
        <EmptyState
          title={t('market.noResults')}
          hint={t('market.noResultsHint')}
          action={
            <Button variant="ghost" onClick={clearAll}>
              {t('common.clear')}
            </Button>
          }
        />
      ) : null}

      {items.length > 0 ? (
        <>
          <p className="muted small" role="status">
            {t('market.results', { count: total })}
          </p>
          <div className="grid grid-cards">
            {items.map((offer) => (
              <OfferCard key={offer.id} offer={offer} />
            ))}
          </div>
          <Pagination
            total={total}
            limit={PAGE_SIZE}
            offset={offset}
            onChange={(next) => {
              const sp = new URLSearchParams(searchParams);
              sp.set('offset', String(next));
              setSearchParams(sp);
            }}
          />
        </>
      ) : null}
    </div>
  );
}
