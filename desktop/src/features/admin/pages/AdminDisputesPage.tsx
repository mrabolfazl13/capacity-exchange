// Support's dispute queue. Each row already carries the service, the money and its
// age, so a decision needs no trip to the booking screen — which support cannot open
// anyway (booking detail is tenant-scoped, §4).

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { adminApi, disputeApi } from '@/api/endpoints';
import { useI18n } from '@/i18n/index';
import { useToast, toastError } from '@/components/ui/Toast';
import { Badge, Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { EmptyState, ErrorPanel, Loading } from '@/components/ui/States';
import { Modal } from '@/components/ui/Modal';
import { InputField, SelectField, TextareaField } from '@/components/ui/Field';
import { Pagination } from '@/components/ui/Pagination';
import { formatDateTime, formatMoney, majorToCents } from '@/lib/format';
import type { BadgeTone } from '@/components/ui/Card';
import type { AdminDisputeRow, DisputeKind, DisputeStatus } from '@/types/api';

const PAGE_SIZE = 20;

const KINDS: DisputeKind[] = ['quality', 'no_show', 'payment', 'damage', 'other'];
const STATUSES: DisputeStatus[] = [
  'open', 'under_review', 'resolved_refund', 'resolved_partial', 'resolved_no_fault', 'closed',
];

const STATUS_TONES: Record<DisputeStatus, BadgeTone> = {
  open: 'warning',
  under_review: 'info',
  resolved_refund: 'success',
  resolved_partial: 'success',
  resolved_no_fault: 'default',
  closed: 'default',
};

/** Only these move a live dispute; `open` is never a target of a resolve call. */
const DECISIONS: DisputeStatus[] = [
  'under_review', 'resolved_refund', 'resolved_partial', 'resolved_no_fault', 'closed',
];

const OPEN: DisputeStatus[] = ['open', 'under_review'];

export function AdminDisputesPage() {
  const { t } = useI18n();
  const toasts = useToast();
  const qc = useQueryClient();
  const [status, setStatus] = useState('open');
  const [kind, setKind] = useState('');
  const [oldestFirst, setOldestFirst] = useState(true);
  const [offset, setOffset] = useState(0);
  const [target, setTarget] = useState<AdminDisputeRow | null>(null);

  const query = useQuery({
    queryKey: ['admin', 'disputes', status || null, kind || null, oldestFirst, offset],
    queryFn: () =>
      adminApi.disputes(
        {
          status: status || undefined,
          kind: kind || undefined,
          oldestFirst,
        },
        PAGE_SIZE,
        offset,
      ),
    placeholderData: (prev) => prev,
  });

  const resolve = useMutation({
    mutationFn: (input: {
      id: string;
      status: DisputeStatus;
      note: string;
      refundCents: number | null;
    }) => disputeApi.resolve(input.id, input.status, input.note, input.refundCents),
    onSuccess: () => {
      toasts.success(t('toast.saved'));
      setTarget(null);
      void qc.invalidateQueries({ queryKey: ['admin', 'disputes'] });
      void qc.invalidateQueries({ queryKey: ['disputes'] });
      void qc.invalidateQueries({ queryKey: ['dashboard'] });
      void qc.invalidateQueries({ queryKey: ['notifications'] });
    },
    onError: (err) => toastError(toasts, err, t),
  });

  const items = query.data?.items ?? [];
  // A resolved row stays readable, but the queue only offers a decision on a live one.
  const live = items.filter((d) => OPEN.includes(d.status));

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>{t('admin.disputes')}</h1>
          <span className="subtitle">{t('admin.title')}</span>
        </div>
        <span className="muted small">
          {t('common.results', { count: query.data?.total ?? 0 })}
        </span>
      </div>

      <Card>
        <div className="form-grid">
          <SelectField
            label={t('common.status')}
            value={status}
            placeholder={t('common.all')}
            onChange={(e) => {
              setStatus(e.target.value);
              setOffset(0);
            }}
            options={STATUSES.map((s) => ({ value: s, label: t(`admin.disputeStatus.${s}`) }))}
          />
          <SelectField
            label={t('admin.kind')}
            value={kind}
            placeholder={t('common.all')}
            onChange={(e) => {
              setKind(e.target.value);
              setOffset(0);
            }}
            options={KINDS.map((k) => ({ value: k, label: t(`admin.disputeKind.${k}`) }))}
          />
          <label className="field row row-tight">
            <input
              type="checkbox"
              checked={oldestFirst}
              onChange={(e) => {
                setOldestFirst(e.target.checked);
                setOffset(0);
              }}
            />
            <span>{t('admin.oldestFirst')}</span>
          </label>
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
          {live.length > 0 ? (
            <p className="small muted">{t('common.results', { count: live.length })}</p>
          ) : null}
          {items.map((d) => (
            <DisputeCard
              key={d.id}
              dispute={d}
              busy={resolve.isPending}
              onOpen={() => setTarget(d)}
            />
          ))}
          <Pagination
            total={query.data?.total ?? 0}
            limit={PAGE_SIZE}
            offset={offset}
            onChange={setOffset}
            label={t('admin.disputes')}
          />
        </div>
      )}

      {target ? (
        <ResolveModal
          dispute={target}
          busy={resolve.isPending}
          onClose={() => setTarget(null)}
          onSubmit={(input) => resolve.mutate({ id: target.id, ...input })}
        />
      ) : null}
    </div>
  );
}

