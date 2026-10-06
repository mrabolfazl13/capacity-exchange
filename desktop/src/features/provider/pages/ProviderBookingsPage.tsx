// Provider board: every booking against the organization, grouped by the state the
// seller has to act on. The buttons come from allowedBookingActions, so the board
// can only offer a transition §5.5 actually permits.

import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { bookingApi } from '@/api/endpoints';
import { useAuth } from '@/auth/AuthProvider';
import { useI18n } from '@/i18n/index';
import { useToast, toastError } from '@/components/ui/Toast';
import { Badge } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { EmptyState, ErrorPanel, Loading } from '@/components/ui/States';
import { BookingStatusBadge } from '@/features/booking/components/BookingStatusBadge';
import { PROVIDER_BOARD_COLUMNS, allowedBookingActions, type BookingAction } from '@/features/booking/logic';
import { formatCountdown, formatDateTime, formatMoney } from '@/lib/format';
import { useHoldCountdown } from '@/hooks/useCountdown';
import type { Booking } from '@/types/api';

const PAGE_SIZE = 100;

/** Only the three seller-side transitions get a button; everything else is read-only. */
const SELLER_ACTIONS = ['confirm', 'start', 'complete'] as const;
type SellerAction = (typeof SELLER_ACTIONS)[number];

const ACTION_KEYS: Record<SellerAction, 'prov.confirm' | 'prov.start' | 'prov.complete'> = {
  confirm: 'prov.confirm',
  start: 'prov.start',
  complete: 'prov.complete',
};

export function ProviderBookingsPage() {
  const { t } = useI18n();
  const { user } = useAuth();
  const toasts = useToast();
  const qc = useQueryClient();
  const [upcomingOnly, setUpcomingOnly] = useState(false);

  const query = useQuery({
    queryKey: ['bookings', 'provider', PAGE_SIZE],
    queryFn: () => bookingApi.list({ provider: true }, PAGE_SIZE, 0),
    enabled: !!user?.active_org_id,
  });

  const move = useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'confirm' | 'start' | 'complete' }) =>
      action === 'confirm'
        ? bookingApi.confirm(id)
        : action === 'start'
          ? bookingApi.start(id)
          : bookingApi.complete(id),
    onSuccess: (_b, vars) => {
      toasts.success(
        vars.action === 'confirm'
          ? t('prov.confirmed')
          : vars.action === 'start'
            ? t('prov.started')
            : t('prov.completed'),
      );
      void qc.invalidateQueries({ queryKey: ['bookings'] });
      void qc.invalidateQueries({ queryKey: ['dashboard'] });
      void qc.invalidateQueries({ queryKey: ['notifications'] });
    },
    onError: (err) => toastError(toasts, err, t),
  });

  const items = query.data?.items ?? [];
  const columns = useMemo(() => {
    const now = Date.now();
    return PROVIDER_BOARD_COLUMNS.map((col) => ({
      ...col,
      rows: items
        .filter((b) => col.statuses.includes(b.status))
        .filter((b) => !upcomingOnly || new Date(b.window_start).getTime() >= now)
        .sort((a, b) => a.window_start.localeCompare(b.window_start)),
    }));
  }, [items, upcomingOnly]);

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>{t('prov.board')}</h1>
          <span className="subtitle">{t('prov.title')}</span>
        </div>
        <div className="row row-tight">
          <Button
            size="sm"
            variant={upcomingOnly ? 'primary' : 'default'}
            onClick={() => setUpcomingOnly((v) => !v)}
          >
            {t('prov.upcoming')}
          </Button>
          <Link to="/provider">
            <Button>{t('prov.dashboard')}</Button>
          </Link>
        </div>
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
        <EmptyState
          title={t('prov.noBookings')}
          hint={t('prov.noMatchesHint')}
          action={
            <Link to="/provider/matches">
              <Button variant="primary">{t('nav.providerMatches')}</Button>
            </Link>
          }
        />
      ) : (
        <div className="board">
          {columns.map((col) => (
            <div key={col.title} className="board-col">
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <h3>{t(col.title)}</h3>
                <span className="muted mono">{col.rows.length}</span>
              </div>
              {col.rows.length === 0 ? (
                <p className="muted small">{t('common.none')}</p>
              ) : (
                col.rows.map((b) => (
                  <BoardCard key={b.id} booking={b} onMove={(a) => move.mutate({ id: b.id, action: a })} busy={move.isPending} />
                ))
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function BoardCard({
  booking: b,
  onMove,
  busy,
}: {
  booking: Booking;
  onMove: (action: SellerAction) => void;
  busy: boolean;
}) {
  const { t } = useI18n();
  const { user } = useAuth();
  const actions = allowedBookingActions(b, user).filter(
    (a): a is SellerAction => (SELLER_ACTIONS as readonly BookingAction[]).includes(a),
  );
  const hold = useHoldCountdown(b.status === 'hold' ? b.hold_expires_at : null);

  return (
    <div className="board-card stack-tight">
      <strong>{b.offer_title ?? b.id.slice(0, 8)}</strong>
      <span className="small muted">{formatDateTime(b.window_start)}</span>
      <div className="row row-tight small">
        <span className="mono">
          {b.quantity} × {formatMoney(b.unit_amount_cents, b.currency)}
        </span>
        <div className="spacer" />
        <strong className="mono">{formatMoney(b.total_cents, b.currency)}</strong>
      </div>
      <div className="row row-tight">
        <BookingStatusBadge status={b.status} />
        {b.payment_status !== 'not_required' ? (
          <Badge tone={b.payment_status === 'paid' ? 'success' : 'warning'}>
            {t(`book.pay.${b.payment_status}`)}
          </Badge>
        ) : null}
      </div>
      {hold.remainingMs != null && !hold.expired ? (
        <span className={`small ${hold.urgent ? 'countdown urgent' : 'countdown'}`}>
          {t('book.holdCountdown', { time: formatCountdown(hold.remainingMs) })}
        </span>
      ) : null}
      <div className="row row-tight">
        <Link to={`/bookings/${b.id}`}>
          <Button size="sm" variant="ghost">
            {t('common.view')}
          </Button>
        </Link>
        <div className="spacer" />
        {actions.map((a) => (
          <Button key={a} size="sm" variant="primary" loading={busy} onClick={() => onMove(a)}>
            {t(ACTION_KEYS[a])}
          </Button>
        ))}
      </div>
    </div>
  );
}
