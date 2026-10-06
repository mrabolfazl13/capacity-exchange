// The checkout path, one step per real call: hold → booking → order → payment intent
// → confirm. Nothing here simulates success; each step advances on the server's answer.

import { useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery } from '@tanstack/react-query';
import { bookingApi, offerApi, orderApi, paymentApi } from '@/api/endpoints';
import { ApiError } from '@/api/client';
import { useI18n } from '@/i18n/index';
import { useToast, toastError } from '@/components/ui/Toast';
import { Card, Badge } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { InputField } from '@/components/ui/Field';
import { Skeleton, ErrorPanel } from '@/components/ui/States';
import { useHoldCountdown } from '@/hooks/useCountdown';
import { describeError, holdConflictCode, holdConflictKey } from '@/api/errors';
import { formatMoney, isoToLocalInput, localInputToIso } from '@/lib/format';
import { validateHoldRequest } from '@/features/booking/logic';
import type { Booking, Order } from '@/types/api';

function nextSlot(): { start: string; end: string } {
  // Two days out, on the hour: inside every offer's lead time and never in the past.
  const start = new Date(Date.now() + 2 * 86_400_000);
  start.setMinutes(0, 0, 0);
  const end = new Date(start.getTime() + 2 * 3_600_000);
  return { start: isoToLocalInput(start.toISOString()), end: isoToLocalInput(end.toISOString()) };
}