function DisputeCard({
  dispute: d,
  busy,
  onOpen,
}: {
  dispute: AdminDisputeRow;
  busy: boolean;
  onOpen: () => void;
}) {
  const { t } = useI18n();
  const open = OPEN.includes(d.status);
  return (
    <Card>
      <div className="row" style={{ justifyContent: 'space-between', gap: 12 }}>
        <div>
          <h3 style={{ margin: 0 }}>{d.offer_title ?? d.org_name ?? d.id.slice(0, 8)}</h3>
          <div className="small muted">
            {d.complainant_name ?? t('common.customer')}
            {d.org_name ? ` · ${d.org_name}` : ''}
            {d.window_start ? ` · ${formatDateTime(d.window_start)}` : ''}
          </div>
        </div>
        <div className="row row-tight">
          <Badge>{t(`admin.disputeKind.${d.kind}`)}</Badge>
          <Badge tone={STATUS_TONES[d.status]}>{t(`admin.disputeStatus.${d.status}`)}</Badge>
          {d.age_hours != null ? (
            <span className="small muted">{t('admin.ageHours', { hours: Math.round(d.age_hours) })}</span>
          ) : null}
        </div>
      </div>

      <p className="small" style={{ margin: '8px 0' }}>
        {d.description}
      </p>

      <div className="kv">
        <span>{t('admin.orderTotal')}</span>
        <span className="mono">{formatMoney(d.order_total_cents ?? 0, 'USD')}</span>
        <span>{t('book.refunded')}</span>
        <span className="mono">{formatMoney(d.order_refunded_cents ?? 0, 'USD')}</span>
        <span>{t('book.payment')}</span>
        <span className="small">{d.order_payment_status ?? '—'}</span>
        <span>{t('common.status')}</span>
        <span className="small">{d.booking_status ?? '—'}</span>
      </div>

      {d.resolution_note ? (
        <div className="stack-tight" style={{ marginTop: 8 }}>
          <span className="small muted">{t('admin.resolution')}</span>
          <p style={{ margin: 0 }} className="small">
            {d.resolution_note}
          </p>
          {d.refund_cents ? (
            <span className="small mono">
              {t('book.refunded')}: {formatMoney(d.refund_cents, 'USD')}
            </span>
          ) : null}
        </div>
      ) : null}

      {open ? (
        <div className="row" style={{ justifyContent: 'flex-end', marginTop: 8 }}>
          <Button size="sm" variant="primary" disabled={busy} onClick={onOpen}>
            {t('admin.resolve')}
          </Button>
        </div>
      ) : null}
    </Card>
  );
}

function ResolveModal({
  dispute: d,
  busy,
  onClose,
  onSubmit,
}: {
  dispute: AdminDisputeRow;
  busy: boolean;
  onClose: () => void;
  onSubmit: (input: { status: DisputeStatus; note: string; refundCents: number | null }) => void;
}) {
  const { t } = useI18n();
  const [decision, setDecision] = useState<DisputeStatus>('resolved_no_fault');
  const [note, setNote] = useState('');
  const [refund, setRefund] = useState('');

  // "only an open dispute can be taken for review" (§5.9) — hide it once it is in review.
  const options = DECISIONS.filter((s) => s !== 'under_review' || d.status === 'open');
  const needsAmount = decision === 'resolved_partial';
  const amountCents = needsAmount ? majorToCents(refund || '0') : 0;
  const ready =
    note.trim().length >= 3 &&
    (!needsAmount || (refund.trim() !== '' && amountCents > 0));

  return (
    <Modal
      title={t('admin.resolve')}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>{t('common.cancel')}</Button>
          <Button
            variant="primary"
            disabled={!ready}
            loading={busy}
            onClick={() =>
              onSubmit({
                status: decision,
                note: note.trim(),
                refundCents: needsAmount ? amountCents : null,
              })
            }
          >
            {t('common.confirm')}
          </Button>
        </>
      }
    >
      <div className="stack-tight">
        <p className="small muted">{d.description.slice(0, 200)}</p>
        <SelectField
          label={t('admin.resolutionStatus')}
          value={decision}
          onChange={(e) => setDecision(e.target.value as DisputeStatus)}
          options={options.map((s) => ({ value: s, label: t(`admin.disputeStatus.${s}`) }))}
        />
        {needsAmount ? (
          <InputField
            label={t('admin.refundCents')}
            inputMode="decimal"
            hint={`${t('book.orderTotal')}: ${formatMoney(d.order_total_cents ?? 0, 'USD')}`}
            value={refund}
            onChange={(e) => setRefund(e.target.value)}
          />
        ) : decision === 'resolved_refund' ? (
          <p className="small muted">{t('book.refunded')} — {formatMoney(Math.max(0, (d.order_total_cents ?? 0) - (d.order_refunded_cents ?? 0)), 'USD')}</p>
        ) : null}
        <TextareaField
          label={t('admin.resolution')}
          rows={4}
          required
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
      </div>
    </Modal>
  );
}
