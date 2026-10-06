import { Link } from 'react-router-dom';
import type { Offer } from '@/types/api';
import { formatMoney } from '@/lib/format';
import { Badge } from '@/components/ui/Card';
import { useI18n } from '@/i18n/index';

export function OfferCard({ offer }: { offer: Offer }) {
  const { t } = useI18n();
  return (
    <article className="card card-hover stack-tight" aria-label={offer.title}>
      <div className="row row-tight" style={{ justifyContent: 'space-between' }}>
        <h3 style={{ margin: 0 }}>
          <Link to={`/offers/${offer.id}`}>{offer.title}</Link>
        </h3>
        <Badge tone={offer.booking_mode === 'instant' ? 'success' : 'info'}>
          {offer.booking_mode === 'instant' ? t('market.instant') : t('market.requestConfirm')}
        </Badge>
      </div>
      <p className="muted small" style={{ marginBottom: 4 }}>
        {offer.description.length > 140
          ? `${offer.description.slice(0, 140)}…`
          : offer.description}
      </p>
      <div className="row row-tight muted small">
        {offer.category_label ? <Badge>{offer.category_label}</Badge> : null}
        {offer.city ? <span>{offer.city}</span> : null}
        {offer.rating_avg != null ? (
          <span aria-label={t('common.rating')}>
            ★ {offer.rating_avg.toFixed(1)}
            {offer.rating_count ? ` (${offer.rating_count})` : ''}
          </span>
        ) : null}
      </div>
      <div className="row" style={{ justifyContent: 'space-between', marginTop: 4 }}>
        <strong>{formatMoney(offer.unit_amount_cents, offer.currency)}</strong>
        <span className="muted small">/ unit</span>
      </div>
      <Link to={`/offers/${offer.id}`} className="btn btn-primary btn-sm mt-auto">
        {t('offer.details')}
      </Link>
    </article>
  );
}
