// The append-only trail (§5.1): who changed what, from which request. Filters mirror
// the route's query parameters one-for-one, and the window is server-owned (§8).

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { adminApi } from '@/api/endpoints';
import { useI18n } from '@/i18n/index';
import { Badge, Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { EmptyState, ErrorPanel, Loading } from '@/components/ui/States';
import { InputField } from '@/components/ui/Field';
import { Pagination } from '@/components/ui/Pagination';
import { formatDateTime } from '@/lib/format';
import type { AuditLog } from '@/types/api';

const PAGE_SIZE = 30;

function isoFromDay(value: string, endOfDay = false): string {
  const d = new Date(value);
  if (endOfDay) d.setUTCHours(23, 59, 59, 0);
  return d.toISOString();
}

export function AdminAuditPage() {
  const { t } = useI18n();
  const [action, setAction] = useState('');
  const [entityType, setEntityType] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [offset, setOffset] = useState(0);

  const windowed = !!from && !!to;
  const query = useQuery({
    queryKey: ['admin', 'audit', action, entityType, windowed ? from : null, windowed ? to : null, offset],
    queryFn: () =>
      adminApi.auditLogs(
        {
          action: action || undefined,
          entityType: entityType || undefined,
          from: windowed ? isoFromDay(from) : undefined,
          to: windowed ? isoFromDay(to, true) : undefined,
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
          <h1>{t('admin.audit')}</h1>
          <span className="subtitle">{t('admin.title')}</span>
        </div>
        <span className="muted small">
          {t('common.results', { count: query.data?.total ?? 0 })}
        </span>
      </div>

      <Card>
        <div className="form-grid">
          <InputField
            label={t('admin.action')}
            value={action}
            placeholder="booking.cancel"
            onChange={(e) => {
              setAction(e.target.value);
              setOffset(0);
            }}
          />
          <InputField
            label={t('admin.entity')}
            value={entityType}
            placeholder="booking"
            onChange={(e) => {
              setEntityType(e.target.value);
              setOffset(0);
            }}
          />
          <InputField
            label={t('common.from')}
            type="date"
            value={from}
            onChange={(e) => {
              setFrom(e.target.value);
              setOffset(0);
            }}
          />
          <InputField
            label={t('common.to')}
            type="date"
            value={to}
            onChange={(e) => {
              setTo(e.target.value);
              setOffset(0);
            }}
          />
          {windowed ? (
            <div className="row" style={{ alignItems: 'flex-end' }}>
              <Button
                size="sm"
                onClick={() => {
                  setFrom('');
                  setTo('');
                  setOffset(0);
                }}
              >
                {t('common.clear')}
              </Button>
            </div>
          ) : null}
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
                  <th>{t('common.date')}</th>
                  <th>{t('admin.action')}</th>
                  <th>{t('admin.entity')}</th>
                  <th>{t('admin.actor')}</th>
                  <th>{t('admin.ip')}</th>
                  <th>{t('admin.changes')}</th>
                </tr>
              </thead>
              <tbody>
                {items.map((a) => (
                  <AuditRow key={a.id} row={a} />
                ))}
              </tbody>
            </table>
          </div>
          <Pagination
            total={query.data?.total ?? 0}
            limit={PAGE_SIZE}
            offset={offset}
            onChange={setOffset}
            label={t('admin.audit')}
          />
        </Card>
      )}
    </div>
  );
}

function AuditRow({ row: a }: { row: AuditLog }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const changed = a.before != null || a.after != null;

  return (
    <>
      <tr>
        <td className="small mono">{formatDateTime(a.created_at)}</td>
        <td>
          <Badge tone="info">{a.action}</Badge>
        </td>
        <td className="small">
          {a.entity_type}
          <div className="small muted mono">{a.entity_id ? a.entity_id.slice(0, 8) : '—'}</div>
        </td>
        <td className="small mono">{a.actor_user_id ? a.actor_user_id.slice(0, 8) : '—'}</td>
        <td className="small mono">{a.ip ?? '—'}</td>
        <td className="text-right">
          {changed ? (
            <Button size="sm" variant="ghost" onClick={() => setOpen((v) => !v)}>
              {t('admin.changes')}
            </Button>
          ) : (
            <span className="muted small">—</span>
          )}
        </td>
      </tr>
      {open && changed ? (
        <tr>
          <td colSpan={6}>
            <div className="stack-tight">
              <div>
                <span className="small muted">{t('common.before')}</span>
                <pre className="mono small" style={{ whiteSpace: 'pre-wrap' }}>
                  {JSON.stringify(a.before ?? {}, null, 2)}
                </pre>
              </div>
              <div>
                <span className="small muted">{t('common.after')}</span>
                <pre className="mono small" style={{ whiteSpace: 'pre-wrap' }}>
                  {JSON.stringify(a.after ?? {}, null, 2)}
                </pre>
              </div>
              {a.request_id ? (
                <span className="small muted">
                  {t('admin.requestId')}: <span className="mono">{a.request_id}</span>
                </span>
              ) : null}
            </div>
          </td>
        </tr>
      ) : null}
    </>
  );
}
