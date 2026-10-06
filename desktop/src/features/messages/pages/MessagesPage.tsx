// Booking and enquiry threads. Two panes: the inbox on the left, the selected thread on the
// right; the route parameter is the shared state so a notification can deep-link here.

import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { conversationApi } from '@/api/endpoints';
import { useAuth, isProviderUser } from '@/auth/AuthProvider';
import { useI18n } from '@/i18n/index';
import { useToast, toastError } from '@/components/ui/Toast';
import { Card, Badge } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { EmptyState, ErrorPanel, Loading } from '@/components/ui/States';
import { formatDateTime } from '@/lib/format';
import type { ConversationStatus } from '@/types/api';

const INBOX_PAGE = 30;

export function MessagesPage() {
  const { conversationId } = useParams<{ conversationId?: string }>();
  const { t } = useI18n();
  const toasts = useToast();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { user } = useAuth();

  const [scope, setScope] = useState<'mine' | 'org'>('mine');
  const [draft, setDraft] = useState('');
  const bottomRef = useRef<HTMLDivElement>(null);

  const canSeeOrgThreads = isProviderUser(user) || !!user?.active_org_id;
  const provider = scope === 'org';

  const inbox = useQuery({
    queryKey: ['conversations', scope],
    queryFn: () => conversationApi.list(INBOX_PAGE, 0, { provider }),
  });

  const active = useMemo(
    () => (inbox.data?.items ?? []).find((c) => c.id === conversationId) ?? null,
    [inbox.data, conversationId],
  );

  const messages = useQuery({
    queryKey: ['conversations', conversationId, 'messages'],
    queryFn: () => conversationApi.messages(conversationId as string, 200, 0),
    enabled: !!conversationId,
  });

  const send = useMutation({
    mutationFn: (body: string) => conversationApi.send(conversationId as string, body),
    onSuccess: () => {
      setDraft('');
      void qc.invalidateQueries({ queryKey: ['conversations', conversationId, 'messages'] });
      void qc.invalidateQueries({ queryKey: ['conversations', scope] });
    },
    onError: (err) => toastError(toasts, err, t),
  });

  const setStatus = useMutation({
    mutationFn: (next: ConversationStatus) =>
      conversationApi.setStatus(conversationId as string, next),
    onSuccess: (_data, next) => {
      void qc.invalidateQueries({ queryKey: ['conversations'] });
      toasts.success(next === 'closed' ? t('msg.threadClosed') : t('msg.threadReopened'));
    },
    onError: (err) => toastError(toasts, err, t),
  });

  // A new thread should sit at the bottom of the transcript, like every chat client.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'end' });
  }, [messages.data?.items.length]);

  // Landing on /messages with no selection: open the first thread in the inbox.
  useEffect(() => {
    if (!conversationId && (inbox.data?.items ?? []).length > 0) {
      navigate(`/messages/${inbox.data!.items[0].id}`, { replace: true });
    }
  }, [conversationId, inbox.data, navigate]);

  const items = inbox.data?.items ?? [];

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>{t('msg.title')}</h1>
          <span className="subtitle">{t('msg.conversations')}</span>
        </div>
        <div className="row row-tight">
          <Button
            variant={scope === 'mine' ? 'primary' : 'ghost'}
            size="sm"
            onClick={() => setScope('mine')}
          >
            {t('msg.scopeMine')}
          </Button>
          {canSeeOrgThreads ? (
            <Button
              variant={scope === 'org' ? 'primary' : 'ghost'}
              size="sm"
              onClick={() => setScope('org')}
            >
              {t('msg.scopeOrg')}
            </Button>
          ) : null}
        </div>
      </div>

      <div className="grid" style={{ gridTemplateColumns: 'minmax(240px, 320px) 1fr' }}>
        <Card aria-label={t('msg.conversations')}>
          {inbox.isPending ? (
            <Loading label={t('common.loading')} />
          ) : inbox.isError ? (
            <ErrorPanel
              message={t('err.network_error')}
              onRetry={() => inbox.refetch()}
              retryLabel={t('common.retry')}
            />
          ) : items.length === 0 ? (
            <EmptyState title={t('msg.empty')} hint={t('msg.emptyHint')} />
          ) : (
            <div className="stack-tight">
              {items.map((c) => (
                <button
                  key={c.id}
                  type="button"
                  className={c.id === conversationId ? 'nav-link active' : 'nav-link'}
                  onClick={() => navigate(`/messages/${c.id}`)}
                >
                  <span className="stack-tight" style={{ minWidth: 0, textAlign: 'start' }}>
                    <strong className="small">{c.peer_name ?? c.kind}</strong>
                    <span className="small muted">{c.last_message_body ?? ''}</span>
                    <span className="small muted">{formatDateTime(c.last_message_at)}</span>
                  </span>
                  {(c.unread_count ?? 0) > 0 ? (
                    <span className="nav-badge">{c.unread_count}</span>
                  ) : null}
                </button>
              ))}
            </div>
          )}
        </Card>

        <Card>
          {!conversationId ? (
            <EmptyState title={t('msg.empty')} hint={t('msg.emptyHint')} />
          ) : (
            <>
              <div className="row row-tight">
                <h2 style={{ margin: 0 }}>{active?.peer_name ?? t('msg.thread')}</h2>
                {active ? (
                  <Badge
                    tone={
                      active.status === 'open'
                        ? 'success'
                        : active.status === 'archived'
                          ? 'default'
                          : 'warning'
                    }
                  >
                    {t(`msg.status.${active.status}`)}
                  </Badge>
                ) : null}
                <span className="spacer" />
                {active && active.status !== 'archived' ? (
                  active.status === 'open' ? (
                    <Button
                      size="sm"
                      variant="ghost"
                      loading={setStatus.isPending}
                      onClick={() => setStatus.mutate('closed')}
                    >
                      {t('msg.closeThread')}
                    </Button>
                  ) : (
                    <Button
                      size="sm"
                      variant="ghost"
                      loading={setStatus.isPending}
                      onClick={() => setStatus.mutate('open')}
                    >
                      {t('msg.reopenThread')}
                    </Button>
                  )
                ) : null}
              </div>

              {messages.isPending ? (
                <Loading label={t('common.loading')} />
              ) : messages.isError ? (
                <ErrorPanel
                  message={t('err.network_error')}
                  onRetry={() => messages.refetch()}
                  retryLabel={t('common.retry')}
                />
              ) : (messages.data?.items ?? []).length === 0 ? (
                <p className="muted small">{t('msg.noMessages')}</p>
              ) : (
                <div className="stack-tight" style={{ marginTop: 12, maxHeight: '55vh', overflowY: 'auto' }}>
                  {messages.data!.items.map((m) => (
                    <div
                      key={m.id}
                      className={m.sender_id === user?.id ? 'card board-card' : 'board-card'}
                      style={{ padding: 10 }}
                    >
                      <div className="row row-tight">
                        <strong className="small">{m.sender_name ?? (m.is_system ? t('msg.system') : '—')}</strong>
                        <span className="small muted">{formatDateTime(m.created_at)}</span>
                      </div>
                      <p className="small" style={{ margin: '4px 0 0' }}>
                        {m.body}
                      </p>
                    </div>
                  ))}
                  <div ref={bottomRef} />
                </div>
              )}

              {active && active.status === 'open' ? (
                <form
                  className="row"
                  style={{ marginTop: 12 }}
                  onSubmit={(e) => {
                    e.preventDefault();
                    if (draft.trim()) send.mutate(draft.trim());
                  }}
                >
                  <input
                    className="input"
                    value={draft}
                    placeholder={t('msg.write')}
                    aria-label={t('msg.write')}
                    onChange={(e) => setDraft(e.target.value)}
                  />
                  <Button type="submit" variant="primary" loading={send.isPending} disabled={!draft.trim()}>
                    {t('common.send')}
                  </Button>
                </form>
              ) : null}
            </>
          )}
        </Card>
      </div>
    </div>
  );
}
