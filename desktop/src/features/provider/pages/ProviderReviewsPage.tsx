// Reviews the organization received. A reply is public and one-shot (§5.7), so the
// form only appears for a published review that has not been answered yet.

import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { reviewApi } from '@/api/endpoints';
import { useAuth } from '@/auth/AuthProvider';
import { useI18n } from '@/i18n/index';
import { useToast, toastError } from '@/components/ui/Toast';
import { Badge, Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { EmptyState, ErrorPanel, Loading } from '@/components/ui/States';
import { Modal } from '@/components/ui/Modal';
import { Pagination } from '@/components/ui/Pagination';
import { TextareaField } from '@/components/ui/Field';
import { formatDate } from '@/lib/format';
import type { BadgeTone } from '@/components/ui/Card';
import type { Review, ReviewStatus } from '@/types/api';

const PAGE_SIZE = 20;

const STATUS_KEYS: Record<ReviewStatus, 'review.status.published' | 'review.status.pending_moderation' | 'review.status.removed'> = {
  published: 'review.status.published',
  pending_moderation: 'review.status.pending_moderation',
  removed: 'review.status.removed',
};

const STATUS_TONES: Record<ReviewStatus, BadgeTone> = {
  published: 'success',
  pending_moderation: 'warning',
  removed: 'danger',
};

function Stars({ rating }: { rating: number }) {
  return (
    <span aria-label={`${rating} / 5`} className="mono">
      {'★'.repeat(rating)}
      <span className="muted">{'★'.repeat(Math.max(0, 5 - rating))}</span>
    </span>
  );
}

export function ProviderReviewsPage() {
  const { t } = useI18n();
  const { user } = useAuth();
  const toasts = useToast();
  const qc = useQueryClient();
  const [offset, setOffset] = useState(0);
  const [replying, setReplying] = useState<Review | null>(null);
  const [draft, setDraft] = useState('');

  const query = useQuery({
    queryKey: ['reviews', 'org', offset],
    queryFn: () => reviewApi.forOrg(PAGE_SIZE, offset),
    enabled: !!user?.active_org_id,
  });

  const reply = useMutation({
    mutationFn: ({ id, text }: { id: string; text: string }) => reviewApi.reply(id, text),
    onSuccess: () => {
      toasts.success(t('prov.replied'));
      setReplying(null);
      setDraft('');
      void qc.invalidateQueries({ queryKey: ['reviews'] });
      void qc.invalidateQueries({ queryKey: ['offers'] });
    },
    onError: (err) => toastError(toasts, err, t),
  });

  const items = query.data?.items ?? [];
  const total = query.data?.total ?? 0;

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>{t('prov.reviewsTitle')}</h1>
          <span className="subtitle">{t('prov.title')}</span>
        </div>
        <Link to="/provider">
          <Button>{t('prov.dashboard')}</Button>
        </Link>
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
        <EmptyState title={t('prov.noReviews')} />
      ) : (
        <div className="stack">
          {items.map((r) => (
            <Card key={r.id}>
              <div className="row" style={{ justifyContent: 'space-between', gap: 12 }}>
                <div className="row row-tight">
                  <Stars rating={r.rating} />
                  <span className="small muted">{r.reviewer_name ?? t('common.customer')}</span>
                </div>
                <div className="row row-tight">
                  <Badge tone={STATUS_TONES[r.status]}>{t(STATUS_KEYS[r.status])}</Badge>
                  <span className="small muted">{formatDate(r.created_at)}</span>
                </div>
              </div>

              <div className="small muted" style={{ margin: '6px 0' }}>
                {r.offer_title ?? ''}
                {r.offer_id ? (
                  <>
                    {' · '}
                    <Link to={`/offers/${r.offer_id}`}>{t('offer.details')}</Link>
                  </>
                ) : null}
              </div>

              {r.comment ? <p style={{ marginBottom: 8 }}>{r.comment}</p> : null}

              {r.provider_reply ? (
                <div className="stack-tight" style={{ borderInlineStart: '2px solid currentColor', paddingInlineStart: 12 }}>
                  <span className="small muted">{t('prov.replyReview')}</span>
                  <p style={{ margin: 0 }}>{r.provider_reply}</p>
                  <span className="small muted">
                    {r.replied_at ? formatDate(r.replied_at) : ''}
                  </span>
                </div>
              ) : null}

              {!r.provider_reply && r.status === 'published' ? (
                <div className="row" style={{ marginTop: 8 }}>
                  <div className="spacer" />
                  <Button
                    size="sm"
                    variant="primary"
                    onClick={() => {
                      setReplying(r);
                      setDraft('');
                    }}
                  >
                    {t('common.reply')}
                  </Button>
                </div>
              ) : null}
            </Card>
          ))}
          <Pagination
            total={total}
            limit={PAGE_SIZE}
            offset={offset}
            onChange={setOffset}
            label={t('prov.reviewsTitle')}
          />
        </div>
      )}

      {replying ? (
        <Modal
          title={t('prov.replyReview')}
          onClose={() => setReplying(null)}
          footer={
            <>
              <Button onClick={() => setReplying(null)}>{t('common.cancel')}</Button>
              <Button
                variant="primary"
                disabled={draft.trim().length < 3}
                loading={reply.isPending}
                onClick={() =>
                  reply.mutate({ id: replying.id, text: draft.trim() })
                }
              >
                {t('common.send')}
              </Button>
            </>
          }
        >
          <div className="stack-tight">
            <p className="small muted">
              {replying.offer_title ?? ''} · <Stars rating={replying.rating} />
            </p>
            <TextareaField
              label={t('common.reply')}
              rows={4}
              value={draft}
              placeholder={t('prov.replyPlaceholder')}
              onChange={(e) => setDraft(e.target.value)}
            />
          </div>
        </Modal>
      ) : null}
    </div>
  );
}
