// Reverse marketplace: a customer posts what they need and the scorer proposes offers back.
// Creating a demand is the only write here; answering proposals belongs to the provider.

import { useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { demandApi } from '@/api/endpoints';
import { useCategories } from '@/features/marketplace/hooks';
import { useI18n } from '@/i18n/index';
import { useToast, toastError } from '@/components/ui/Toast';
import { Card, Badge } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { InputField, SelectField, TextareaField } from '@/components/ui/Field';
import { EmptyState, ErrorPanel, Loading } from '@/components/ui/States';
import { Pagination } from '@/components/ui/Pagination';
import { formatMoney, formatDate, localInputToIso, majorToCents } from '@/lib/format';
import type { DemandStatus } from '@/types/api';

const PAGE_SIZE = 20;
const STATUSES: DemandStatus[] = ['open', 'matched', 'closed', 'cancelled'];

const EMPTY_FORM = {
  description: '',
  category_id: '',
  quantity: '1',
  city: '',
  budget_min: '',
  budget_max: '',
  start: '',
  end: '',
};

export function DemandsPage() {
  const { t } = useI18n();
  const toasts = useToast();
  const qc = useQueryClient();
  const categories = useCategories();

  const [status, setStatus] = useState<string>('');
  const [offset, setOffset] = useState(0);
  const [form, setForm] = useState(EMPTY_FORM);
  const [open, setOpen] = useState(false);

  const list = useQuery({
    queryKey: ['demands', 'mine', status, offset],
    queryFn: () => demandApi.mine(status || undefined, PAGE_SIZE, offset),
  });

  const create = useMutation({
    mutationFn: () =>
      demandApi.create({
        description: form.description,
        category_id: form.category_id || null,
        quantity: Number(form.quantity) || 1,
        address: form.city ? { city: form.city } : null,
        desired_start: localInputToIso(form.start),
        desired_end: localInputToIso(form.end),
        budget_min_cents: form.budget_min ? majorToCents(form.budget_min) : null,
        budget_max_cents: form.budget_max ? majorToCents(form.budget_max) : null,
      }),
    onSuccess: () => {
      setOpen(false);
      setForm(EMPTY_FORM);
      void qc.invalidateQueries({ queryKey: ['demands'] });
      list.refetch();
      toasts.success(t('demand.created'));
    },
    onError: (err) => toastError(toasts, err, t),
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    create.mutate();
  }

  const items = list.data?.items ?? [];

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>{t('demand.title')}</h1>
          <span className="subtitle">{t('demand.emptyHint')}</span>
        </div>
        <Button variant="primary" onClick={() => setOpen((v) => !v)}>
          {t('demand.new')}
        </Button>
      </div>

      {open ? (
        <Card>
          <form className="grid form-grid" onSubmit={onSubmit} aria-label={t('demand.new')}>
            <div style={{ gridColumn: '1 / -1' }}>
              <TextareaField
                label={t('demand.description')}
                required
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
                hint={t('common.required')}
              />
            </div>
            <SelectField
              label={t('demand.category')}
              value={form.category_id}
              placeholder={t('common.all')}
              onChange={(e) => setForm({ ...form, category_id: e.target.value })}
              options={(categories.data?.items ?? []).map((c) => ({
                value: c.id,
                label: c.label,
              }))}
            />
            <InputField
              label={t('demand.quantity')}
              type="number"
              min={1}
              value={form.quantity}
              onChange={(e) => setForm({ ...form, quantity: e.target.value })}
            />
            <InputField
              label={t('demand.city')}
              value={form.city}
              onChange={(e) => setForm({ ...form, city: e.target.value })}
            />
            <InputField
              label={t('demand.desiredStart')}
              type="datetime-local"
              value={form.start}
              onChange={(e) => setForm({ ...form, start: e.target.value })}
            />
            <InputField
              label={t('demand.desiredEnd')}
              type="datetime-local"
              value={form.end}
              onChange={(e) => setForm({ ...form, end: e.target.value })}
            />
            <InputField
              label={t('demand.budgetMin')}
              type="number"
              min={0}
              step="0.01"
              value={form.budget_min}
              onChange={(e) => setForm({ ...form, budget_min: e.target.value })}
            />
            <InputField
              label={t('demand.budgetMax')}
              type="number"
              min={0}
              step="0.01"
              value={form.budget_max}
              onChange={(e) => setForm({ ...form, budget_max: e.target.value })}
            />
            <div className="row row-tight" style={{ alignItems: 'flex-end' }}>
              <Button type="submit" variant="primary" loading={create.isPending}>
                {t('demand.post')}
              </Button>
              <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
                {t('common.cancel')}
              </Button>
            </div>
          </form>
        </Card>
      ) : null}

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
            options={STATUSES.map((s) => ({ value: s, label: t(`demand.status.${s}`) }))}
          />
        </div>
      </Card>

      {list.isPending ? (
        <Loading label={t('common.loading')} />
      ) : list.isError ? (
        <ErrorPanel
          message={t('err.network_error')}
          onRetry={() => list.refetch()}
          retryLabel={t('common.retry')}
        />
      ) : items.length === 0 ? (
        <EmptyState title={t('demand.empty')} hint={t('demand.emptyHint')} />
      ) : (
        <Card>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>{t('common.description')}</th>
                  <th>{t('common.status')}</th>
                  <th className="text-right">{t('common.quantity')}</th>
                  <th>{t('common.date')}</th>
                  <th className="text-right">{t('demand.matches')}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {items.map((d) => (
                  <tr key={d.id}>
                    <td>
                      <div className="small">{d.description.slice(0, 120)}</div>
                      <div className="small muted">
                        {d.category_label ?? ''}
                        {d.address?.city ? ` · ${d.address.city}` : ''}
                        {d.budget_max_cents != null
                          ? ` · ≤ ${formatMoney(d.budget_max_cents, 'USD')}`
                          : ''}
                      </div>
                    </td>
                    <td>
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
                    </td>
                    <td className="text-right mono">{d.quantity}</td>
                    <td className="small">{formatDate(d.desired_start ?? d.created_at)}</td>
                    <td className="text-right mono">{d.match_count ?? 0}</td>
                    <td className="text-right">
                      <Link to={`/demands/${d.id}`}>
                        <Button size="sm">{t('demand.view')}</Button>
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination
            total={list.data?.total ?? 0}
            limit={PAGE_SIZE}
            offset={offset}
            onChange={setOffset}
            label={t('demand.title')}
          />
        </Card>
      )}
    </div>
  );
}
