// Demand inbox: open requests that scored against this organization's published
// offers. Accepting a proposal raises the draft booking the customer then confirms;
// rejecting it leaves the demand open for other providers.

import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { matchApi } from '@/api/endpoints';
import { useAuth } from '@/auth/AuthProvider';
import { useI18n } from '@/i18n/index';
import { useToast, toastError } from '@/components/ui/Toast';
import { Badge, Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { EmptyState, ErrorPanel, Loading } from '@/components/ui/States';
import { SelectField } from '@/components/ui/Field';
import { Pagination } from '@/components/ui/Pagination';
import { formatDate, formatDateTime, formatMoney } from '@/lib/format';
import type { BadgeTone } from '@/components/ui/Card';
import type { MatchStatus } from '@/types/api';

const PAGE_SIZE = 20;
const FILTERS: MatchStatus[] = ['suggested', 'accepted', 'declined', 'expired'];
const STATUS_TONES: Record<MatchStatus, BadgeTone> = {
  suggested: 'info',
  accepted: 'success',
  declined: 'default',
  expired: 'warning',
};

/** The scorer ships 0..1000 (§5.3); the tone says "worth acting on" at a glance. */
function scoreTone(score: number): BadgeTone {
  if (score >= 700) return 'success';
  if (score >= 400) return 'primary';
  return 'default';
}

export function ProviderMatchesPage() {
  const { t } = useI18n();
  const { user } = useAuth();
  const toasts = useToast();
  const qc = useQueryClient();
  const [status, setStatus] = useState('suggested');
  const [offset, setOffset] = useState(0);

  const query = useQuery({
    queryKey: ['matches', 'provider', status || null, offset],
    queryFn: () => matchApi.list(status || undefined, PAGE_SIZE, offset),
    enabled: !!user?.active_org_id,
    placeholderData: (prev) => prev,
  });

  const answer = useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'accept' | 'reject' }) =>
      action === 'accept' ? matchApi.accept(id) : matchApi.reject(id),
    onSuccess: (m, vars) => {
      toasts.success(vars.action === 'accept' ? t('match.bookingCreated') : t('match.declined'));
      void qc.invalidateQueries({ queryKey: ['matches'] });
      void qc.invalidateQueries({ queryKey: ['bookings'] });
      void qc.invalidateQueries({ queryKey: ['notifications'] });
      if (vars.action === 'accept' && m.booking_id) {
        void qc.invalidateQueries({ queryKey: ['booking', m.booking_id] });
      }
    },
    onError: (err) => toastError(toasts, err, t),
  });

  const items = query.data?.items ?? [];

  if (!user?.active_org_id) {
    return (
      <EmptyState title={t('prov.noMatches')} hint={t('prov.noMatchesHint')} />
    );
  }

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>{t('prov.matchesTitle')}</h1>
          <span className="subtitle">{t('prov.title')}</span>
        </div>
        <div className="row row-tight">
          <Link to="/provider/offers">
            <Button>{t('nav.providerOffers')}</Button>
          </Link>
          <Link to="/demands">
            <Button>{t('nav.demands')}</Button>
          </Link>
        </div>
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
            options={FILTERS.map((s) => ({ value: s, label: t(`match.status.${s}`) }))}
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
        <EmptyState title={t('prov.noMatches')} hint={t('prov.noMatchesHint')} />
      ) : (
        <div className="stack">
          {items.map((m) => {
            const demand = m.demand;
            const offer = m.offer;
            return (
              <Card key={m.id}>
                <div className="row" style={{ justifyContent: 'space-between', gap: 12 }}>
                  <div>
                    <h3 style={{ margin: 0 }}>
                      {demand?.description?.slice(0, 90) ?? t('match.demand')}
                    </h3>
                    <div className="small muted">
                      {offer?.title ?? m.offer_id.slice(0, 8)}
                      {offer?.city ? ` · ${offer.city}` : ''}
                    </div>
                  </div>
                  <div className="row row-tight">
                    <Badge tone={scoreTone(m.score)}>
                      {t('match.score')} {m.score.toFixed(0)}
                    </Badge>
                    <Badge tone={STATUS_TONES[m.status]}>{t(`match.status.${m.status}`)}</Badge>
                  </div>
                </div>

                <div className="kv" style={{ marginTop: 8 }}>
                  <span>{t('common.quantity')}</span>
                  <strong className="mono">{demand?.quantity ?? '—'}</strong>
                  <span>{t('demand.desiredStart')}</span>
                  <span className="small">
                    {demand?.desired_start ? formatDateTime(demand.desired_start) : '—'}
                  </span>
                  <span>{t('demand.budgetMax')}</span>
                  <span className="mono">
                    {demand?.budget_max_cents != null ? formatMoney(demand.budget_max_cents, 'USD') : '—'}
                  </span>
                  <span>{t('demand.expires')}</span>
                  <span className="small">
                    {demand?.expires_at ? formatDate(demand.expires_at) : '—'}
                  </span>
                </div>

                {m.reasons.length > 0 ? (
                  <div className="chip-row" style={{ marginTop: 8 }}>
                    {m.reasons.map((r) => (
                      <Badge key={r}>{r}</Badge>
                    ))}
                  </div>
                ) : null}

                <div className="row" style={{ marginTop: 12 }}>
                  <Link to={`/demands/${m.demand_id}`}>
                    <Button size="sm">{t('prov.viewDemand')}</Button>
                  </Link>
                  {offer ? (
                    <Link to={`/offers/${offer.id}`}>
                      <Button size="sm" variant="ghost">
                        {t('match.offer')}
                      </Button>
                    </Link>
                  ) : null}
                  <div className="spacer" />
                  {m.booking_id ? (
                    <Link to={`/bookings/${m.booking_id}`}>
                      <Button size="sm">{t('book.detail')}</Button>
                    </Link>
                  ) : m.status === 'suggested' ? (
                    <>
                      <Button
                        size="sm"
                        variant="danger"
                        loading={answer.isPending}
                        onClick={() => answer.mutate({ id: m.id, action: 'reject' })}
                      >
                        {t('demand.reject')}
                      </Button>
                      <Button
                        size="sm"
                        variant="primary"
                        loading={answer.isPending}
                        onClick={() => answer.mutate({ id: m.id, action: 'accept' })}
                      >
                        {t('demand.accept')}
                      </Button>
                    </>
                  ) : null}
                </div>
              </Card>
            );
          })}
          <Pagination
            total={query.data?.total ?? 0}
            limit={PAGE_SIZE}
            offset={offset}
            onChange={setOffset}
            label={t('prov.matchesTitle')}
          />
        </div>
      )}
    </div>
  );
}
