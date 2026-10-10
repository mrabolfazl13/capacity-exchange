// Platform totals and windowed activity — the screen that answers "how is the market doing".
// The two reads have different clocks and say so: `/dashboard/admin` counts everything that
// exists, while `/admin/analytics` only answers for the window it is given, so a figure is
// never shown without the period it was measured over (§8).

import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { adminApi, dashboardApi } from '@/api/endpoints';
import { useI18n } from '@/i18n/index';
import { Badge, Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { EmptyState, ErrorPanel, Loading } from '@/components/ui/States';
import { InputField } from '@/components/ui/Field';
import { formatDate, formatMoney } from '@/lib/format';

function isoFromDate(value: string, endOfDay = false): string {
  const d = new Date(value);
  if (endOfDay) d.setUTCHours(23, 59, 59, 0);
  return d.toISOString();
}

function Figure({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="row" style={{ gap: 8, alignItems: 'baseline' }}>
      <span className="small muted">{label}</span>
      <div className="spacer" />
      <strong className="mono">{value}</strong>
      {hint ? <span className="small muted">{hint}</span> : null}
    </div>
  );
}

export function AdminAnalyticsPage() {
  const { t } = useI18n();
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');

  // Half a window is a client-side dead end: the route answers 400 for it.
  const windowed = !!from && !!to;

  const totals = useQuery({
    queryKey: ['admin', 'dashboard'],
    queryFn: () => dashboardApi.admin(),
  });

  const activity = useQuery({
    queryKey: ['admin', 'analytics', windowed ? from : null, windowed ? to : null],
    queryFn: () =>
      adminApi.analytics(
        windowed ? isoFromDate(from) : undefined,
        windowed ? isoFromDate(to, true) : undefined,
      ),
  });

  if (totals.isPending) return <Loading label={t('common.loading')} />;
  if (totals.isError)
    return (
      <ErrorPanel
        message={t('err.network_error')}
        onRetry={() => totals.refetch()}
        retryLabel={t('common.retry')}
      />
    );

  const d = totals.data;
  // Analytics money carries no currency of its own, so it is read on the axis the platform
  // totals are reported in rather than assumed.
  const currency = d.currency;

  const categories = activity.data?.top_categories ?? [];
  const daily = activity.data?.daily ?? [];
  const rating = activity.data?.rating_avg ?? null;

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>{t('admin.analytics')}</h1>
          <span className="subtitle">{t('admin.title')}</span>
        </div>
        <div className="row row-tight">
          <Link to="/admin/providers">
            <Button>{t('admin.providers')}</Button>
          </Link>
          <Link to="/admin/disputes">
            <Button variant="primary">{t('admin.disputes')}</Button>
          </Link>
        </div>
      </div>

      <div className="grid grid-cards">
        <Card>
          <span className="small muted">{t('admin.users')}</span>
          <div style={{ fontSize: 28 }} className="mono">{d.users_total}</div>
        </Card>
        <Card>
          <span className="small muted">{t('admin.organizations')}</span>
          <div style={{ fontSize: 28 }} className="mono">{d.orgs_total}</div>
        </Card>
        <Card>
          <span className="small muted">{t('admin.offerCount')}</span>
          <div style={{ fontSize: 28 }} className="mono">{d.offers_total}</div>
        </Card>
        <Card>
          <span className="small muted">{t('admin.bookingCount')}</span>
          <div style={{ fontSize: 28 }} className="mono">{d.bookings_total}</div>
        </Card>
        <Card>
          <span className="small muted">{t('admin.gmv')}</span>
          <div style={{ fontSize: 28 }}>{formatMoney(d.gmv_cents, currency)}</div>
          <span className="small muted">{t('admin.settledHint')}</span>
        </Card>
        <Card>
          <span className="small muted">{t('admin.commission')}</span>
          <div style={{ fontSize: 28 }}>{formatMoney(d.commission_cents, currency)}</div>
        </Card>
        <Card>
          <span className="small muted">{t('admin.openDisputes')}</span>
          <div style={{ fontSize: 28 }}>
            <Badge tone={d.open_disputes > 0 ? 'warning' : 'default'}>{d.open_disputes}</Badge>
          </div>
        </Card>
      </div>

      <Card>
        <div className="row" style={{ alignItems: 'flex-end', gap: 12 }}>
          <InputField
            type="date"
            label={t('common.from')}
            value={from}
            onChange={(e) => setFrom(e.target.value)}
          />
          <InputField
            type="date"
            label={t('common.to')}
            value={to}
            onChange={(e) => setTo(e.target.value)}
          />
          <span className="small muted">{t('admin.windowHint')}</span>
          <div className="spacer" />
          {windowed ? (
            <Button
              size="sm"
              onClick={() => {
                setFrom('');
                setTo('');
              }}
            >
              {t('common.clear')}
            </Button>
          ) : null}
        </div>
      </Card>

      {activity.isPending ? (
        <Loading label={t('common.loading')} />
      ) : activity.isError ? (
        <ErrorPanel
          message={t('err.network_error')}
          onRetry={() => activity.refetch()}
          retryLabel={t('common.retry')}
        />
      ) : (
        <>
          <Card>
            <h3>{t('admin.activity')}</h3>
            <span className="small muted">
              {formatDate(activity.data.from)} — {formatDate(activity.data.to)}
            </span>
            {/* Every key names its own clock (§8), and the window can honestly show more
                completions than creations because they are not dated by the same event. */}
            <p className="small muted">{t('admin.clockHint')}</p>
            <div className="stack-tight" style={{ marginTop: 12 }}>
              <Figure label={t('admin.newUsers')} value={String(activity.data.users_created)} />
              <Figure
                label={t('admin.newProviders')}
                value={String(activity.data.providers_created)}
              />
              <Figure label={t('admin.newOffers')} value={String(activity.data.offers_created)} />
              <Figure
                label={t('admin.bookingsCreated')}
                value={String(activity.data.bookings_created)}
              />
              <Figure
                label={t('admin.bookingsCompleted')}
                value={String(activity.data.bookings_completed)}
              />
              <Figure
                label={t('admin.bookingsCancelled')}
                value={String(activity.data.bookings_cancelled)}
              />
              <Figure label={t('admin.ordersPlaced')} value={String(activity.data.orders_placed)} />
              <Figure
                label={t('admin.gmv')}
                value={formatMoney(activity.data.gmv_cents, currency)}
              />
              <Figure
                label={t('admin.commission')}
                value={formatMoney(activity.data.commission_cents, currency)}
              />
              <Figure
                label={t('admin.refunded')}
                value={formatMoney(activity.data.refunded_cents, currency)}
              />
              <Figure
                label={t('admin.reviewsPublished')}
                value={String(activity.data.reviews_published)}
                hint={
                  rating === null
                    ? undefined
                    : `${t('admin.ratingAvg')} ${rating.toFixed(2)}`
                }
              />
              <Figure
                label={t('admin.disputesOpened')}
                value={String(activity.data.disputes_opened)}
              />
              <Figure
                label={t('admin.disputesResolved')}
                value={String(activity.data.disputes_resolved)}
              />
            </div>
          </Card>

          <div className="grid grid-cards">
            <Card>
              <h3>{t('admin.topCategories')}</h3>
              {categories.length === 0 ? (
                <EmptyState icon="✦" title={t('common.none')} hint={t('admin.windowHint')} />
              ) : (
                <div className="table-wrap">
                  <table className="table">
                    <thead>
                      <tr>
                        <th>{t('admin.category')}</th>
                        <th className="text-right">{t('admin.bookingCount')}</th>
                        <th className="text-right">{t('prov.revenue')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {categories.map((c) => (
                        <tr key={c.category_id}>
                          <td>{c.label}</td>
                          <td className="text-right mono">{c.bookings}</td>
                          <td className="text-right mono">
                            {formatMoney(c.revenue_cents, currency)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>

            <Card>
              <h3>{t('admin.daily')}</h3>
              {daily.length === 0 ? (
                <EmptyState icon="≡" title={t('common.none')} hint={t('admin.windowHint')} />
              ) : (
                <div className="table-wrap">
                  <table className="table">
                    <thead>
                      <tr>
                        <th>{t('admin.day')}</th>
                        <th className="text-right">{t('admin.bookingsCreated')}</th>
                        <th className="text-right">{t('admin.ordersPlaced')}</th>
                        <th className="text-right">{t('admin.gmv')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {daily.map((row) => (
                        <tr key={row.date}>
                          <td className="small mono">{formatDate(row.date)}</td>
                          <td className="text-right mono">{row.bookings_created}</td>
                          <td className="text-right mono">{row.orders_placed}</td>
                          <td className="text-right mono">
                            {formatMoney(row.gmv_cents, currency)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
