// Promotions: coupons carry the code a buyer types, campaigns are automatic and
// have none (§5.6). Discount shape is validated here in the same two forms the
// server accepts, so a mis-typed config never reaches a 422.

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { adminApi } from '@/api/endpoints';
import { useI18n } from '@/i18n/index';
import { useToast, toastError } from '@/components/ui/Toast';
import { Badge, Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { EmptyState, ErrorPanel, Loading } from '@/components/ui/States';
import { Modal } from '@/components/ui/Modal';
import { InputField, SelectField } from '@/components/ui/Field';
import { Pagination } from '@/components/ui/Pagination';
import { formatDateTime, formatMoney, isoToLocalInput, localInputToIso, majorToCents } from '@/lib/format';
import type { BadgeTone } from '@/components/ui/Card';
import type { PromoStatus, Promotion, PromotionKind } from '@/types/api';

const PAGE_SIZE = 20;
const KINDS: PromotionKind[] = ['coupon', 'campaign'];
const STATUSES: PromoStatus[] = ['draft', 'active', 'expired', 'disabled'];

const STATUS_TONES: Record<PromoStatus, BadgeTone> = {
  draft: 'warning',
  active: 'success',
  expired: 'default',
  disabled: 'danger',
};

/** Only what an operator may move a live promotion to (§5.6 patch contract). */
const NEXT_STATUSES: ('draft' | 'active' | 'disabled')[] = ['draft', 'active', 'disabled'];

type DiscountKind = 'pct' | 'fixed';

export function AdminPromotionsPage() {
  const { t } = useI18n();
  const toasts = useToast();
  const qc = useQueryClient();
  const [q, setQ] = useState('');
  const [status, setStatus] = useState('');
  const [kind, setKind] = useState('');
  const [offset, setOffset] = useState(0);
  const [creating, setCreating] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);

  const query = useQuery({
    queryKey: ['admin', 'promotions', q, status, kind, offset],
    queryFn: () =>
      adminApi.promotions(
        { q: q || undefined, status: status || undefined, kind: kind || undefined },
        PAGE_SIZE,
        offset,
      ),
    placeholderData: (prev) => prev,
  });

  const change = useMutation({
    mutationFn: ({ id, status: s }: { id: string; status: 'draft' | 'active' | 'disabled' }) =>
      adminApi.patchPromotion(id, { status: s }),
    onSuccess: () => {
      toasts.success(t('toast.saved'));
      void qc.invalidateQueries({ queryKey: ['admin', 'promotions'] });
    },
    onError: (err) => toastError(toasts, err, t),
  });

  const items = query.data?.items ?? [];

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>{t('admin.promotions')}</h1>
          <span className="subtitle">{t('admin.title')}</span>
        </div>
        <Button variant="primary" onClick={() => setCreating(true)}>
          {t('admin.newPromotion')}
        </Button>
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
            placeholder={`${t('common.name')} / ${t('admin.code')}`}
          />
          <SelectField
            label={t('common.status')}
            value={status}
            placeholder={t('common.all')}
            onChange={(e) => {
              setStatus(e.target.value);
              setOffset(0);
            }}
            options={STATUSES.map((s) => ({ value: s, label: t(`admin.promoStatus.${s}`) }))}
          />
          <SelectField
            label={t('admin.kind')}
            value={kind}
            placeholder={t('common.all')}
            onChange={(e) => {
              setKind(e.target.value);
              setOffset(0);
            }}
            options={KINDS.map((k) => ({ value: k, label: t(`admin.promoKind.${k}`) }))}
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
        <div className="stack">
          {items.map((p) => (
            <PromotionRow
              key={p.id}
              promo={p}
              busy={change.isPending}
              expanded={expanded === p.id}
              onToggle={() => setExpanded(expanded === p.id ? null : p.id)}
              onChange={(s) => change.mutate({ id: p.id, status: s })}
            />
          ))}
          <Pagination
            total={query.data?.total ?? 0}
            limit={PAGE_SIZE}
            offset={offset}
            onChange={setOffset}
            label={t('admin.promotions')}
          />
        </div>
      )}

      {creating ? (
        <NewPromotionModal onClose={() => setCreating(false)} />
      ) : null}
    </div>
  );
}

type DiscountConfig = { type?: string; bp?: number; cents?: number; currency?: string };

function discountOf(p: Promotion): DiscountConfig {
  return (p.discount_config ?? {}) as DiscountConfig;
}

function discountSummary(p: Promotion): string {
  const c = discountOf(p);
  if (c.type === 'pct' && c.bp != null) return `${(c.bp / 100).toFixed(0)}%`;
  if (c.type === 'fixed' && c.cents != null) return formatMoney(c.cents, c.currency ?? 'USD');
  return '—';
}

