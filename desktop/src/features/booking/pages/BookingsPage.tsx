// My bookings: a status filter over GET /bookings?mine=true.

import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { bookingApi } from '@/api/endpoints';
import { useI18n } from '@/i18n/index';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { EmptyState, ErrorPanel, Loading } from '@/components/ui/States';
import { Pagination } from '@/components/ui/Pagination';
import { SelectField } from '@/components/ui/Field';
import { formatMoney, formatDateTime } from '@/lib/format';
import { BookingStatusBadge } from '@/features/booking/components/BookingStatusBadge';
import type { BookingStatus } from '@/types/api';

const PAGE_SIZE = 20;
const ALL_STATUSES: BookingStatus[] = [
  'draft', 'hold', 'confirmed', 'in_progress', 'completed', 'cancelled', 'expired', 'disputed',
];

export function BookingsPage() {
  const { t } = useI18n();
  const [status, setStatus] = useState<string>('');
  const [offset, setOffset] = useState(0);

  const query = useQuery({
    queryKey: ['bookings', 'mine', status, offset],
    queryFn: () => bookingApi.list({ mine: true, status: status || undefined }, PAGE_SIZE, offset),
  });

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>{t('book.myBookings')}</h1>
          <span className="subtitle">{t('nav.bookings')}</span>
        </div>
        <Link to="/marketplace">
          <Button variant="primary">{t('market.title')}</Button>
        </Link>
      </div>

      <Card>
        <div className="row" style={{ alignItems: 'flex-end', gap: 12 }}>
          <SelectField
            label={t('common.status')}
            value={status}
            placeholder={t('common.all')}
            onChange={(e) => {
              setStatus(e.target.value);
              setOffset(0);
            }}
            options={ALL_STATUSES.map((s) => ({ value: s, label: t(`book.status.${s}`) }))}
          />
        </div>
      </Card>

      {query.isPending ? (
        <Loading label={t('common.loading')} />
      ) : query.isError ? (
        <ErrorPanel message={t('err.network_error')} onRetry={() => query.refetch()} retryLabel={t('common.retry')} />
      ) : (query.data?.items ?? []).length === 0 ? (
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
        <Card>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>{t('offer.details')}</th>
                  <th>{t('common.status')}</th>
                  <th>{t('book.windowStart')}</th>
                  <th className="text-right">{t('common.quantity')}</th>
                  <th className="text-right">{t('common.total')}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {(query.data?.items ?? []).map((b) => (
                  <tr key={b.id}>
                    <td>
                      <strong>{b.offer_title ?? b.id.slice(0, 8)}</strong>
                      <div className="small muted">{b.org_name ?? ''}</div>
                    </td>
                    <td>
                      <BookingStatusBadge status={b.status} />
                    </td>
                    <td className="small">{formatDateTime(b.window_start)}</td>
                    <td className="text-right mono">{b.quantity}</td>
                    <td className="text-right mono">{formatMoney(b.total_cents, b.currency)}</td>
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
          <Pagination
            total={query.data?.total ?? 0}
            limit={PAGE_SIZE}
            offset={offset}
            onChange={setOffset}
            label={t('book.myBookings')}
          />
        </Card>
      )}
    </div>
  );
}
