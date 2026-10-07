// Provider home: the numbers a seller acts on, all from GET /dashboard/provider.
// The window is server-owned (§8): `from`/`to` travel together, and leaving them
// empty asks for the trailing default rather than a client-side guess.
// The two assistant panels under the KPIs read the same window from `/ai/*`; they
// render only when they have something to say, so advice never gates the numbers.

import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { dashboardApi } from '@/api/endpoints';
import { useI18n } from '@/i18n/index';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { EmptyState, ErrorPanel, Loading } from '@/components/ui/States';
import { InputField } from '@/components/ui/Field';
import { formatDateTime, formatMoney } from '@/lib/format';
import { BookingStatusBadge } from '@/features/booking/components/BookingStatusBadge';
import { useCopilot, useUtilizationInsights } from '@/features/assistant/hooks';
import { CopilotCard, QuietHoursCard } from '@/features/assistant/components/AssistantCards';
import type { BookingStatus } from '@/types/api';

const STATUS_ORDER: BookingStatus[] = [
  'hold', 'draft', 'confirmed', 'in_progress', 'completed', 'cancelled', 'expired', 'disputed',
];

function isoFromDate(value: string, endOfDay = false): string {
  const d = new Date(value);
  if (endOfDay) d.setUTCHours(23, 59, 59, 0);
  return d.toISOString();
}

export function ProviderDashboardPage() {
  const { t } = useI18n();
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');

  // Half a window is a client-side dead end: the route answers 400 for it.
  const windowed = !!from && !!to;
  const query = useQuery({
    queryKey: ['dashboard', 'provider', windowed ? from : null, windowed ? to : null],
    queryFn: () =>
      dashboardApi.provider(
        windowed ? isoFromDate(from) : undefined,
        windowed ? isoFromDate(to, true) : undefined,
      ),
  });

  // Advisory reads over the same window the dashboard answers for, called before the
  // dashboard's own early returns so the hook order stays stable either way.
  const assistantFrom = windowed ? isoFromDate(from) : undefined;
  const assistantTo = windowed ? isoFromDate(to, true) : undefined;
  const copilot = useCopilot();
  const insights = useUtilizationInsights(assistantFrom, assistantTo);

  if (query.isPending) return <Loading label={t('common.loading')} />;
  if (query.isError)
    return (
      <ErrorPanel
        message={t('err.network_error')}
        onRetry={() => query.refetch()}
        retryLabel={t('common.retry')}
      />
    );

  const d = query.data;
  const counts = d.bookings_by_status ?? {};
  const statusRows = STATUS_ORDER.filter((s) => (counts[s] ?? 0) > 0);
  const upcoming = d.upcoming_bookings ?? [];
  const topOffers = d.top_offers ?? [];

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>{t('prov.dashboard')}</h1>
          <span className="subtitle">{t('prov.title')}</span>
        </div>
        <div className="row row-tight">
          <Link to="/provider/bookings">
            <Button>{t('prov.board')}</Button>
          </Link>
          <Link to="/provider/new">
            <Button variant="primary">{t('nav.wizard')}</Button>
          </Link>
        </div>
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
          <span className="small muted">{t('prov.utilizationHint')}</span>
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

      <div className="grid grid-cards">
        <Card>
          <span className="small muted">{t('prov.utilization')}</span>
          <div style={{ fontSize: 28 }}>{d.utilization_pct.toFixed(1)}%</div>
        </Card>
        <Card>
          <span className="small muted">{t('prov.revenue')}</span>
          <div style={{ fontSize: 28 }}>
            {formatMoney(d.revenue_cents ?? 0, d.revenue_currency ?? 'USD')}
          </div>
        </Card>
        <Card>
          <span className="small muted">{t('prov.bookingsByStatus')}</span>
          <div className="stack-tight" style={{ marginTop: 8 }}>
            {statusRows.length === 0 ? (
              <span className="muted small">{t('dash.empty')}</span>
            ) : (
              statusRows.map((s) => (
                <div key={s} className="row" style={{ gap: 8 }}>
                  <BookingStatusBadge status={s} />
                  <span className="spacer" />
                  <strong className="mono">{counts[s]}</strong>
                </div>
              ))
            )}
          </div>
        </Card>
      </div>

      {copilot.data ? <CopilotCard briefing={copilot.data} /> : null}
      {insights.data ? <QuietHoursCard insights={insights.data} /> : null}

      <Card>
        <h2>{t('prov.upcoming')}</h2>
        {upcoming.length === 0 ? (
          <EmptyState title={t('prov.noUpcoming')} />
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>{t('common.when')}</th>
                  <th>{t('offer.details')}</th>
                  <th>{t('common.customer')}</th>
                  <th className="text-right">{t('common.quantity')}</th>
                  <th className="text-right">{t('common.total')}</th>
                  <th>{t('common.status')}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {upcoming.map((b) => (
                  <tr key={b.id}>
                    <td className="small mono">{formatDateTime(b.window_start)}</td>
                    <td>
                      <strong>{b.offer_title ?? b.id.slice(0, 8)}</strong>
                      <div className="small muted">{b.resource_name ?? ''}</div>
                    </td>
                    <td className="small">{b.customer_name ?? '—'}</td>
                    <td className="text-right mono">{b.quantity}</td>
                    <td className="text-right mono">{formatMoney(b.total_cents, b.currency)}</td>
                    <td>
                      <BookingStatusBadge status={b.status} />
                    </td>
                    <td className="text-right">
                      <Link to={`/bookings/${b.id}`}>
                        <Button size="sm">{t('common.view')}</Button>
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card>
        <h2>{t('prov.topOffers')}</h2>
        {topOffers.length === 0 ? (
          <p className="muted small">{t('prov.noOffers')}</p>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>{t('offer.details')}</th>
                  <th className="text-right">{t('book.myBookings')}</th>
                  <th className="text-right">{t('prov.revenue')}</th>
                </tr>
              </thead>
              <tbody>
                {topOffers.map((o) => (
                  <tr key={o.offer_id}>
                    <td>
                      <Link to={`/offers/${o.offer_id}`}>{o.title}</Link>
                    </td>
                    <td className="text-right mono">{o.bookings}</td>
                    <td className="text-right mono">
                      {formatMoney(o.revenue_cents, d.revenue_currency ?? 'USD')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
