import { useFreeWindows } from '@/features/marketplace/hooks';
import { useI18n } from '@/i18n/index';
import { Loading, EmptyState, Skeleton } from '@/components/ui/States';
import { slotLabel } from '@/lib/format';

/**
 * Availability grid for an offer: renders the server-expanded free windows from
 * GET /availability/free?definition_id&from&to (CONTRACTS §8) — never computed
 * client-side (§5.5 "never trust client-observed availability").
 */
export function AvailabilityGrid({
  definitionId,
  from,
  to,
}: {
  definitionId: string;
  from: string;
  to: string;
}) {
  const { t } = useI18n();
  const query = useFreeWindows(definitionId, from, to);

  if (query.isPending) return <Skeleton lines={4} />;
  if (query.isError) return <Loading label={t('err.unknown')} />;

  const items = query.data?.items ?? [];
  if (items.length === 0) {
    return <EmptyState title={t('offer.noAvailability')} />;
  }

  return (
    <div className="avail-grid" role="list" aria-label={t('offer.availability')}>
      {items.map((w) => (
        <div
          key={`${w.window_start}-${w.window_end}`}
          className={
            w.free_quantity <= 0
              ? 'avail-cell full'
              : 'avail-cell free'
          }
          role="listitem"
        >
          <span className="avail-count">{w.free_quantity}</span>
          <span>{t('offer.freeQuantity')}</span>
          <div className="muted">{slotLabel(w.window_start, w.window_end)}</div>
        </div>
      ))}
    </div>
  );
}