export function BookingFlowPage() {
  const { offerId } = useParams<{ offerId: string }>();
  const { t } = useI18n();
  const toasts = useToast();
  const navigate = useNavigate();

  const initial = useMemo(nextSlot, []);
  const [form, setForm] = useState({ ...initial, quantity: 1, coupon: '' });
  const [booking, setBooking] = useState<Booking | null>(null);
  const [order, setOrder] = useState<Order | null>(null);
  const [paymentId, setPaymentId] = useState<string | null>(null);

  const offer = useQuery({
    queryKey: ['offers', offerId],
    queryFn: () => offerApi.get(offerId as string),
    enabled: !!offerId,
  });

  const startIso = localInputToIso(form.start);
  const endIso = localInputToIso(form.end);
  const clientErrors = useMemo(
    () =>
      offer.data && startIso && endIso
        ? validateHoldRequest(offer.data, startIso, endIso, form.quantity)
        : [],
    [offer.data, startIso, endIso, form.quantity],
  );

  const hold = useMutation({
    mutationFn: () =>
      bookingApi.hold(
        {
          offer_id: offerId as string,
          window_start: startIso as string,
          window_end: endIso as string,
          quantity: form.quantity,
        },
        // The key is the retry identity: re-clicking must not take capacity twice.
        `hold-${offerId}-${startIso}-${endIso}-${form.quantity}`,
      ),
    onSuccess: (b) => setBooking(b),
    onError: (err) => {
      const key = holdConflictKey(err);
      if (key) toasts.error(t(key));
      else toastError(toasts, err, t);
    },
  });

  const convert = useMutation({
    mutationFn: () => bookingApi.create({ hold_id: (booking as Booking).id }, `book-${booking?.id}`),
    onSuccess: (b) => {
      setBooking(b);
      toasts.success(t('toast.created'));
    },
    onError: (err) => toastError(toasts, err, t),
  });

  const openOrder = useMutation({
    mutationFn: () => orderApi.create((booking as Booking).id, form.coupon || null),
    onSuccess: (o) => {
      setOrder(o);
      if (o.discount_cents > 0) toasts.success(t('book.couponApplied'));
      else if (form.coupon) toasts.error(t('book.couponIgnored'));
    },
    onError: (err) => toastError(toasts, err, t),
  });

  const pay = useMutation({
    mutationFn: async () => {
      const intent = await paymentApi.createIntent((order as Order).id);
      setPaymentId(intent.id);
      return paymentApi.confirm(intent.id, `pay-${intent.id}`);
    },
    onSuccess: () => {
      toasts.success(t('book.paid'));
      if (booking) navigate(`/bookings/${booking.id}`);
    },
    onError: (err) => toastError(toasts, err, t),
  });

  const countdown = useHoldCountdown(booking?.hold_expires_at);

  if (offer.isPending) return <Skeleton lines={6} />;
  if (offer.isError)
    return (
      <ErrorPanel
        message={t('err.not_found')}
        onRetry={() => offer.refetch()}
        retryLabel={t('common.retry')}
      />
    );

  const o = offer.data;
  const estimated = o.unit_amount_cents * form.quantity;

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>{t('book.title')}</h1>
          <span className="subtitle">{o.title}</span>
        </div>
        <Link to={`/offers/${o.id}`}>
          <Button>{t('common.back')}</Button>
        </Link>
      </div>

      <Card>
        <div className="grid form-grid">
          <InputField
            label={t('book.windowStart')}
            type="datetime-local"
            value={form.start}
            disabled={booking !== null}
            error={clientErrors[0]}
            onChange={(e) => setForm({ ...form, start: e.target.value })}
          />
          <InputField
            label={t('book.windowEnd')}
            type="datetime-local"
            value={form.end}
            disabled={booking !== null}
            onChange={(e) => setForm({ ...form, end: e.target.value })}
          />
          <InputField
            label={t('book.quantity')}
            type="number"
            min={o.min_quantity ?? 1}
            max={o.max_quantity ?? undefined}
            value={form.quantity}
            disabled={booking !== null}
            onChange={(e) => setForm({ ...form, quantity: Number(e.target.value) })}
          />
          <InputField
            label={t('book.coupon')}
            value={form.coupon}
            placeholder={t('common.optional')}
            disabled={order !== null}
            onChange={(e) => setForm({ ...form, coupon: e.target.value })}
          />
        </div>

        <div className="row" style={{ marginTop: 12 }}>
          <strong>{formatMoney(estimated, o.currency)}</strong>
          <span className="muted small">
            {form.quantity} × {formatMoney(o.unit_amount_cents, o.currency)}
          </span>
        </div>
      </Card>

      {booking ? (
        <Card>
          <div className="row row-tight">
            <h2 style={{ margin: 0 }}>{t('book.detail')}</h2>
            <Badge tone={booking.status === 'hold' ? 'info' : 'primary'}>
              {t(`book.status.${booking.status}`)}
            </Badge>
            <span className="spacer" />
            {countdown.remainingMs != null && !countdown.expired ? (
              <span className={countdown.urgent ? 'countdown urgent' : 'countdown'}>
                {t('book.holdCountdown', {
                  time: new Date(countdown.remainingMs).toISOString().slice(11, 19),
                })}
              </span>
            ) : null}
          </div>
          <div className="kv" style={{ marginTop: 8 }}>
            <span>{t('common.status')}</span>
            <strong>{t(`book.status.${booking.status}`)}</strong>
            <span>{t('common.price')}</span>
            <strong>{formatMoney(booking.total_cents, booking.currency)}</strong>
            <span>{t('book.payment')}</span>
            <strong>{booking.payment_status}</strong>
          </div>
          {countdown.expired ? <p className="error-text">{t('book.holdExpired')}</p> : null}

          <div className="row" style={{ marginTop: 12, gap: 8 }}>
            {booking.status === 'hold' ? (
              <Button
                variant="primary"
                loading={convert.isPending}
                disabled={countdown.expired}
                onClick={() => convert.mutate()}
              >
                {t('book.convert')}
              </Button>
            ) : null}
            {booking.status === 'draft' ? (
              <Badge tone="warning">{t('market.requestConfirm')}</Badge>
            ) : null}
            {booking.status !== 'hold' && !order && booking.payment_status === 'unpaid' ? (
              <Button variant="primary" loading={openOrder.isPending} onClick={() => openOrder.mutate()}>
                {t('book.paymentIntent')}
              </Button>
            ) : null}
            {order ? (
              <div className="row row-tight">
                <Badge tone={order.payment_status === 'paid' ? 'success' : 'warning'}>
                  {order.number}
                </Badge>
                <span className="small muted">
                  {formatMoney(order.total_cents, order.currency)}
                  {order.discount_cents > 0 ? ` (−${formatMoney(order.discount_cents, order.currency)})` : ''}
                </span>
                {order.payment_status !== 'paid' ? (
                  <Button variant="primary" loading={pay.isPending} onClick={() => pay.mutate()}>
                    {t('book.pay')}
                  </Button>
                ) : (
                  <Link to={`/bookings/${booking.id}`}>
                    <Button variant="primary">{t('common.view')}</Button>
                  </Link>
                )}
              </div>
            ) : null}
          </div>
          {paymentId ? <p className="small muted mono">{paymentId}</p> : null}
        </Card>
      ) : (
        <Card>
          <div className="row">
            <Button
              variant="primary"
              loading={hold.isPending}
              disabled={clientErrors.length > 0 || !startIso || !endIso}
              onClick={() => hold.mutate()}
            >
              {t('book.hold')}
            </Button>
            <span className="muted small">{t('offer.holdMinutes')}: {o.hold_minutes}</span>
          </div>
          {hold.error instanceof ApiError && holdConflictCode(hold.error) ? (
            <ErrorPanel
              message={describeError(hold.error, t)}
              onRetry={() => hold.mutate()}
              retryLabel={t('common.retry')}
            />
          ) : null}
        </Card>
      )}

      {clientErrors.length > 0 ? (
        <ErrorPanel message={clientErrors.join(' · ')} />
      ) : null}
      {o.booking_mode === 'request_confirm' ? (
        <p className="muted small">{t('market.requestConfirm')}</p>
      ) : null}
    </div>
  );
}
