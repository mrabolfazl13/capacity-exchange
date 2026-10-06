import { Link } from 'react-router-dom';
import { useI18n } from '@/i18n/index';
import { EmptyState } from '@/components/ui/States';
import { Button } from '@/components/ui/Button';

export function NotFoundPage() {
  const { t } = useI18n();
  return (
    <EmptyState
      icon="∅"
      title={t('err.not_found')}
      hint={t('routes.notFoundHint')}
      action={
        <Link to="/dashboard">
          <Button variant="primary">{t('dash.title')}</Button>
        </Link>
      }
    />
  );
}
