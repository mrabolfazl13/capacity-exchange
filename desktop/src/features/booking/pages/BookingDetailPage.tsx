// The booking's whole life in one screen: what it is, what happened, and only the actions
// the state machine and the viewer's role allow (logic.ts is the single source for that set).

import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  bookingApi,
  conversationApi,
  disputeApi,
  fulfillmentApi,
  orderApi,
  paymentApi,
  reviewApi,
} from '@/api/endpoints';
import { useAuth } from '@/auth/AuthProvider';
import { useI18n } from '@/i18n/index';
import { useToast, toastError } from '@/components/ui/Toast';
import { Card, Badge, type BadgeTone } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { SelectField, TextareaField } from '@/components/ui/Field';
import { Modal } from '@/components/ui/Modal';
import { EmptyState, ErrorPanel, Loading } from '@/components/ui/States';
import { Pagination } from '@/components/ui/Pagination';
import { BookingStatusBadge } from '@/features/booking/components/BookingStatusBadge';
import { useHoldCountdown } from '@/hooks/useCountdown';
import { allowedBookingActions, refundPctForCancellation } from '@/features/booking/logic';
import { formatDateTime, formatMoney } from '@/lib/format';
import type { BookingStatus, DisputeKind, Order } from '@/types/api';

/** Money and work share one tone scale so a warning always looks the same. */
const STATE_TONES: Record<string, BadgeTone> = {
  paid: 'success',
  succeeded: 'success',
  completed: 'success',
  not_required: 'default',
  unpaid: 'warning',
  pending: 'warning',
  created: 'warning',
  in_progress: 'warning',
  refunded: 'info',
  partially_refunded: 'info',
  failed: 'danger',
  canceled: 'danger',
  no_show: 'danger',
};

const DISPUTE_KINDS: DisputeKind[] = ['quality', 'no_show', 'payment', 'damage', 'other'];

function stateTone(state: string): BadgeTone {
  return STATE_TONES[state] ?? 'default';
}

const TIMELINE_PAGE = 10;

