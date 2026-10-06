// The bell's full list. One query key with the sidebar hook, so the SSE push, the polling
// fallback and this screen all read and invalidate the same cache entry.

import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { notificationApi } from '@/api/endpoints';
import { useNotifications } from '@/hooks/useNotifications';
import { useI18n } from '@/i18n/index';
import { useToast, toastError } from '@/components/ui/Toast';
import { Card, Badge } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { EmptyState, ErrorPanel, Loading } from '@/components/ui/States';
import { Pagination } from '@/components/ui/Pagination';
import { formatDateTime } from '@/lib/format';
import type { AppNotification } from '@/types/api';

const PAGE_SIZE = 20;

/** Where a notification points to, from the payload the backend attached (§1). */
function linkFor(n: AppNotification): { to: string; label: string } | null {
  const d = n.data as Record<string, unknown>;
  if (typeof d.booking_id === 'string')
    return { to: `/bookings/${d.booking_id}`, label: n.title };
  if (typeof d.offer_id === 'string') return { to: `/offers/${d.offer_id}`, label: n.title };
  if (typeof d.demand_id === 'string')
    return { to: `/demands/${d.demand_id}`, label: n.title };
  if (typeof d.conversation_id === 'string')
    return { to: `/messages/${d.conversation_id}`, label: n.title };
  return null;
}

export function NotificationsPage() {
  const { t } = useI18n();
  const toasts = useToast();
  const qc = useQueryClient();
  const { data, isPending, isError, refetch, unread, live } = useNotifications();
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [offset, setOffset] = useState(0);

  const items = useMemo(() => {
    const all = (data?.items ?? []).filter((n) => (unreadOnly ? !n.read_at : true));
    return all.slice(offset, offset + PAGE_SIZE);
  }, [data, unreadOnly, offset]);

  const markRead = useMutation({
    mutationFn: (id: string) => notificationApi.markRead(id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['notifications'] }),
    onError: (err) => toastError(toasts, err, t),
  });

  const markAll = useMutation({
    mutationFn: () => notificationApi.markAllRead(),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['notifications'] });
      void qc.invalidateQueries({ queryKey: ['dashboard'] });
      toasts.success(t('notif.markedAll'));
    },
    onError: (err) => toastError(toasts, err, t),
  });

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>{t('notif.title')}</h1>
          <span className="subtitle">{live ? t('notif.live') : t('notif.polling')}</span>
        </div>
        <div className="row row-tight">
          <Button
            size="sm"
            variant={unreadOnly ? 'primary' : 'ghost'}
            onClick={() => {
              setUnreadOnly((v) => !v);
              setOffset(0);
            }}
          >
            {t('notif.unreadOnly')} ({unread})
          </Button>
          <Button
            size="sm"
            loading={markAll.isPending}
            disabled={unread === 0}
            onClick={() => markAll.mutate()}
          >
            {t('notif.markAll')}
          </Button>
        </div>
      </div>

      {isPending ? (
        <Loading label={t('common.loading')} />
      ) : isError ? (
        <ErrorPanel
          message={t('err.network_error')}
          onRetry={() => refetch()}
          retryLabel={t('common.retry')}
        />
      ) : items.length === 0 ? (
        <EmptyState title={unreadOnly ? t('notif.noUnread') : t('notif.empty')} />
      ) : (
        <Card>
          <div className="stack-tight">
            {items.map((n) => {
              const link = linkFor(n);
              return (
                <div key={n.id} className="row" style={{ alignItems: 'flex-start' }}>
                  <div style={{ minWidth: 0 }}>
                    <div className="row row-tight">
                      <strong className="small">{n.title}</strong>
                      {!n.read_at ? <Badge tone="primary">{t('notif.markRead')}</Badge> : null}
                      <span className="small muted">{n.kind}</span>
                    </div>
                    <p className="small muted" style={{ margin: '2px 0 0' }}>
                      {n.body}
                    </p>
                    <span className="small muted">{formatDateTime(n.created_at)}</span>
                  </div>
                  <span className="spacer" />
                  <div className="row row-tight">
                    {link ? (
                      <Link to={link.to}>
                        <Button size="sm" variant="ghost">
                          {t('notif.open')}
                        </Button>
                      </Link>
                    ) : null}
                    {!n.read_at ? (
                      <Button
                        size="sm"
                        loading={markRead.isPending && markRead.variables === n.id}
                        onClick={() => markRead.mutate(n.id)}
                      >
                        {t('notif.markRead')}
                      </Button>
                    ) : null}
                  </div>
                </div>
              );
            })}
          </div>
          <Pagination
            total={data?.total ?? 0}
            limit={PAGE_SIZE}
            offset={offset}
            onChange={setOffset}
            label={t('notif.title')}
          />
        </Card>
      )}
    </div>
  );
}
