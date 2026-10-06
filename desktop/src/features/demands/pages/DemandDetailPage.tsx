// One demand and the proposals the scorer keeps for it. The customer reads and closes;
// answering a proposal (accept → draft booking) is the provider's action, not this screen's.

import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { demandApi } from '@/api/endpoints';
import { useI18n } from '@/i18n/index';
import { useToast, toastError } from '@/components/ui/Toast';
import { Card, Badge } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { EmptyState, ErrorPanel, Loading } from '@/components/ui/States';
import { Pagination } from '@/components/ui/Pagination';
import { formatDate, formatDateTime, formatMoney } from '@/lib/format';
import type { MatchStatus } from '@/types/api';

const PAGE_SIZE = 10;

export function DemandDetailPage() {
  const { demandId } = useParams<{ demandId: string }>();
  const { t } = useI18n();
  const toasts = useToast();
  const qc = useQueryClient();
  const [offset, setOffset] = useState(0);

  const demand = useQuery({
    queryKey: ['demands', demandId],
    queryFn: () => demandApi.get(demandId as string),
    enabled: !!demandId,
  });
  const matches = useQuery({
    queryKey: ['demands', demandId, 'matches', offset],
    queryFn: () => demandApi.matches(demandId as string, PAGE_SIZE, offset),
    enabled: !!demandId,
  });

  const close = useMutation({
    mutationFn: () => demandApi.close(demandId as string),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['demands'] });
      toasts.success(t('demand.closedOk'));
    },
    onError: (err) => toastError(toasts, err, t),
  });

  const cancel = useMutation({
    mutationFn: () => demandApi.cancel(demandId as string),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['demands'] });
      toasts.success(t('demand.cancelOk'));
    },
    onError: (err) => toastError(toasts, err, t),
  });

  if (demand.isPending) return <Loading label={t('common.loading')} />;
  if (demand.isError || !demand.data)
    return (
      <ErrorPanel
        message={t('err.not_found')}
        onRetry={() => demand.refetch()}
        retryLabel={t('common.retry')}
      />
    );

  const d = demand.data;
  const rows = matches.data?.items ?? [];

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>{d.category_label ?? t('demand.title')}</h1>
          <span className="subtitle">{formatDate(d.created_at)}</span>
        </div>
        <div className="row row-tight">
          <Badge
            tone={
              d.status === 'open'
                ? 'info'
                : d.status === 'matched'
                  ? 'success'
                  : d.status === 'cancelled'
                    ? 'danger'
                    : 'default'
            }
          >
            {t(`demand.status.${d.status}`)}
          </Badge>
          <Link to="/demands">
            <Button>{t('common.back')}</Button>
          </Link>
        </div>
      </div>

      <Card>
        <p>{d.description}</p>
        <div className="kv">
          <span>{t('common.quantity')}</span>
          <strong>{d.quantity}</strong>
          <span>{t('demand.desiredStart')}</span>
          <strong>{d.desired_start ? formatDateTime(d.desired_start) : '—'}</strong>
          <span>{t('demand.desiredEnd')}</span>
          <strong>{d.desired_end ? formatDateTime(d.desired_end) : '—'}</strong>
          <span>{t('demand.budgetMin')}</span>
          <strong>
            {d.budget_min_cents != null ? formatMoney(d.budget_min_cents, 'USD') : '—'}
          </strong>
          <span>{t('demand.budgetMax')}</span>
          <strong>
            {d.budget_max_cents != null ? formatMoney(d.budget_max_cents, 'USD') : '—'}
          </strong>
          <span>{t('demand.expires')}</span>
          <strong>{d.expires_at ? formatDateTime(d.expires_at) : '—'}</strong>
          {d.address?.city ? (
            <>
              <span>{t('demand.city')}</span>
              <strong>{d.address.city}</strong>
            </>
          ) : null}
        </div>
        {d.status === 'open' || d.status === 'matched' ? (
          <div className="row row-tight" style={{ marginTop: 12 }}>
            <Button variant="danger" loading={close.isPending} onClick={() => close.mutate()}>
              {t('demand.close')}
            </Button>
            <Button variant="ghost" loading={cancel.isPending} onClick={() => cancel.mutate()}>
              {t('book.cancel')}
            </Button>
          </div>
        ) : null}
      </Card>

      <Card>
        <h2>{t('demand.yourMatches')}</h2>
        {matches.isPending ? (
          <Loading label={t('common.loading')} />
        ) : matches.isError ? (
          <ErrorPanel
            message={t('err.network_error')}
            onRetry={() => matches.refetch()}
            retryLabel={t('common.retry')}
          />
        ) : rows.length === 0 ? (
          <EmptyState title={t('demand.noMatches')} />
        ) : (
          <>
            <div className="stack-tight">
              {rows.map((m) => (
                <div key={m.id} className="divider">
                  <div className="row row-tight" style={{ flexWrap: 'wrap' }}>
                    <strong>{m.offer?.title ?? m.offer_id.slice(0, 8)}</strong>
                    <span className="small muted">{m.offer?.org_name ?? ''}</span>
                    <Badge tone={m.status === 'accepted' ? 'success' : m.status === 'declined' ? 'danger' : 'info'}>
                      {t(`match.status.${m.status as MatchStatus}`)}
                    </Badge>
                    <span className="spacer" />
                    <span className="small muted">
                      {t('match.score')}: <strong className="mono">{m.score}</strong>
                    </span>
                    {m.offer ? (
                      <span className="mono small">
                        {formatMoney(m.offer.unit_amount_cents, m.offer.currency)}
                      </span>
                    ) : null}
                    <Link to={`/offers/${m.offer_id}`}>
                      <Button size="sm">{t('common.view')}</Button>
                    </Link>
                    {m.booking_id ? (
                      <Link to={`/bookings/${m.booking_id}`}>
                        <Button size="sm" variant="primary">
                          {t('book.detail')}
                        </Button>
                      </Link>
                    ) : null}
                  </div>
                  {(m.reasons ?? []).length > 0 ? (
                    <div className="chip-row small muted" style={{ marginTop: 4 }}>
                      <span>{t('match.reasons')}:</span>
                      {m.reasons.map((r) => (
                        <Badge key={r}>{r}</Badge>
                      ))}
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
            <Pagination
              total={matches.data?.total ?? 0}
              limit={PAGE_SIZE}
              offset={offset}
              onChange={setOffset}
              label={t('demand.matches')}
            />
          </>
        )}
      </Card>
    </div>
  );
}