export function BookingDetailPage() {
  const { bookingId } = useParams<{ bookingId: string }>();
  const { t } = useI18n();
  const toasts = useToast();
  const qc = useQueryClient();
  const { user } = useAuth();

  const [dialog, setDialog] = useState<'cancel' | 'review' | 'dispute' | null>(null);
  const [reason, setReason] = useState('');
  const [rating, setRating] = useState('5');
  const [comment, setComment] = useState('');
  const [kind, setKind] = useState<DisputeKind>('quality');
  const [note, setNote] = useState('');
  const [timelineOffset, setTimelineOffset] = useState(0);

  const booking = useQuery({
    queryKey: ['bookings', bookingId],
    queryFn: () => bookingApi.get(bookingId as string),
    enabled: !!bookingId,
  });
  const timeline = useQuery({
    queryKey: ['bookings', bookingId, 'timeline', timelineOffset],
    queryFn: () => bookingApi.timeline(bookingId as string, TIMELINE_PAGE, timelineOffset),
    enabled: !!bookingId,
  });

  const b = booking.data;
  const actions = b ? allowedBookingActions(b, user) : [];
  const isProviderSide =
    !!b && !!user?.active_org_id && user.active_org_id === b.org_id;
  const countdown = useHoldCountdown(b?.status === 'hold' ? b.hold_expires_at : null);

  // Preview only — the server recomputes the refund when the cancel actually lands (§5.6).
  const refundPct = useMemo(() => {
    if (!b) return null;
    const bands = b.offer?.cancellation_policy ?? [];
    if (bands.length === 0) return null;
    return refundPctForCancellation(bands, b.window_start, Date.now());
  }, [b]);

  function invalidate() {
    void qc.invalidateQueries({ queryKey: ['bookings', bookingId] });
    void qc.invalidateQueries({ queryKey: ['bookings', bookingId, 'timeline'] });
    void qc.invalidateQueries({ queryKey: ['dashboard'] });
    void qc.invalidateQueries({ queryKey: ['bookings'] });
  }

  const move = useMutation({
    mutationFn: (action: 'confirm' | 'start' | 'complete') =>
      action === 'confirm'
        ? bookingApi.confirm(b!.id)
        : action === 'start'
          ? bookingApi.start(b!.id)
          : bookingApi.complete(b!.id),
    onSuccess: (_data, action) => {
      invalidate();
      toasts.success(
        action === 'confirm' ? t('prov.confirmed') : action === 'start' ? t('prov.started') : t('prov.completed'),
      );
    },
    onError: (err) => toastError(toasts, err, t),
  });

  /** Order → intent → confirm; settling the payment is what moves the booking to confirmed. */
  const pay = useMutation({
    mutationFn: async () => {
      const order: Order = b!.order ?? (await orderApi.create(b!.id));
      const intent = await paymentApi.createIntent(order.id);
      return paymentApi.confirm(intent.id, `pay-${intent.id}`);
    },
    onSuccess: () => {
      invalidate();
      toasts.success(t('book.paid'));
    },
    onError: (err) => toastError(toasts, err, t),
  });

  const cancel = useMutation({
    mutationFn: () => bookingApi.cancel(b!.id, reason),
    onSuccess: () => {
      setDialog(null);
      setReason('');
      invalidate();
      toasts.success(t('book.cancelled'));
    },
    onError: (err) => toastError(toasts, err, t),
  });

  const review = useMutation({
    mutationFn: () =>
      reviewApi.create({
        booking_id: b!.id,
        fulfillment_id: b!.fulfillment!.id,
        rating: Number(rating),
        comment: comment || null,
      }),
    onSuccess: () => {
      setDialog(null);
      setComment('');
      invalidate();
      toasts.success(t('book.reviewSubmitted'));
    },
    onError: (err) => toastError(toasts, err, t),
  });

  const dispute = useMutation({
    mutationFn: () => disputeApi.create({ booking_id: b!.id, kind, description: note }),
    onSuccess: () => {
      setDialog(null);
      setNote('');
      invalidate();
      toasts.success(t('book.disputeOpened'));
    },
    onError: (err) => toastError(toasts, err, t),
  });

  const addNote = useMutation({
    mutationFn: (body: string) => fulfillmentApi.addNote(b!.fulfillment!.id, body),
    onSuccess: () => {
      invalidate();
      toasts.success(t('book.noteSaved'));
    },
    onError: (err) => toastError(toasts, err, t),
  });

  const thread = useMutation({
    mutationFn: () => conversationApi.create({ kind: 'booking', ref_id: b!.id }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['conversations'] }),
    onError: (err) => toastError(toasts, err, t),
  });

  if (booking.isPending) return <Loading label={t('common.loading')} />;
  if (booking.isError || !b)
    return (
      <ErrorPanel
        message={t('err.not_found')}
        onRetry={() => booking.refetch()}
        retryLabel={t('common.retry')}
      />
    );

  const order = b.order ?? null;
  const fulfillment = b.fulfillment ?? null;
  const events = timeline.data?.items ?? [];

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>{b.offer_title ?? t('book.detail')}</h1>
          <span className="subtitle">
            {[b.org_name, b.resource_name, isProviderSide ? b.customer_name : null]
              .filter(Boolean)
              .join(' · ')}
          </span>
        </div>
        <div className="row row-tight">
          <BookingStatusBadge status={b.status} />
          <Link to="/bookings">
            <Button>{t('common.back')}</Button>
          </Link>
        </div>
      </div>

      <Card>
        <div className="grid grid-cards">
          <div className="kv">
            <span>{t('book.windowStart')}</span>
            <strong>{formatDateTime(b.window_start)}</strong>
            <span>{t('book.windowEnd')}</span>
            <strong>{formatDateTime(b.window_end)}</strong>
            <span>{t('common.quantity')}</span>
            <strong>{b.quantity}</strong>
            <span>{t('offer.unitPrice')}</span>
            <strong>{formatMoney(b.unit_amount_cents, b.currency)}</strong>
            <span>{t('common.total')}</span>
            <strong>{formatMoney(b.total_cents, b.currency)}</strong>
          </div>

          <div className="kv">
            <span>{t('book.payment')}</span>
            <Badge tone={stateTone(b.payment_status)}>{t(`book.pay.${b.payment_status}`)}</Badge>
            {b.offer ? (
              <>
                <span>{t('market.bookingMode')}</span>
                <strong>
                  {b.offer.booking_mode === 'instant'
                    ? t('market.instant')
                    : t('market.requestConfirm')}
                </strong>
              </>
            ) : null}
            {b.hold_expires_at ? (
              <>
                <span>{t('book.holdExpires')}</span>
                <strong>{formatDateTime(b.hold_expires_at)}</strong>
              </>
            ) : null}
            {b.confirmed_at ? (
              <>
                <span>{t('book.confirmedLabel')}</span>
                <strong>{formatDateTime(b.confirmed_at)}</strong>
              </>
            ) : null}
            {b.started_at ? (
              <>
                <span>{t('book.startedAt')}</span>
                <strong>{formatDateTime(b.started_at)}</strong>
              </>
            ) : null}
            {b.completed_at ? (
              <>
                <span>{t('book.completedAt')}</span>
                <strong>{formatDateTime(b.completed_at)}</strong>
              </>
            ) : null}
          </div>

          {order ? (
            <div className="kv">
              <span>{t('book.orderNumber')}</span>
              <strong className="mono">{order.number}</strong>
              <span>{t('common.total')}</span>
              <strong>{formatMoney(order.subtotal_cents, order.currency)}</strong>
              {order.discount_cents > 0 ? (
                <>
                  <span>{t('book.coupon')}</span>
                  <strong>−{formatMoney(order.discount_cents, order.currency)}</strong>
                </>
              ) : null}
              <span>{t('admin.commission')}</span>
              <strong>{formatMoney(order.commission_cents, order.currency)}</strong>
              <span>{t('admin.orderTotal')}</span>
              <strong>{formatMoney(order.total_cents, order.currency)}</strong>
              {order.refunded_cents > 0 ? (
                <>
                  <span>{t('book.refunded')}</span>
                  <strong>{formatMoney(order.refunded_cents, order.currency)}</strong>
                </>
              ) : null}
            </div>
          ) : null}
        </div>

        {b.status === 'hold' && countdown.remainingMs != null ? (
          <p className={countdown.urgent ? 'countdown urgent' : 'countdown'}>
            {countdown.expired
              ? t('book.holdExpired')
              : t('book.holdCountdown', {
                  time: new Date(countdown.remainingMs).toISOString().slice(11, 19),
                })}
          </p>
        ) : null}

        {b.status === 'cancelled' && b.cancel_reason ? (
          <p className="small muted">
            {t('book.cancelInfo')}: {b.cancel_reason}
          </p>
        ) : null}

        <div className="row row-tight" style={{ marginTop: 12, flexWrap: 'wrap' }}>
          {actions.includes('pay') ? (
            <Button variant="primary" loading={pay.isPending} onClick={() => pay.mutate()}>
              {t('book.pay')}
            </Button>
          ) : null}
          {actions.includes('confirm') ? (
            <Button
              variant="primary"
              loading={move.isPending}
              onClick={() => move.mutate('confirm')}
            >
              {isProviderSide ? t('prov.confirm') : t('book.convert')}
            </Button>
          ) : null}
          {actions.includes('start') ? (
            <Button variant="primary" loading={move.isPending} onClick={() => move.mutate('start')}>
              {t('prov.start')}
            </Button>
          ) : null}
          {actions.includes('complete') ? (
            <Button
              variant="primary"
              loading={move.isPending}
              onClick={() => move.mutate('complete')}
            >
              {t('prov.complete')}
            </Button>
          ) : null}
          {actions.includes('cancel') ? (
            <Button variant="danger" onClick={() => setDialog('cancel')}>
              {t('book.cancel')}
            </Button>
          ) : null}
          {actions.includes('review') ? (
            <Button onClick={() => setDialog('review')}>{t('book.rate')}</Button>
          ) : null}
          {actions.includes('dispute') ? (
            <Button variant="ghost" onClick={() => setDialog('dispute')}>
              {t('book.dispute')}
            </Button>
          ) : null}
          <Button variant="ghost" loading={thread.isPending} onClick={() => thread.mutate()}>
            {t('book.message')}
          </Button>
          {thread.data ? (
            <Link to={`/messages/${thread.data.id}`}>
              <Button size="sm">{t('msg.thread')}</Button>
            </Link>
          ) : null}
          <Link to={`/offers/${b.offer_id}`}>
            <Button size="sm" variant="ghost">
              {t('offer.details')}
            </Button>
          </Link>
        </div>

        {actions.includes('cancel') && refundPct != null ? (
          <p className="small muted">
            {t('book.policyPreview')}: {refundPct > 0 ? `${refundPct}%` : t('book.noRefund')}
          </p>
        ) : null}
      </Card>

      {fulfillment ? (
        <Card>
          <div className="row row-tight">
            <h2 style={{ margin: 0 }}>{t('book.fulfillment')}</h2>
            <Badge tone={stateTone(fulfillment.status)}>
              {t(`book.fulfillment.${fulfillment.status}`)}
            </Badge>
          </div>
          {(fulfillment.notes ?? []).length === 0 ? (
            <p className="muted small">{t('common.none')}</p>
          ) : (
            <ul className="stack-tight small">
              {fulfillment.notes.map((n) => (
                <li key={`${n.created_at}-${n.author_id ?? 'system'}`} className="divider">
                  {n.body}
                  <div className="muted">{formatDateTime(n.created_at)}</div>
                </li>
              ))}
            </ul>
          )}
          {isProviderSide && fulfillment.status !== 'completed' ? (
            <div className="row" style={{ alignItems: 'flex-end', gap: 8 }}>
              <div style={{ flex: 1 }}>
                <TextareaField
                  label={t('common.notes')}
                  value={note}
                  placeholder={t('book.notePlaceholder')}
                  onChange={(e) => setNote(e.target.value)}
                />
              </div>
              <Button
                loading={addNote.isPending}
                disabled={note.trim().length === 0}
                onClick={() => {
                  addNote.mutate(note.trim());
                  setNote('');
                }}
              >
                {t('book.addNote')}
              </Button>
            </div>
          ) : null}
        </Card>
      ) : null}

      <Card>
        <h2>{t('book.timeline')}</h2>
        {timeline.isPending ? (
          <Loading label={t('common.loading')} />
        ) : events.length === 0 ? (
          <EmptyState title={t('common.none')} />
        ) : (
          <>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>{t('common.when')}</th>
                    <th>{t('common.status')}</th>
                    <th>{t('common.reason')}</th>
                  </tr>
                </thead>
                <tbody>
                  {events.map((e) => (
                    <tr key={e.id}>
                      <td className="small mono">{formatDateTime(e.created_at)}</td>
                      <td>
                        {e.from_status ? (
                          <span className="small muted">
                            {t(`book.status.${e.from_status as BookingStatus}`)} →{' '}
                          </span>
                        ) : null}
                        <BookingStatusBadge status={e.to_status} />
                      </td>
                      <td className="small">{e.reason ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination
              total={timeline.data?.total ?? 0}
              limit={TIMELINE_PAGE}
              offset={timelineOffset}
              onChange={setTimelineOffset}
              label={t('book.timeline')}
            />
          </>
        )}
      </Card>

      {dialog === 'cancel' ? (
        <Modal
          title={t('book.cancel')}
          onClose={() => setDialog(null)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setDialog(null)}>
                {t('common.close')}
              </Button>
              <Button
                variant="danger"
                loading={cancel.isPending}
                disabled={reason.trim().length < 3}
                onClick={() => cancel.mutate()}
              >
                {t('book.cancel')}
              </Button>
            </>
          }
        >
          <TextareaField
            label={t('book.cancelReason')}
            required
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
          {refundPct != null ? (
            <p className="small muted" style={{ marginTop: 8 }}>
              {t('book.policyPreview')}: {refundPct > 0 ? `${refundPct}%` : t('book.noRefund')}
            </p>
          ) : null}
        </Modal>
      ) : null}

      {dialog === 'review' ? (
        <Modal
          title={t('book.rate')}
          onClose={() => setDialog(null)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setDialog(null)}>
                {t('common.close')}
              </Button>
              <Button variant="primary" loading={review.isPending} onClick={() => review.mutate()}>
                {t('common.submit')}
              </Button>
            </>
          }
        >
          <div className="grid form-grid">
            <SelectField
              label={t('book.reviewRating')}
              value={rating}
              onChange={(e) => setRating(e.target.value)}
              options={[1, 2, 3, 4, 5].map((n) => ({ value: String(n), label: '★'.repeat(n) }))}
            />
          </div>
          <TextareaField
            label={t('book.reviewComment')}
            value={comment}
            onChange={(e) => setComment(e.target.value)}
          />
        </Modal>
      ) : null}

      {dialog === 'dispute' ? (
        <Modal
          title={t('book.dispute')}
          onClose={() => setDialog(null)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setDialog(null)}>
                {t('common.close')}
              </Button>
              <Button
                variant="primary"
                loading={dispute.isPending}
                disabled={note.trim().length < 10}
                onClick={() => dispute.mutate()}
              >
                {t('common.submit')}
              </Button>
            </>
          }
        >
          <div className="grid form-grid">
            <SelectField
              label={t('book.disputeKind')}
              value={kind}
              onChange={(e) => setKind(e.target.value as DisputeKind)}
              options={DISPUTE_KINDS.map((k) => ({ value: k, label: t(`admin.disputeKind.${k}`) }))}
            />
          </div>
          <TextareaField
            label={t('book.disputeDescription')}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            hint={t('common.required')}
          />
        </Modal>
      ) : null}
    </div>
  );
}
