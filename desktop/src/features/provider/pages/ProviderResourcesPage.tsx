// Resource inventory for the organization: what exists, how many units it sells,
// and the two actions a provider needs on it — retire a unit, delete a resource.

import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useQueryClient } from '@tanstack/react-query';
import { capacityApi } from '@/api/endpoints';
import { useCategories } from '@/features/marketplace/hooks';
import { useI18n } from '@/i18n/index';
import { useToast, toastError } from '@/components/ui/Toast';
import { Badge, Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { EmptyState, ErrorPanel, Loading } from '@/components/ui/States';
import { Modal } from '@/components/ui/Modal';
import { formatDuration } from '@/lib/format';
import type { BadgeTone } from '@/components/ui/Card';
import type { CapacityMode, ResourceStatus } from '@/types/api';

const PAGE_SIZE = 100;

const MODE_KEYS: Record<CapacityMode, 'wizard.modeScheduled' | 'wizard.modeQuantity' | 'wizard.modeOpenEnded'> = {
  scheduled: 'wizard.modeScheduled',
  quantity: 'wizard.modeQuantity',
  open_ended: 'wizard.modeOpenEnded',
};

const MODE_TONES: Record<CapacityMode, BadgeTone> = {
  scheduled: 'info',
  quantity: 'primary',
  open_ended: 'default',
};

const STATUS_KEYS: Record<ResourceStatus, 'offer.status.draft' | 'admin.active' | 'admin.retire'> = {
  draft: 'offer.status.draft',
  active: 'admin.active',
  archived: 'admin.retire',
};

const STATUS_TONES: Record<ResourceStatus, BadgeTone> = {
  draft: 'warning',
  active: 'success',
  archived: 'default',
};

export function ProviderResourcesPage() {
  const { t } = useI18n();
  const toasts = useToast();
  const qc = useQueryClient();
  const categories = useCategories();
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);

  const resources = useQuery({
    queryKey: ['resources'],
    queryFn: () => capacityApi.listMine(PAGE_SIZE, 0),
  });

  const toggleUnit = useMutation({
    mutationFn: ({ id, is_active }: { id: string; is_active: boolean }) =>
      capacityApi.patchDefinition(id, { is_active }),
    onSuccess: () => {
      toasts.success(t('wizard.created'));
      void qc.invalidateQueries({ queryKey: ['resources'] });
    },
    onError: (err) => toastError(toasts, err, t),
  });

  const removeResource = useMutation({
    mutationFn: (id: string) => capacityApi.deleteResource(id),
    onSuccess: () => {
      toasts.success(t('common.deleted'));
      setPendingDelete(null);
      void qc.invalidateQueries({ queryKey: ['resources'] });
      void qc.invalidateQueries({ queryKey: ['offers', 'mine'] });
    },
    onError: (err) => toastError(toasts, err, t),
  });

  const labelFor = (categoryId: string) =>
    categories.data?.items.find((c) => c.id === categoryId)?.label ?? '—';

  const items = resources.data?.items ?? [];
  const doomed = items.find((r) => r.id === pendingDelete) ?? null;

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>{t('prov.resources')}</h1>
          <span className="subtitle">{t('prov.title')}</span>
        </div>
        <Link to="/provider/new">
          <Button variant="primary">{t('nav.wizard')}</Button>
        </Link>
      </div>

      {resources.isPending ? (
        <Loading label={t('common.loading')} />
      ) : resources.isError ? (
        <ErrorPanel
          message={t('err.network_error')}
          onRetry={() => resources.refetch()}
          retryLabel={t('common.retry')}
        />
      ) : items.length === 0 ? (
        <EmptyState
          title={t('prov.noResources')}
          hint={t('prov.noResourcesHint')}
          action={
            <Link to="/provider/new">
              <Button variant="primary">{t('nav.wizard')}</Button>
            </Link>
          }
        />
      ) : (
        <div className="stack">
          {items.map((r) => (
            <Card key={r.id}>
              <div className="row" style={{ justifyContent: 'space-between', gap: 12 }}>
                <div>
                  <h3 style={{ margin: 0 }}>{r.name}</h3>
                  <div className="small muted">
                    {labelFor(r.category_id)}
                    {r.address.city ? ` · ${r.address.city}` : ''}
                    {r.address.country ? `, ${r.address.country}` : ''}
                  </div>
                </div>
                <div className="row row-tight">
                  <Badge tone={MODE_TONES[r.capacity_mode]}>{t(MODE_KEYS[r.capacity_mode])}</Badge>
                  <Badge tone={STATUS_TONES[r.status]}>{t(STATUS_KEYS[r.status])}</Badge>
                  <Button size="sm" variant="danger" onClick={() => setPendingDelete(r.id)}>
                    {t('prov.deleteResource')}
                  </Button>
                </div>
              </div>

              {r.description ? (
                <p className="small muted" style={{ marginBottom: 8 }}>
                  {r.description}
                </p>
              ) : null}

              <h4 style={{ margin: '8px 0 4px' }}>{t('prov.definitions')}</h4>
              {(r.definitions ?? []).length === 0 ? (
                <p className="muted small">{t('common.none')}</p>
              ) : (
                <div className="table-wrap">
                  <table className="table">
                    <thead>
                      <tr>
                        <th>{t('common.name')}</th>
                        <th>{t('wizard.unitLabel')}</th>
                        <th className="text-right">{t('prov.units')}</th>
                        <th className="text-right">{t('wizard.slotDuration')}</th>
                        <th>{t('common.status')}</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {(r.definitions ?? []).map((def) => (
                        <tr key={def.id}>
                          <td>{def.name}</td>
                          <td className="small">{def.unit_label}</td>
                          <td className="text-right mono">
                            {def.min_quantity}–{def.max_quantity}
                          </td>
                          <td className="text-right small">
                            {formatDuration(def.slot_duration_minutes)}
                          </td>
                          <td>
                            <Badge tone={def.is_active ? 'success' : 'default'}>
                              {def.is_active ? t('admin.active') : t('admin.disable')}
                            </Badge>
                          </td>
                          <td className="text-right">
                            <Button
                              size="sm"
                              loading={toggleUnit.isPending}
                              onClick={() =>
                                toggleUnit.mutate({ id: def.id, is_active: !def.is_active })
                              }
                            >
                              {def.is_active ? t('admin.disable') : t('admin.enable')}
                            </Button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          ))}
        </div>
      )}

      {doomed ? (
        <Modal
          title={`${t('prov.deleteResource')}: ${doomed.name}`}
          onClose={() => setPendingDelete(null)}
          footer={
            <>
              <Button onClick={() => setPendingDelete(null)}>{t('common.cancel')}</Button>
              <Button
                variant="danger"
                loading={removeResource.isPending}
                onClick={() => removeResource.mutate(doomed.id)}
              >
                {t('common.delete')}
              </Button>
            </>
          }
        >
          <p className="muted small">{t('prov.deleteWarning')}</p>
        </Modal>
      ) : null}
    </div>
  );
}
