import { useI18n } from '@/i18n/index';
import { Badge, type BadgeTone } from '@/components/ui/Card';
import type { BookingStatus } from '@/types/api';

const TONES: Record<BookingStatus, BadgeTone> = {
  draft: 'default',
  hold: 'info',
  confirmed: 'primary',
  in_progress: 'warning',
  completed: 'success',
  cancelled: 'danger',
  expired: 'danger',
  disputed: 'danger',
};

/** One badge for every booking status label in the app, so colours stay consistent. */
export function BookingStatusBadge({ status }: { status: BookingStatus }) {
  const { t } = useI18n();
  return <Badge tone={TONES[status]}>{t(`book.status.${status}`)}</Badge>;
}
