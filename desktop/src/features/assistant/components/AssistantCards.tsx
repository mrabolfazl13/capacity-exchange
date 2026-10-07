// The provider-facing assistant panels (CONTRACTS §8). Both take their payload as props
// and render nothing on their own initiative: the pages decide when a suggestion is
// allowed to appear, so an `/ai/*` failure can never blank or block a working screen.

import { Link } from 'react-router-dom';
import { useI18n, type MessageKey } from '@/i18n/index';
import { Badge, Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { formatDateTime, formatMoney } from '@/lib/format';
import type { CopilotBriefing, IdleWindowRow, UtilizationInsights } from '@/types/api';

export function CopilotCard({ briefing }: { briefing: CopilotBriefing }) {
  const { t } = useI18n();
  const holds = briefing.at_risk_holds;

  return (
    <Card>
      <div className="row">
        <h2 style={{ margin: 0 }}>{t('ai.copilot.title')}</h2>
        <Badge tone="info">{t('ai.advisory')}</Badge>
        <div className="spacer" />
        <span className="small muted">
          {t('ai.copilot.collected', {
            amount: formatMoney(briefing.revenue_last_30d_cents, briefing.currency),
          })}
        </span>
      </div>

      <p className="ai-summary">{briefing.summary_text}</p>

      {holds.length > 0 ? (
        <div className="stack-tight">
          <h3 style={{ margin: 0 }} className="small">
            {t('ai.copilot.atRisk')}
          </h3>
          {holds.map((h) => (
            <div key={h.booking_id} className="row" style={{ gap: 8 }}>
              <span className="small">{h.offer_title}</span>
              <span className="small muted mono">{formatDateTime(h.hold_expires_at)}</span>
              <span className="spacer" />
              <Link to={`/bookings/${h.booking_id}`}>
                <Button size="sm">{t('common.view')}</Button>
              </Link>
            </div>
          ))}
        </div>
      ) : null}

      {briefing.truncated ? (
        <p className="small muted">{t('ai.truncated')}</p>
      ) : null}
    </Card>
  );
}

/**
 * Published hours that stayed empty often enough to be a pattern rather than a quiet
 * week. `occurrences` is the evidence count, so the row says how often it was offered.
 */
export function QuietHoursCard({
  insights,
}: {
  insights: UtilizationInsights;
}) {
  const { t } = useI18n();
  const rows: IdleWindowRow[] = insights.idle_windows;

  return (
    <Card>
      <div className="row">
        <h2 style={{ margin: 0 }}>{t('ai.quiet.title')}</h2>
        <Badge tone="info">{t('ai.advisory')}</Badge>
      </div>
      <p className="small muted">{t('ai.quiet.hint')}</p>

      {rows.length === 0 ? (
        <p className="muted small">{t('ai.quiet.none')}</p>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>{t('ai.quiet.when')}</th>
                <th className="text-right">{t('ai.quiet.occurrences')}</th>
                <th>{t('common.description')}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((w, i) => (
                <tr key={`${w.definition_id}-${w.dow}-${w.start_time}-${i}`}>
                  <td className="small mono">
                    {`${t(`common.dow.${w.dow}` as MessageKey)} ${w.start_time}–${w.end_time}`}
                  </td>
                  <td className="text-right mono">{w.occurrences}</td>
                  <td className="small">{w.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {insights.truncated ? <p className="small muted">{t('ai.truncated')}</p> : null}
    </Card>
  );
}
