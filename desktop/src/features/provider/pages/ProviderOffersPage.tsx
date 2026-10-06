// The provider's own listings, including drafts that the marketplace does not
// show. Status transitions are the only writes here: publish, pause, close.

import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { offerApi } from '@/api/endpoints';
import { useI18n } from '@/i18n/index';
import { useToast, toastError } from '@/components/ui/Toast';
import { Badge, Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { EmptyState, ErrorPanel, Loading } from '@/components/ui/States';
import { SelectField } from '@/components/ui/Field';
import { Pagination } from '@/components/ui/Pagination';
import { formatMoney } from '@/lib/format';
import type { BadgeTone } from '@/components/ui/Card';
import type { OfferStatus } from '@/types/api';

const PAGE_SIZE = 20;

const STATUS_TONES: Record<OfferStatus, BadgeTone> = {
  draft: 'warning',
  published: 'success',
  paused: 'info',
  closed: 'default',
};

const FILTERS: OfferStatus[] = ['draft', 'published', 'paused', 'closed'];

export function ProviderOffersPage() {
  const { t } = useI18n();
  const toasts = useToast();
  const qc = useQueryClient();
  const [status, setStatus] = useState('');
  const [offset, setOffset] = useState(0);

  const query = useQuery({
    queryKey: ['offers', 'mine', status || null, offset],
    queryFn: () =>
      offerApi.listMine(PAGE_SIZE, offset, (status || undefined) as OfferStatus | undefined),
    placeholderData: (prev) => prev,
  });

  const move = useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'publish' | 'pause' | 'close' }) =>
      action === 'publish'
        ? offerApi.publish(id)
        : action === 'pause'
          ? offerApi.pause(id)
          : offerApi.close(id),
    onSuccess: () => {
      toasts.success(t('offer.stateChanged'));
      void qc.invalidateQueries({ queryKey: ['offers'] });
    },
    onError: (err) => toastError(toasts, err, t),
  });

  const items = query.data?.items ?? [];

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>{t('prov.myOffers')}</h1>
          <span className="subtitle">{t('prov.title')}</span>
        </div>
        <div className="row row-tight">
          <Link to="/provider/matches">
            <Button>{t('nav.providerMatches')}</Button>
          </Link>
          <Link to="/provider/new">
            <Button variant="primary">{t('nav.wizard')}</Button>
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
            options={FILTERS.map((s) => ({ value: s, label: t(`offer.status.${s}`) }))}
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
        <EmptyState
          title={t('prov.noOffers')}
          hint={t('prov.noResourcesHint')}
          action={
            <Link to="/provider/new">
              <Button variant="primary">{t('nav.wizard')}</Button>
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
                  <th>{t('market.bookingMode')}</th>
                  <th className="text-right">{t('offer.unitPrice')}</th>
                  <th className="text-right">{t('common.rating')}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {items.map((o) => (
                  <tr key={o.id}>
                    <td>
                      <Link to={`/offers/${o.id}`}>
                        <strong>{o.title}</strong>
                      </Link>
                      <div className="small muted">
                        {[o.resource_name, o.definition_name, o.city]
                          .filter(Boolean)
                          .join(' · ')}
                      </div>
                    </td>
                    <td>
                      <Badge tone={STATUS_TONES[o.status]}>{t(`offer.status.${o.status}`)}</Badge>
                    </td>
                    <td className="small">
                      {o.booking_mode === 'instant' ? t('market.instant') : t('market.requestConfirm')}
                    </td>
                    <td className="text-right mono">
                      {formatMoney(o.unit_amount_cents, o.currency)}
                      <span className="small muted"> / {o.unit_label ?? 'unit'}</span>
                    </td>
                    <td className="text-right mono">
                      {o.rating_avg != null
                        ? `★ ${o.rating_avg.toFixed(1)} (${o.rating_count ?? 0})`
                        : '—'}
                    </td>
                    <td className="text-right">
                      <div className="row row-tight" style={{ justifyContent: 'flex-end' }}>
                        {o.status === 'draft' || o.status === 'paused' ? (
                          <Button
                            size="sm"
                            variant="primary"
                            loading={move.isPending}
                            onClick={() => move.mutate({ id: o.id, action: 'publish' })}
                          >
                            {t('offer.publish')}
                          </Button>
                        ) : null}
                        {o.status === 'published' ? (
                          <Button
                            size="sm"
                            loading={move.isPending}
                            onClick={() => move.mutate({ id: o.id, action: 'pause' })}
                          >
                            {t('offer.pause')}
                          </Button>
                        ) : null}
                        {o.status !== 'closed' ? (
                          <Button
                            size="sm"
                            variant="danger"
                            loading={move.isPending}
                            onClick={() => move.mutate({ id: o.id, action: 'close' })}
                          >
                            {t('offer.close')}
                          </Button>
                        ) : null}
                      </div>
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
            label={t('prov.myOffers')}
          />
        </Card>
      )}
    </div>
  );
}