function PromotionRow({
  promo: p,
  busy,
  expanded,
  onToggle,
  onChange,
}: {
  promo: Promotion;
  busy: boolean;
  expanded: boolean;
  onToggle: () => void;
  onChange: (status: 'draft' | 'active' | 'disabled') => void;
}) {
  const { t } = useI18n();
  return (
    <Card>
      <div className="row" style={{ justifyContent: 'space-between', gap: 12 }}>
        <div>
          <h3 style={{ margin: 0 }}>{p.name}</h3>
          <div className="small muted mono">
            {p.code ? `${p.code} · ` : ''}
            {discountSummary(p)}
            {p.min_order_cents > 0
              ? ` · ${t('admin.minOrder')} ${formatMoney(p.min_order_cents, discountOf(p).currency ?? 'USD')}`
              : ''}
          </div>
        </div>
        <div className="row row-tight">
          <Badge>{t(`admin.promoKind.${p.kind}`)}</Badge>
          <Badge tone={STATUS_TONES[p.status]}>{t(`admin.promoStatus.${p.status}`)}</Badge>
          <span className="small mono">
            {p.used_count}
            {p.usage_limit != null ? `/${p.usage_limit}` : ''} {t('admin.used')}
          </span>
        </div>
      </div>

      <div className="kv" style={{ marginTop: 8 }}>
        <span>{t('admin.startsAt')}</span>
        <span className="small mono">{formatDateTime(p.starts_at)}</span>
        <span>{t('admin.endsAt')}</span>
        <span className="small mono">{formatDateTime(p.ends_at)}</span>
        <span>{t('admin.perUserLimit')}</span>
        <span className="mono">{p.per_user_limit ?? t('common.none')}</span>
      </div>

      <div className="row" style={{ marginTop: 8 }}>
        <Button size="sm" variant="ghost" onClick={onToggle}>
          {t('admin.redemptions')}
        </Button>
        <div className="spacer" />
        {NEXT_STATUSES.filter((s) => s !== p.status).map((s) => (
          <Button
            key={s}
            size="sm"
            variant={s === 'active' ? 'primary' : s === 'disabled' ? 'danger' : 'default'}
            disabled={busy}
            onClick={() => onChange(s)}
          >
            {s === 'active' ? t('admin.enable') : s === 'disabled' ? t('admin.disable') : t(`admin.promoStatus.${s}`)}
          </Button>
        ))}
      </div>

      {expanded ? <Redemptions id={p.id} currency={discountOf(p).currency} /> : null}
    </Card>
  );
}

