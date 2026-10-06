// The supply side with the counts that make each profile triageable in one table.
// Reads only: an organization's status is changed by its own admins, not from here.

import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { adminApi } from '@/api/endpoints';
import { useDebouncedValue } from '@/hooks/useCountdown';
import { useI18n } from '@/i18n/index';
import { Badge, Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { EmptyState, ErrorPanel, Loading } from '@/components/ui/States';
import { InputField, SelectField } from '@/components/ui/Field';
import { Pagination } from '@/components/ui/Pagination';
import { formatDateTime, formatMoney } from '@/lib/format';
import type { AdminProvider } from '@/types/api';

const PAGE_SIZE = 20;

export function AdminProvidersPage() {
  const { t } = useI18n();
  const [q, setQ] = useState('');
  const [status, setStatus] = useState('');
  const [country, setCountry] = useState('');
  const [offset, setOffset] = useState(0);
  const search = useDebouncedValue(q, 350);

  const query = useQuery({
    queryKey: ['admin', 'providers', search, status, country, offset],
    queryFn: () =>
      adminApi.providers(
        {
          q: search || undefined,
          status: status || undefined,
          country: country || undefined,
        },
        PAGE_SIZE,
        offset,
      ),
    placeholderData: (prev) => prev,
  });

  const items = query.data?.items ?? [];

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>{t('admin.providers')}</h1>
          <span className="subtitle">{t('admin.title')}</span>
        </div>
        <Link to="/admin/disputes">
          <Button>{t('admin.disputes')}</Button>
        </Link>
      </div>

      <Card>
        <div className="form-grid">
          <InputField
            label={t('common.search')}
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setOffset(0);
            }}
            placeholder={`${t('auth.orgName')} / ${t('auth.email')}`}
          />
          <SelectField
            label={t('common.status')}
            value={status}
            placeholder={t('common.all')}
            onChange={(e) => {
              setStatus(e.target.value);
              setOffset(0);
            }}
            options={[
              { value: 'active', label: t('admin.orgStatus.active') },
              { value: 'suspended', label: t('admin.orgStatus.suspended') },
            ]}
          />
          <InputField
            label={t('market.country')}
            maxLength={2}
            value={country}
            onChange={(e) => {
              setCountry(e.target.value.toUpperCase());
              setOffset(0);
            }}
          />
        </div>
      </Card>

      {query.isPending ? (
        <Loading label={t('common.loading')} />
      ) : query.isError ? (
        <ErrorPanel
          message={t('err.network_error')}
          onRetry={() => query.refetch()}
          retryLabel={t('common.retry')}
        />
      ) : items.length === 0 ? (
        <EmptyState title={t('common.none')} />
      ) : (
        <Card>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>{t('auth.orgName')}</th>
                  <th>{t('common.status')}</th>
                  <th className="text-right">{t('admin.offerCount')}</th>
                  <th className="text-right">{t('admin.bookingCount')}</th>
                  <th className="text-right">{t('admin.gmv')}</th>
                  <th className="text-right">{t('common.rating')}</th>
                  <th className="text-right">{t('admin.openDisputes')}</th>
                  <th>{t('admin.orgAdminEmail')}</th>
                  <th>{t('common.date')}</th>
                </tr>
              </thead>
              <tbody>
                {items.map((p) => (
                  <ProviderRow key={p.id} p={p} />
                ))}
              </tbody>
            </table>
          </div>
          <Pagination
            total={query.data?.total ?? 0}
            limit={PAGE_SIZE}
            offset={offset}
            onChange={setOffset}
            label={t('admin.providers')}
          />
        </Card>
      )}
    </div>
  );
}

function ProviderRow({ p }: { p: AdminProvider }) {
  const { t } = useI18n();
  return (
    <tr>
      <td>
        <strong>{p.name}</strong>
        <div className="small muted mono">/{p.slug}</div>
      </td>
      <td>
        <Badge tone={p.status === 'active' ? 'success' : 'danger'}>
          {p.status === 'active' ? t('admin.orgStatus.active') : t('admin.orgStatus.suspended')}
        </Badge>
      </td>
      <td className="text-right mono">{p.offer_count ?? 0}</td>
      <td className="text-right mono">{p.booking_count ?? 0}</td>
      <td className="text-right mono">{formatMoney(p.gmv_cents ?? 0, p.currency ?? 'USD')}</td>
      <td className="text-right mono">
        {p.rating_avg != null ? `★ ${p.rating_avg.toFixed(1)} (${p.rating_count ?? 0})` : '—'}
      </td>
      <td className="text-right">
        {(p.open_disputes ?? 0) > 0 ? (
          <Badge tone="danger">{p.open_disputes}</Badge>
        ) : (
          <span className="muted mono">0</span>
        )}
      </td>
      <td className="small mono">{p.org_admin_email ?? '—'}</td>
      <td className="small mono">{formatDateTime(p.created_at)}</td>
    </tr>
  );
}
