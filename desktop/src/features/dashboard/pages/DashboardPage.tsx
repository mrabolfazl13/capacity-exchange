// Customer home: the four numbers a buyer cares about, all read from
// GET /dashboard/customer.

import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { dashboardApi } from '@/api/endpoints';
import { useI18n } from '@/i18n/index';
import { Card, Badge } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { EmptyState, ErrorPanel, Loading } from '@/components/ui/States';
import { Pagination } from '@/components/ui/Pagination';
import { formatMoney, formatDateTime } from '@/lib/format';
import { BookingStatusBadge } from '@/features/booking/components/BookingStatusBadge';
import { useState } from 'react';

const PAGE_SIZE = 10;

export function DashboardPage() {
  const { t } = useI18n();
  const [offset, setOffset] = useState(0);
  const query = useQuery({
    queryKey: ['dashboard', 'customer'],
    queryFn: () => dashboardApi.customer(),
  });

  if (query.isPending) return <Loading label={t('common.loading')} />;
  if (query.isError)
    return (
      <ErrorPanel
        message={t('err.network_error')}
        onRetry={() => query.refetch()}
        retryLabel={t('common.retry')}
      />
    );

  const data = query.data;
  const active = data.active_bookings ?? [];
  const orders = data.recent_orders ?? [];
  const page = orders.slice(offset, offset + PAGE_SIZE);

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>{t('dash.title')}</h1>
          <span className="subtitle">{t('app.tagline')}</span>
        </div>
        <div className="row row-tight">
          <Link to="/marketplace">
            <Button variant="primary">{t('nav.marketplace')}</Button>
          </Link>
          <Link to="/demands">
            <Button>{t('demand.new')}</Button>
          </Link>
        </div>
      </div>

      <div className="grid grid-cards">
        <Card>
          <span className="small muted">{t('dash.activeBookings')}</span>
          <div style={{ fontSize: 28 }}>{active.length}</div>
        </Card>
        <Card>
          <span className="small muted">{t('dash.spend')}</span>
          <div style={{ fontSize: 28 }}>
            {formatMoney(data.spend_cents ?? 0, data.spend_currency ?? 'USD')}
          </div>
        </Card>
        <Card>
          <span className="small muted">{t('dash.unread')}</span>
          <div style={{ fontSize: 28 }}>{data.unread_notifications ?? 0}</div>
        </Card>
      </div>

      <Card>
        <h2>{t('book.myBookings')}</h2>
        {active.length === 0 ? (
          <EmptyState
            title={t('book.noBookings')}
            hint={t('book.noBookingsHint')}
            action={
              <Link to="/marketplace">
                <Button variant="primary">{t('market.title')}</Button>
              </Link>
            }
          />
        ) : (
          <div className="stack-tight">
            {active.map((b) => (
              <div key={b.id} className="row" style={{ gap: 12 }}>
                <div>
                  <strong>{b.offer_title ?? t('book.detail')}</strong>
                  <div className="small muted">
                    {formatDateTime(b.window_start)} · {b.quantity} ×{' '}
                    {formatMoney(b.unit_amount_cents, b.currency)}
                  </div>
                </div>
                <div className="spacer" />
                <BookingStatusBadge status={b.status} />
                <Link to={`/bookings/${b.id}`}>
                  <Button size="sm">{t('common.view')}</Button>
                </Link>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card>
        <h2>{t('dash.recentOrders')}</h2>
        {orders.length === 0 ? (
          <p className="muted small">{t('dash.empty')}</p>
        ) : (
          <>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>{t('common.date')}</th>
                    <th>{t('common.status')}</th>
                    <th className="text-right">{t('common.total')}</th>
                    <th className="text-right">{t('book.refunded')}</th>
                  </tr>
                </thead>
                <tbody>
                  {page.map((o) => (
                    <tr key={o.id}>
                      <td className="mono small">{formatDateTime(o.placed_at ?? o.created_at)}</td>
                      <td>
                        <Badge tone={o.payment_status === 'paid' ? 'success' : 'warning'}>
                          {o.number}
                        </Badge>
                      </td>
                      <td className="text-right mono">
                        {formatMoney(o.total_cents, o.currency)}
                      </td>
                      <td className="text-right mono">
                        {o.refunded_cents > 0 ? formatMoney(o.refunded_cents, o.currency) : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination
              total={orders.length}
              limit={PAGE_SIZE}
              offset={offset}
              onChange={setOffset}
              label={t('dash.recentOrders')}
            />
          </>
        )}
      </Card>
    </div>
  );
}
