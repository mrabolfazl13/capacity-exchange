// Offer detail: the listing, its rules, its live availability and its reviews, with
// the entry point into the booking flow.

import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { offerApi } from '@/api/endpoints';
import { useI18n } from '@/i18n/index';
import { Card, Badge } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { EmptyState, ErrorPanel, Loading, Skeleton } from '@/components/ui/States';
import { formatDuration, formatMoney, slotLabel, formatDate } from '@/lib/format';

function defaultRange(): { from: string; to: string } {
  const start = new Date();
  const end = new Date(start.getTime() + 14 * 86_400_000);
  return { from: start.toISOString(), to: end.toISOString() };
}

export function OfferDetailPage() {
  const { offerId } = useParams<{ offerId: string }>();
  const { t } = useI18n();
  const [range, setRange] = useState(defaultRange);

  const offer = useQuery({
    queryKey: ['offers', offerId],
    queryFn: () => offerApi.get(offerId as string),
    enabled: !!offerId,
  });
  const availability = useQuery({
    queryKey: ['offers', offerId, 'availability', range],
    queryFn: () => offerApi.availability(offerId as string, range.from, range.to),
    enabled: !!offerId,
  });
  const reviews = useQuery({
    queryKey: ['offers', offerId, 'reviews'],
    queryFn: () => offerApi.reviews(offerId as string, 10, 0),
    enabled: !!offerId,
  });

  if (offer.isPending) return <Loading label={t('common.loading')} />;
  if (offer.isError)
    return (
      <ErrorPanel
        message={t('err.not_found')}
        onRetry={() => offer.refetch()}
        retryLabel={t('common.retry')}
      />
    );

  const o = offer.data;
  const windows = availability.data?.items ?? [];

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>{o.title}</h1>
          <span className="subtitle">
            {o.org_name ?? o.resource_name ?? ''}
            {o.city ? ` · ${o.city}` : ''}
          </span>
        </div>
        <div className="row row-tight">
          <Link to="/marketplace">
            <Button>{t('common.back')}</Button>
          </Link>
          <Link to={`/offers/${o.id}/book`}>
            <Button variant="primary">{t('offer.book')}</Button>
          </Link>
        </div>
      </div>

      <div className="grid grid-cards">
        <Card>
          <h2>{t('offer.pricing')}</h2>
          <div className="kv">
            <span>{t('offer.unitPrice')}</span>
            <strong>{formatMoney(o.unit_amount_cents, o.currency)}</strong>
            <span>{t('common.currency')}</span>
            <strong>{o.currency}</strong>
            <span>{t('wizard.pricingMode')}</span>
            <strong>{o.pricing_mode}</strong>
          </div>
        </Card>
        <Card>
          <h2>{t('offer.capacityRules')}</h2>
          <div className="kv">
            <span>{t('offer.minLead')}</span>
            <strong>{formatDuration(o.min_lead_time_minutes)}</strong>
            <span>{t('offer.minDuration')}</span>
            <strong>{formatDuration(o.min_duration_minutes)}</strong>
            <span>{t('offer.maxDuration')}</span>
            <strong>{formatDuration(o.max_duration_minutes)}</strong>
            <span>{t('offer.holdMinutes')}</span>
            <strong>{formatDuration(o.hold_minutes)}</strong>
            <span>{t('market.minQuantity')}</span>
            <strong>{o.min_quantity ?? '—'}</strong>
            <span>{t('wizard.maxQty')}</span>
            <strong>{o.max_quantity ?? o.max_quantity_definition ?? '—'}</strong>
            <span>{t('market.bookingMode')}</span>
            <strong>
              {o.booking_mode === 'instant' ? t('market.instant') : t('market.requestConfirm')}
            </strong>
          </div>
        </Card>
        <Card>
          <h2>{t('offer.location')}</h2>
          <div className="kv">
            <span>{t('common.date')}</span>
            <strong>{formatDate(o.published_at ?? o.created_at)}</strong>
            <span>{t('offer.provider')}</span>
            <strong>{o.org_name ?? '—'}</strong>
            <span>{t('market.city')}</span>
            <strong>{o.city ?? '—'}</strong>
          </div>
          {o.description ? <p className="small muted">{o.description}</p> : null}
        </Card>
      </div>

      <Card>
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <h2>{t('offer.availability')}</h2>
          <div className="row row-tight">
            <label className="small muted">
              {t('common.from')}
              <input
                className="input"
                type="date"
                value={range.from.slice(0, 10)}
                onChange={(e) =>
                  setRange({ ...defaultRange(), from: new Date(`${e.target.value}T00:00:00Z`).toISOString() })
                }
              />
            </label>
            <label className="small muted">
              {t('common.to')}
              <input
                className="input"
                type="date"
                value={range.to.slice(0, 10)}
                onChange={(e) =>
                  setRange({ ...range, to: new Date(`${e.target.value}T23:59:59Z`).toISOString() })
                }
              />
            </label>
          </div>
        </div>
        {availability.isPending ? (
          <Skeleton lines={4} />
        ) : availability.isError ? (
          <ErrorPanel message={t('err.no_availability')} onRetry={() => availability.refetch()} />
        ) : windows.length === 0 ? (
          <EmptyState title={t('offer.noAvailability')} />
        ) : (
          <div className="avail-grid" role="list" aria-label={t('offer.availability')}>
            {windows.map((w) => (
              <div
                key={`${w.window_start}-${w.window_end}`}
                className={w.free_quantity <= 0 ? 'avail-cell full' : 'avail-cell free'}
                role="listitem"
                title={`${slotLabel(w.window_start, w.window_end)} · ${w.free_quantity} ${t('offer.freeQuantity')}`}
              >
                <span className="small">{slotLabel(w.window_start, w.window_end)}</span>
                <strong>
                  {w.free_quantity} {t('offer.freeQuantity')}
                </strong>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card>
        <h2>{t('offer.cancellation')}</h2>
        {o.cancellation_policy?.length ? (
          <ul className="stack-tight small">
            {o.cancellation_policy.map((band, i) => (
              <li key={i}>{t('book.refundPct', { pct: band.refund_pct, hours: band.hours_before })}</li>
            ))}
          </ul>
        ) : (
          <p className="muted small">{t('book.noRefund')}</p>
        )}
      </Card>

      <Card>
        <h2>
          {t('common.reviews')}{' '}
          {o.rating_avg != null ? (
            <Badge tone="primary">★ {o.rating_avg.toFixed(1)}</Badge>
          ) : null}
        </h2>
        {reviews.isPending ? (
          <Skeleton lines={3} />
        ) : (reviews.data?.items ?? []).length === 0 ? (
          <p className="muted small">{t('common.none')}</p>
        ) : (
          <div className="stack-tight">
            {(reviews.data?.items ?? []).map((r) => (
              <div key={r.id} className="divider">
                <div className="row row-tight">
                  <Badge tone="primary">★ {r.rating}</Badge>
                  <strong className="small">{r.reviewer_name ?? '—'}</strong>
                  <span className="spacer" />
                  <span className="small muted">{formatDate(r.created_at)}</span>
                </div>
                {r.comment ? <p className="small">{r.comment}</p> : null}
                {r.provider_reply ? (
                  <p className="small muted">↳ {r.provider_reply}</p>
                ) : null}
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