function Redemptions({ id, currency }: { id: string; currency?: string }) {
  const { t } = useI18n();
  const query = useQuery({
    queryKey: ['admin', 'promotions', id, 'redemptions'],
    queryFn: () => adminApi.promotionRedemptions(id, 20, 0),
  });

  if (query.isPending) return <Loading label={t('common.loading')} />;
  if (query.isError)
    return (
      <ErrorPanel
        message={t('err.network_error')}
        onRetry={() => query.refetch()}
        retryLabel={t('common.retry')}
      />
    );
  const items = query.data?.items ?? [];
  if (items.length === 0) return <p className="muted small">{t('admin.noRedemptions')}</p>;

  return (
    <div className="table-wrap" style={{ marginTop: 8 }}>
      <table className="table">
        <thead>
          <tr>
            <th>{t('common.date')}</th>
            <th>{t('common.customer')}</th>
            <th>{t('book.order')}</th>
            <th className="text-right">{t('admin.discount')}</th>
          </tr>
        </thead>
        <tbody>
          {items.map((r) => (
            <tr key={r.id}>
              <td className="small mono">{formatDateTime(r.redeemed_at)}</td>
              <td>
                {r.user_name ?? '—'}
                <div className="small muted mono">{r.user_email}</div>
              </td>
              <td className="small mono">{r.order_number ?? r.order_id?.slice(0, 8) ?? '—'}</td>
              <td className="text-right mono">{formatMoney(r.discount_cents, currency ?? 'USD')}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function NewPromotionModal({ onClose }: { onClose: () => void }) {
  const { t } = useI18n();
  const toasts = useToast();
  const qc = useQueryClient();
  const [name, setName] = useState('');
  const [kind, setKind] = useState<PromotionKind>('coupon');
  const [code, setCode] = useState('');
  const [discountKind, setDiscountKind] = useState<DiscountKind>('pct');
  const [percent, setPercent] = useState('10');
  const [fixed, setFixed] = useState('');
  const [currency, setCurrency] = useState('USD');
  const [minOrder, setMinOrder] = useState('');
  const [usageLimit, setUsageLimit] = useState('');
  const [perUserLimit, setPerUserLimit] = useState('');
  const [startsAt, setStartsAt] = useState(isoToLocalInput(new Date().toISOString()));
  const [endsAt, setEndsAt] = useState(
    isoToLocalInput(new Date(Date.now() + 30 * 86_400_000).toISOString()),
  );

  const create = useMutation({
    mutationFn: () => {
      const start = localInputToIso(startsAt);
      const end = localInputToIso(endsAt);
      const discount_config =
        discountKind === 'pct'
          ? { type: 'pct', bp: Math.round(Math.min(100, Math.max(0, Number(percent))) * 100) }
          : { type: 'fixed', cents: majorToCents(fixed || '0'), currency: currency.trim().toUpperCase() };
      return adminApi.createPromotion({
        name: name.trim(),
        kind,
        code: kind === 'coupon' ? code.trim().toUpperCase() : null,
        discount_config,
        min_order_cents: minOrder ? majorToCents(minOrder) : 0,
        usage_limit: toIntOrNull(usageLimit),
        per_user_limit: toIntOrNull(perUserLimit),
        starts_at: start,
        ends_at: end,
        status: 'draft',
      });
    },
    onSuccess: () => {
      toasts.success(t('toast.created'));
      onClose();
      void qc.invalidateQueries({ queryKey: ['admin', 'promotions'] });
    },
    onError: (err) => toastError(toasts, err, t),
  });

  const start = localInputToIso(startsAt);
  const end = localInputToIso(endsAt);
  const ready =
    name.trim().length >= 2 &&
    (kind === 'campaign' || code.trim().length >= 3) &&
    !!start &&
    !!end &&
    end > start &&
    (discountKind === 'pct'
      ? Number.isFinite(Number(percent)) && Number(percent) >= 0 && Number(percent) <= 100
      : majorToCents(fixed || '0') > 0) &&
    (currency.trim().length === 3 || discountKind === 'pct');

  return (
    <Modal
      title={t('admin.newPromotion')}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>{t('common.cancel')}</Button>
          <Button variant="primary" disabled={!ready || create.isPending} loading={create.isPending} onClick={() => create.mutate()}>
            {t('common.create')}
          </Button>
        </>
      }
    >
      <div className="form-grid">
        <InputField label={t('common.name')} required value={name} onChange={(e) => setName(e.target.value)} />
        <SelectField
          label={t('admin.kind')}
          value={kind}
          onChange={(e) => setKind(e.target.value as PromotionKind)}
          options={KINDS.map((k) => ({ value: k, label: t(`admin.promoKind.${k}`) }))}
        />
        {kind === 'coupon' ? (
          <InputField
            label={t('admin.code')}
            required
            maxLength={64}
            value={code}
            onChange={(e) => setCode(e.target.value.toUpperCase())}
          />
        ) : null}
        <SelectField
          label={t('admin.discount')}
          value={discountKind}
          onChange={(e) => setDiscountKind(e.target.value as DiscountKind)}
          options={[
            { value: 'pct', label: t('admin.discountPct') },
            { value: 'fixed', label: t('admin.discountCents') },
          ]}
        />
        {discountKind === 'pct' ? (
          <InputField
            label={`${t('admin.discountValue')} (%)`}
            inputMode="numeric"
            value={percent}
            onChange={(e) => setPercent(e.target.value)}
          />
        ) : (
          <>
            <InputField
              label={t('admin.discountValue')}
              inputMode="decimal"
              value={fixed}
              onChange={(e) => setFixed(e.target.value)}
            />
            <InputField
              label={t('common.currency')}
              maxLength={3}
              value={currency}
              onChange={(e) => setCurrency(e.target.value.toUpperCase())}
            />
          </>
        )}
        <InputField
          label={t('admin.minOrder')}
          inputMode="decimal"
          value={minOrder}
          onChange={(e) => setMinOrder(e.target.value)}
        />
        <InputField
          label={t('admin.usageLimit')}
          inputMode="numeric"
          value={usageLimit}
          onChange={(e) => setUsageLimit(e.target.value)}
        />
        <InputField
          label={t('admin.perUserLimit')}
          inputMode="numeric"
          value={perUserLimit}
          onChange={(e) => setPerUserLimit(e.target.value)}
        />
        <InputField
          label={t('admin.startsAt')}
          type="datetime-local"
          required
          value={startsAt}
          onChange={(e) => setStartsAt(e.target.value)}
        />
        <InputField
          label={t('admin.endsAt')}
          type="datetime-local"
          required
          value={endsAt}
          onChange={(e) => setEndsAt(e.target.value)}
        />
      </div>
    </Modal>
  );
}

function toIntOrNull(value: string): number | null {
  const n = Number.parseInt(value, 10);
  return Number.isFinite(n) && n >= 1 ? n : null;
}
