// Capacity taxonomy. Labels are editable and a category is retired rather than
// deleted, so offers and demands keep the row they were created against (§5.2).

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { adminApi } from '@/api/endpoints';
import { useI18n } from '@/i18n/index';
import { useToast, toastError } from '@/components/ui/Toast';
import { Badge, Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { EmptyState, ErrorPanel, Loading } from '@/components/ui/States';
import { InputField } from '@/components/ui/Field';
import type { CapacityCategory } from '@/types/api';

export function AdminCategoriesPage() {
  const { t } = useI18n();
  const toasts = useToast();
  const qc = useQueryClient();
  const [edits, setEdits] = useState<Record<string, string>>({});

  const query = useQuery({
    queryKey: ['admin', 'categories'],
    queryFn: () => adminApi.categories(),
  });

  const save = useMutation({
    mutationFn: ({ id, label, is_active }: { id: string; label?: string; is_active?: boolean }) =>
      adminApi.patchCategory(id, { label, is_active }),
    onSuccess: () => {
      toasts.success(t('toast.saved'));
      setEdits({});
      void qc.invalidateQueries({ queryKey: ['admin', 'categories'] });
      void qc.invalidateQueries({ queryKey: ['categories'] });
    },
    onError: (err) => toastError(toasts, err, t),
  });

  const items = query.data?.items ?? [];

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>{t('admin.categories')}</h1>
          <span className="subtitle">{t('admin.title')}</span>
        </div>
        <span className="muted small">{t('common.results', { count: items.length })}</span>
      </div>

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
                  <th>{t('common.label')}</th>
                  <th>{t('admin.categoryKey')}</th>
                  <th>{t('common.status')}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {items.map((c) => (
                  <CategoryRow
                    key={c.id}
                    category={c}
                    busy={save.isPending}
                    edit={edits[c.id]}
                    onEdit={(value) => setEdits({ ...edits, [c.id]: value })}
                    onSave={(input) => save.mutate(input)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}

function CategoryRow({
  category: c,
  busy,
  edit,
  onEdit,
  onSave,
}: {
  category: CapacityCategory;
  busy: boolean;
  edit?: string;
  onEdit: (value: string) => void;
  onSave: (input: { id: string; label?: string; is_active?: boolean }) => void;
}) {
  const { t } = useI18n();
  const current = edit ?? c.label;
  const renamed = current.trim() !== c.label && current.trim().length >= 1;

  return (
    <tr>
      <td style={{ minWidth: 260 }}>
        <InputField
          label={t('common.label')}
          value={current}
          onChange={(e) => onEdit(e.target.value)}
        />
      </td>
      <td className="mono small">{c.key}</td>
      <td>
        <Badge tone={c.is_active ? 'success' : 'default'}>
          {c.is_active ? t('admin.active') : t('admin.retire')}
        </Badge>
      </td>
      <td className="text-right">
        <div className="row row-tight" style={{ justifyContent: 'flex-end' }}>
          {renamed ? (
            <Button
              size="sm"
              variant="primary"
              loading={busy}
              onClick={() => onSave({ id: c.id, label: current.trim() })}
            >
              {t('common.save')}
            </Button>
          ) : null}
          <Button
            size="sm"
            variant={c.is_active ? 'danger' : 'default'}
            loading={busy}
            onClick={() => onSave({ id: c.id, is_active: !c.is_active })}
          >
            {c.is_active ? t('admin.retire') : t('admin.restoreCategory')}
          </Button>
        </div>
      </td>
    </tr>
  );
}
