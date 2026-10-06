// Platform user directory: find an account, see what it can do, and move a role.
// A grant reaches the account on its next request, so the list is refetched after
// every write rather than patched locally.

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { adminApi } from '@/api/endpoints';
import { useDebouncedValue } from '@/hooks/useCountdown';
import { useI18n } from '@/i18n/index';
import { useToast, toastError } from '@/components/ui/Toast';
import { Badge, Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { EmptyState, ErrorPanel, Loading } from '@/components/ui/States';
import { SelectField } from '@/components/ui/Field';
import { Pagination } from '@/components/ui/Pagination';
import { formatDateTime } from '@/lib/format';
import type { RoleKey } from '@/types/api';

const PAGE_SIZE = 20;

/** Every account is a customer already; the admin surface only moves granted roles (§5.1). */
const GRANTABLE: Exclude<RoleKey, 'customer'>[] = [
  'provider',
  'org_admin',
  'support',
  'platform_admin',
];

export function AdminUsersPage() {
  const { t } = useI18n();
  const toasts = useToast();
  const qc = useQueryClient();
  const [q, setQ] = useState('');
  const [role, setRole] = useState('');
  const [active, setActive] = useState('');
  const [offset, setOffset] = useState(0);
  const [pick, setPick] = useState<Record<string, string>>({});
  const search = useDebouncedValue(q, 350);

  const query = useQuery({
    queryKey: ['admin', 'users', search, role, active, offset],
    queryFn: () =>
      adminApi.users(
        {
          q: search || undefined,
          role: role || undefined,
          active: active === '' ? undefined : active === 'true',
        },
        PAGE_SIZE,
        offset,
      ),
    placeholderData: (prev) => prev,
  });

  const grant = useMutation({
    mutationFn: ({ userId, role: r }: { userId: string; role: Exclude<RoleKey, 'customer'> }) =>
      adminApi.grantRole(userId, r),
    onSuccess: () => {
      toasts.success(t('admin.grantOk'));
      void qc.invalidateQueries({ queryKey: ['admin', 'users'] });
    },
    onError: (err) => toastError(toasts, err, t),
  });

  const revoke = useMutation({
    mutationFn: ({ userId, role: r }: { userId: string; role: Exclude<RoleKey, 'customer'> }) =>
      adminApi.revokeRole(userId, r),
    onSuccess: () => {
      toasts.success(t('admin.revokeOk'));
      void qc.invalidateQueries({ queryKey: ['admin', 'users'] });
    },
    onError: (err) => toastError(toasts, err, t),
  });

  const items = query.data?.items ?? [];

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>{t('admin.users')}</h1>
          <span className="subtitle">{t('admin.title')}</span>
        </div>
      </div>

      <Card>
        <div className="form-grid">
          <label className="field">
            <span>{t('common.search')}</span>
            <input
              className="input"
              value={q}
              onChange={(e) => {
                setQ(e.target.value);
                setOffset(0);
              }}
              placeholder={`${t('auth.email')} / ${t('auth.fullName')}`}
            />
          </label>
          <SelectField
            label={t('admin.role')}
            value={role}
            placeholder={t('common.all')}
            onChange={(e) => {
              setRole(e.target.value);
              setOffset(0);
            }}
            options={GRANTABLE.map((r) => ({ value: r, label: t(`role.${r}`) }))}
          />
          <SelectField
            label={t('admin.active')}
            value={active}
            placeholder={t('common.all')}
            onChange={(e) => {
              setActive(e.target.value);
              setOffset(0);
            }}
            options={[
              { value: 'true', label: t('common.yes') },
              { value: 'false', label: t('common.no') },
            ]}
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
                  <th>{t('auth.fullName')}</th>
                  <th>{t('admin.role')}</th>
                  <th>{t('common.status')}</th>
                  <th>{t('admin.lastLogin')}</th>
                  <th>{t('admin.organizations')}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {items.map((u) => {
                  const held = u.roles.filter((r) => r !== 'customer');
                  return (
                    <tr key={u.id}>
                      <td>
                        <strong>{u.full_name}</strong>
                        <div className="small muted mono">{u.email}</div>
                      </td>
                      <td>
                        <div className="chip-row">
                          {held.length === 0 ? (
                            <span className="muted small">{t('role.customer')}</span>
                          ) : (
                            held.map((r) => (
                              <Badge key={r} tone={r === 'platform_admin' ? 'danger' : 'primary'}>
                                {t(`role.${r}`)}
                                <button
                                  type="button"
                                  className="btn btn-ghost btn-sm"
                                  aria-label={`${t('admin.revoke')} ${r}`}
                                  disabled={revoke.isPending}
                                  onClick={() =>
                                    revoke.mutate({
                                      userId: u.id,
                                      role: r as Exclude<RoleKey, 'customer'>,
                                    })
                                  }
                                >
                                  ×
                                </button>
                              </Badge>
                            ))
                          )}
                        </div>
                      </td>
                      <td>
                        <Badge tone={u.is_active ? 'success' : 'danger'}>
                          {u.is_active ? t('common.yes') : t('common.no')}
                        </Badge>
                      </td>
                      <td className="small mono">{formatDateTime(u.last_login_at)}</td>
                      <td className="small">{u.org_names?.join(', ') ?? '—'}</td>
                      <td className="text-right">
                        <div className="row row-tight" style={{ justifyContent: 'flex-end' }}>
                          <select
                            className="select"
                            aria-label={t('admin.grant')}
                            style={{ width: 150, minHeight: 32 }}
                            value={pick[u.id] ?? 'provider'}
                            onChange={(e) => setPick({ ...pick, [u.id]: e.target.value })}
                          >
                            {GRANTABLE.map((r) => (
                              <option key={r} value={r}>
                                {t(`role.${r}`)}
                              </option>
                            ))}
                          </select>
                          <Button
                            size="sm"
                            variant="primary"
                            loading={grant.isPending}
                            onClick={() =>
                              grant.mutate({
                                userId: u.id,
                                role: (pick[u.id] ?? 'provider') as Exclude<RoleKey, 'customer'>,
                              })
                            }
                          >
                            {t('admin.grant')}
                          </Button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <Pagination
            total={query.data?.total ?? 0}
            limit={PAGE_SIZE}
            offset={offset}
            onChange={setOffset}
            label={t('admin.users')}
          />
        </Card>
      )}
    </div>
  );
}
