// Provider capacity wizard (UI_UX_SPEC §Provider Capacity Wizard): ten steps that
// produce a resource, its units, its availability and one published listing.
//
// Nothing is written until the publish step: POST /capacities carries the units
// inline, then availability rules, overrides and the listing follow against the
// ids the server minted. That ordering keeps a half-finished draft off the database.

import { useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { capacityApi, offerApi, type DefinitionInput, type OverrideInput, type RecurringAvailabilityInput } from '@/api/endpoints';
import { describeError } from '@/api/errors';
import { useCategories } from '@/features/marketplace/hooks';
import { useI18n, type MessageKey } from '@/i18n/index';
import { useToast, toastError } from '@/components/ui/Toast';
import { Badge, Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { ErrorPanel, Loading } from '@/components/ui/States';
import { InputField, SelectField, TextareaField } from '@/components/ui/Field';
import { formatMoney } from '@/lib/format';
import type {
  BookingMode,
  CapacityMode,
  OfferPricingMode,
  OverrideKind,
} from '@/types/api';

const uid = () => Math.random().toString(36).slice(2, 10);

const STEPS: { key: number; title: MessageKey; hint: MessageKey }[] = [
  { key: 0, title: 'wizard.step.category', hint: 'wizard.stepHint.category' },
  { key: 1, title: 'wizard.step.resource', hint: 'wizard.stepHint.resource' },
  { key: 2, title: 'wizard.step.definition', hint: 'wizard.stepHint.definition' },
  { key: 3, title: 'wizard.step.location', hint: 'wizard.stepHint.location' },
  { key: 4, title: 'wizard.step.availability', hint: 'wizard.stepHint.availability' },
  { key: 5, title: 'wizard.step.pricing', hint: 'wizard.stepHint.pricing' },
  { key: 6, title: 'wizard.step.rules', hint: 'wizard.stepHint.rules' },
  { key: 7, title: 'wizard.step.media', hint: 'wizard.stepHint.media' },
  { key: 8, title: 'wizard.step.preview', hint: 'wizard.stepHint.preview' },
  { key: 9, title: 'wizard.step.publish', hint: 'wizard.stepHint.publish' },
];

const MODES: CapacityMode[] = ['quantity', 'scheduled', 'open_ended'];
const MODE_KEYS: Record<CapacityMode, MessageKey> = {
  quantity: 'wizard.modeQuantity',
  scheduled: 'wizard.modeScheduled',
  open_ended: 'wizard.modeOpenEnded',
};
const PRICING_MODES: OfferPricingMode[] = ['per_unit_time', 'per_quantity', 'flat'];

interface UnitDraft {
  key: string;
  name: string;
  unit_label: string;
  min_quantity: string;
  max_quantity: string;
  slot_duration_minutes: string;
  buffer_before_minutes: string;
  buffer_after_minutes: string;
}

interface RuleDraft {
  key: string;
  unitKey: string;
  dow: string;
  start_time: string;
  end_time: string;
  quantity: string;
}

interface OverrideDraft {
  key: string;
  unitKey: string;
  override_date: string;
  kind: OverrideKind;
  start_time: string;
  end_time: string;
  quantity: string;
  reason: string;
}

interface BandDraft {
  key: string;
  hours_before: string;
  refund_pct: string;
}

interface MediaDraft {
  key: string;
  a: string;
  b: string;
  c: string;
}

interface Draft {
  categoryId: string;
  resourceName: string;
  resourceDescription: string;
  capacityMode: CapacityMode;
  units: UnitDraft[];
  line1: string;
  line2: string;
  city: string;
  state: string;
  postalCode: string;
  country: string;
  lat: string;
  lon: string;
  timezone: string;
  rules: RuleDraft[];
  overrides: OverrideDraft[];
  offerTitle: string;
  offerDescription: string;
  pricingMode: OfferPricingMode;
  price: string;
  currency: string;
  bookingMode: BookingMode;
  holdMinutes: string;
  minLead: string;
  maxLead: string;
  minDuration: string;
  maxDuration: string;
  minQty: string;
  maxQty: string;
  bands: BandDraft[];
  photos: MediaDraft[];
  documents: MediaDraft[];
  publishNow: boolean;
}

const emptyUnit = (): UnitDraft => ({
  key: uid(),
  name: '',
  unit_label: '',
  min_quantity: '1',
  max_quantity: '1',
  slot_duration_minutes: '60',
  buffer_before_minutes: '0',
  buffer_after_minutes: '0',
});

const initialDraft = (): Draft => ({
  categoryId: '',
  resourceName: '',
  resourceDescription: '',
  capacityMode: 'quantity',
  units: [emptyUnit()],
  line1: '',
  line2: '',
  city: '',
  state: '',
  postalCode: '',
  country: '',
  lat: '',
  lon: '',
  timezone: 'UTC',
  rules: [],
  overrides: [],
  offerTitle: '',
  offerDescription: '',
  pricingMode: 'per_quantity',
  price: '',
  currency: 'USD',
  bookingMode: 'instant',
  holdMinutes: '15',
  minLead: '0',
  maxLead: '',
  minDuration: '',
  maxDuration: '',
  minQty: '',
  maxQty: '',
  bands: [{ key: uid(), hours_before: '24', refund_pct: '100' }],
  photos: [],
  documents: [],
  publishNow: true,
});

function toInt(value: string, fallback: number): number {
  const n = Number.parseInt(value, 10);
  return Number.isFinite(n) ? n : fallback;
}

function toIntOrNull(value: string): number | null {
  const n = Number.parseInt(value, 10);
  return Number.isFinite(n) ? n : null;
}

function toFloatOrNull(value: string): number | null {
  const n = Number.parseFloat(value.replace(',', '.'));
  return Number.isFinite(n) ? n : null;
}

export function CapacityWizardPage() {
  const { t } = useI18n();
  const toasts = useToast();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const categories = useCategories();
  const [step, setStep] = useState(0);
  const [draft, setDraft] = useState<Draft>(initialDraft);
  const [blocked, setBlocked] = useState(false);

  const patch = (p: Partial<Draft>) => setDraft((d) => ({ ...d, ...p }));

  const unitOptions = useMemo(
    () =>
      draft.units.map((u, i) => ({
        value: u.key,
        label: u.name.trim() || `${t('wizard.step.definition')} ${i + 1}`,
      })),
    [draft.units, t],
  );

  const stepValid = useMemo(() => validate(step, draft), [step, draft]);

  const publish = useMutation({
    mutationFn: () => runPublish(draft),
    onSuccess: (result) => {
      toasts.success(result.offerId ? t('wizard.published') : t('wizard.created'));
      void qc.invalidateQueries({ queryKey: ['resources'] });
      void qc.invalidateQueries({ queryKey: ['offers'] });
      void qc.invalidateQueries({ queryKey: ['dashboard'] });
      navigate(result.offerId ? '/provider/offers' : '/provider/resources');
    },
    onError: (err) => toastError(toasts, err, t),
  });

  if (categories.isPending) return <Loading label={t('common.loading')} />;
  if (categories.isError)
    return (
      <ErrorPanel
        message={t('err.network_error')}
        onRetry={() => categories.refetch()}
        retryLabel={t('common.retry')}
      />
    );

  const goNext = () => {
    if (!stepValid) {
      setBlocked(true);
      return;
    }
    setBlocked(false);
    setStep((s) => Math.min(s + 1, STEPS.length - 1));
  };

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>{t('wizard.title')}</h1>
          <span className="subtitle">{t(STEPS[step].hint)}</span>
        </div>
        <span className="muted small">
          {t('wizard.step', { n: step + 1 })}
        </span>
      </div>

      <div className="wizard-steps" role="tablist">
        {STEPS.map((s, i) => (
          <button
            key={s.key}
            type="button"
            role="tab"
            aria-selected={i === step}
            className={i === step ? 'wizard-step active' : 'wizard-step'}
            disabled={i > step}
            onClick={() => setStep(i)}
          >
            {i + 1}. {t(s.title)}
          </button>
        ))}
      </div>

      <Card>
        <h2>{t(STEPS[step].title)}</h2>
        {step === 0 ? (
          <SelectField
            label={t('wizard.categoryPick')}
            required
            placeholder={t('wizard.selectCategory')}
            value={draft.categoryId}
            onChange={(e) => patch({ categoryId: e.target.value })}
            options={(categories.data?.items ?? [])
              .filter((c) => c.is_active)
              .map((c) => ({ value: c.id, label: c.label }))}
          />
        ) : null}

        {step === 1 ? (
          <div className="form-grid">
            <InputField
              label={t('wizard.resourceName')}
              required
              value={draft.resourceName}
              onChange={(e) => patch({ resourceName: e.target.value })}
            />
            <SelectField
              label={t('wizard.capacityMode')}
              value={draft.capacityMode}
              onChange={(e) => patch({ capacityMode: e.target.value as CapacityMode })}
              options={MODES.map((m) => ({ value: m, label: t(MODE_KEYS[m]) }))}
            />
            <TextareaField
              label={t('wizard.resourceDescription')}
              rows={4}
              value={draft.resourceDescription}
              onChange={(e) => patch({ resourceDescription: e.target.value })}
            />
          </div>
        ) : null}

        {step === 2 ? (
          <UnitsStep draft={draft} patch={patch} />
        ) : null}

        {step === 3 ? (
          <div className="form-grid">
            <InputField
              label={t('wizard.line1')}
              required
              value={draft.line1}
              onChange={(e) => patch({ line1: e.target.value })}
            />
            <InputField
              label={t('wizard.line2')}
              value={draft.line2}
              onChange={(e) => patch({ line2: e.target.value })}
            />
            <InputField
              label={t('wizard.city')}
              required
              value={draft.city}
              onChange={(e) => patch({ city: e.target.value })}
            />
            <InputField
              label={t('wizard.state')}
              value={draft.state}
              onChange={(e) => patch({ state: e.target.value })}
            />
            <InputField
              label={t('wizard.postalCode')}
              value={draft.postalCode}
              onChange={(e) => patch({ postalCode: e.target.value })}
            />
            <InputField
              label={t('wizard.country')}
              value={draft.country}
              maxLength={2}
              onChange={(e) => patch({ country: e.target.value.toUpperCase() })}
            />
            <InputField
              label={t('wizard.timezone')}
              required
              value={draft.timezone}
              onChange={(e) => patch({ timezone: e.target.value })}
            />
            <InputField
              label="Latitude"
              value={draft.lat}
              onChange={(e) => patch({ lat: e.target.value })}
            />
            <InputField
              label="Longitude"
              value={draft.lon}
              onChange={(e) => patch({ lon: e.target.value })}
            />
          </div>
        ) : null}

        {step === 4 ? (
          <AvailabilityStep draft={draft} patch={patch} unitOptions={unitOptions} />
        ) : null}

        {step === 5 ? (
          <div className="form-grid">
            <InputField
              label={t('wizard.offerTitle')}
              required
              value={draft.offerTitle}
              onChange={(e) => patch({ offerTitle: e.target.value })}
            />
            <TextareaField
              label={t('wizard.offerDescription')}
              required
              rows={5}
              value={draft.offerDescription}
              onChange={(e) => patch({ offerDescription: e.target.value })}
            />
            <SelectField
              label={t('wizard.pricingMode')}
              value={draft.pricingMode}
              onChange={(e) => patch({ pricingMode: e.target.value as OfferPricingMode })}
              options={PRICING_MODES.map((m) => ({ value: m, label: t(`wizard.pricing.${m}`) }))}
            />
            <InputField
              label={t('wizard.pricePerUnit')}
              required
              inputMode="decimal"
              value={draft.price}
              onChange={(e) => patch({ price: e.target.value })}
            />
            <InputField
              label={t('wizard.currency')}
              required
              maxLength={3}
              value={draft.currency}
              onChange={(e) => patch({ currency: e.target.value.toUpperCase() })}
            />
          </div>
        ) : null}

        {step === 6 ? (
          <RulesStep draft={draft} patch={patch} />
        ) : null}

        {step === 7 ? (
          <MediaStep draft={draft} patch={patch} />
        ) : null}

        {step === 8 ? <Preview draft={draft} /> : null}

        {step === 9 ? (
          <div className="stack-tight">
            <p className="muted small">{t('wizard.stepHint.publish')}</p>
            <label className="row row-tight">
              <input
                type="checkbox"
                checked={draft.publishNow}
                onChange={(e) => patch({ publishNow: e.target.checked })}
              />
              <span>{t('wizard.publishNow')}</span>
            </label>
            {publish.isError ? (
              <div className="stack-tight">
                <ErrorPanel
                  message={describeError(publish.error, t)}
                  retryLabel={t('common.retry')}
                  onRetry={() => publish.mutate()}
                />
                <p className="small muted">
                  {t('wizard.publishPartial')}{' '}
                  <Link to="/provider/resources">{t('prov.resources')}</Link>
                </p>
              </div>
            ) : null}
          </div>
        ) : null}
      </Card>

      {blocked && !stepValid ? (
        <p className="error-text" role="alert">
          {t('wizard.nextBlocked')}
        </p>
      ) : null}

      <div className="actions">
        <Button disabled={step === 0} onClick={() => setStep((s) => Math.max(0, s - 1))}>
          {t('common.previous')}
        </Button>
        <div className="spacer" />
        {step < STEPS.length - 1 ? (
          <Button variant="primary" onClick={goNext}>
            {t('common.next')}
          </Button>
        ) : (
          <Button
            variant="primary"
            loading={publish.isPending}
            disabled={!stepValid}
            onClick={() => publish.mutate()}
          >
            {t('wizard.publish')}
          </Button>
        )}
      </div>
    </div>
  );
}

function UnitsStep({ draft, patch }: { draft: Draft; patch: (p: Partial<Draft>) => void }) {
  const { t } = useI18n();
  const scheduled = draft.capacityMode === 'scheduled';

  const setUnit = (key: string, p: Partial<UnitDraft>) =>
    patch({ units: draft.units.map((u) => (u.key === key ? { ...u, ...p } : u)) });

  return (
    <div className="stack">
      {draft.units.map((u, i) => (
        <div key={u.key} className="card stack-tight">
          <div className="row" style={{ justifyContent: 'space-between' }}>
            <h3 style={{ margin: 0 }}>
              {t('wizard.units')} {i + 1}
            </h3>
            {draft.units.length > 1 ? (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => patch({ units: draft.units.filter((x) => x.key !== u.key) })}
              >
                {t('common.delete')}
              </Button>
            ) : null}
          </div>
          <div className="form-grid">
            <InputField
              label={t('wizard.definitionName')}
              required
              value={u.name}
              onChange={(e) => setUnit(u.key, { name: e.target.value })}
            />
            <InputField
              label={t('wizard.unitLabel')}
              required
              maxLength={32}
              value={u.unit_label}
              onChange={(e) => setUnit(u.key, { unit_label: e.target.value })}
            />
            <InputField
              label={t('wizard.minQty')}
              inputMode="numeric"
              value={u.min_quantity}
              onChange={(e) => setUnit(u.key, { min_quantity: e.target.value })}
            />
            <InputField
              label={t('wizard.maxQty')}
              inputMode="numeric"
              value={u.max_quantity}
              onChange={(e) => setUnit(u.key, { max_quantity: e.target.value })}
            />
            {scheduled ? (
              <InputField
                label={t('wizard.slotDuration')}
                inputMode="numeric"
                value={u.slot_duration_minutes}
                onChange={(e) => setUnit(u.key, { slot_duration_minutes: e.target.value })}
              />
            ) : null}
            <InputField
              label={t('wizard.bufferBefore')}
              inputMode="numeric"
              value={u.buffer_before_minutes}
              onChange={(e) => setUnit(u.key, { buffer_before_minutes: e.target.value })}
            />
            <InputField
              label={t('wizard.bufferAfter')}
              inputMode="numeric"
              value={u.buffer_after_minutes}
              onChange={(e) => setUnit(u.key, { buffer_after_minutes: e.target.value })}
            />
          </div>
        </div>
      ))}
      <div>
        <Button size="sm" onClick={() => patch({ units: [...draft.units, emptyUnit()] })}>
          {t('wizard.addUnit')}
        </Button>
      </div>
    </div>
  );
}

function AvailabilityStep({
  draft,
  patch,
  unitOptions,
}: {
  draft: Draft;
  patch: (p: Partial<Draft>) => void;
  unitOptions: { value: string; label: string }[];
}) {
  const { t } = useI18n();
  const primaryUnit = draft.units[0]?.key ?? '';

  const setRule = (key: string, p: Partial<RuleDraft>) =>
    patch({ rules: draft.rules.map((r) => (r.key === key ? { ...r, ...p } : r)) });

  const setOverride = (key: string, p: Partial<OverrideDraft>) =>
    patch({
      overrides: draft.overrides.map((o) => (o.key === key ? { ...o, ...p } : o)),
    });

  return (
    <div className="stack">
      <div className="stack-tight">
        <h3 style={{ margin: 0 }}>{t('wizard.recurrence')}</h3>
        {draft.rules.length === 0 ? (
          <p className="muted small">{t('wizard.noRulesYet')}</p>
        ) : null}
        {draft.rules.map((r) => (
          <div key={r.key} className="card stack-tight">
            <div className="form-grid">
              <SelectField
                label={t('wizard.ruleFor')}
                value={r.unitKey}
                onChange={(e) => setRule(r.key, { unitKey: e.target.value })}
                options={unitOptions}
              />
              <SelectField
                label={t('wizard.day')}
                value={r.dow}
                onChange={(e) => setRule(r.key, { dow: e.target.value })}
                options={[0, 1, 2, 3, 4, 5, 6].map((d) => ({
                  value: String(d),
                  label: t(`wizard.dow.${d}` as MessageKey),
                }))}
              />
              <InputField
                label={`${t('common.time')} ${t('common.from')}`}
                type="time"
                required
                value={r.start_time}
                onChange={(e) => setRule(r.key, { start_time: e.target.value })}
              />
              <InputField
                label={`${t('common.time')} ${t('common.to')}`}
                type="time"
                required
                value={r.end_time}
                onChange={(e) => setRule(r.key, { end_time: e.target.value })}
              />
              <InputField
                label={t('common.quantity')}
                inputMode="numeric"
                required
                value={r.quantity}
                onChange={(e) => setRule(r.key, { quantity: e.target.value })}
              />
              <div className="row" style={{ alignItems: 'flex-end' }}>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => patch({ rules: draft.rules.filter((x) => x.key !== r.key) })}
                >
                  {t('common.delete')}
                </Button>
              </div>
            </div>
          </div>
        ))}
        <div>
          <Button
            size="sm"
            onClick={() =>
              patch({
                rules: [
                  ...draft.rules,
                  {
                    key: uid(),
                    unitKey: primaryUnit,
                    dow: '0',
                    start_time: '09:00',
                    end_time: '17:00',
                    quantity: '1',
                  },
                ],
              })
            }
          >
            {t('wizard.addRule')}
          </Button>
        </div>
      </div>

      <div className="stack-tight">
        <h3 style={{ margin: 0 }}>{t('wizard.overrides')}</h3>
        {draft.overrides.map((o) => (
          <div key={o.key} className="card stack-tight">
            <div className="form-grid">
              <SelectField
                label={t('wizard.ruleFor')}
                value={o.unitKey}
                onChange={(e) => setOverride(o.key, { unitKey: e.target.value })}
                options={unitOptions}
              />
              <InputField
                label={t('common.date')}
                type="date"
                required
                value={o.override_date}
                onChange={(e) => setOverride(o.key, { override_date: e.target.value })}
              />
              <SelectField
                label={t('wizard.overrideKind')}
                value={o.kind}
                onChange={(e) => setOverride(o.key, { kind: e.target.value as OverrideKind })}
                options={(['closed', 'extra', 'reduced'] as OverrideKind[]).map((k) => ({
                  value: k,
                  label: t(`wizard.kind.${k}`),
                }))}
              />
              {o.kind !== 'closed' ? (
                <>
                  <InputField
                    label={t('common.quantity')}
                    inputMode="numeric"
                    value={o.quantity}
                    onChange={(e) => setOverride(o.key, { quantity: e.target.value })}
                  />
                  <InputField
                    label={`${t('common.time')} ${t('common.from')}`}
                    type="time"
                    value={o.start_time}
                    onChange={(e) => setOverride(o.key, { start_time: e.target.value })}
                  />
                  <InputField
                    label={`${t('common.time')} ${t('common.to')}`}
                    type="time"
                    value={o.end_time}
                    onChange={(e) => setOverride(o.key, { end_time: e.target.value })}
                  />
                </>
              ) : null}
              <InputField
                label={t('common.reason')}
                value={o.reason}
                onChange={(e) => setOverride(o.key, { reason: e.target.value })}
              />
              <div className="row" style={{ alignItems: 'flex-end' }}>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() =>
                    patch({ overrides: draft.overrides.filter((x) => x.key !== o.key) })
                  }
                >
                  {t('common.delete')}
                </Button>
              </div>
            </div>
          </div>
        ))}
        <div>
          <Button
            size="sm"
            onClick={() =>
              patch({
                overrides: [
                  ...draft.overrides,
                  {
                    key: uid(),
                    unitKey: primaryUnit,
                    override_date: '',
                    kind: 'closed',
                    start_time: '',
                    end_time: '',
                    quantity: '',
                    reason: '',
                  },
                ],
              })
            }
          >
            {t('wizard.addOverride')}
          </Button>
        </div>
      </div>
    </div>
  );
}

function RulesStep({ draft, patch }: { draft: Draft; patch: (p: Partial<Draft>) => void }) {
  const { t } = useI18n();
  const scheduled = draft.capacityMode === 'scheduled';

  const setBand = (key: string, p: Partial<BandDraft>) =>
    patch({ bands: draft.bands.map((b) => (b.key === key ? { ...b, ...p } : b)) });

  return (
    <div className="stack">
      <div className="form-grid">
        <SelectField
          label={t('wizard.bookingMode')}
          value={draft.bookingMode}
          onChange={(e) => patch({ bookingMode: e.target.value as BookingMode })}
          options={[
            { value: 'instant', label: t('market.instant') },
            { value: 'request_confirm', label: t('market.requestConfirm') },
          ]}
        />
        <InputField
          label={t('wizard.holdMinutes')}
          inputMode="numeric"
          value={draft.holdMinutes}
          onChange={(e) => patch({ holdMinutes: e.target.value })}
        />
        <InputField
          label={t('wizard.minLead')}
          inputMode="numeric"
          value={draft.minLead}
          onChange={(e) => patch({ minLead: e.target.value })}
        />
        <InputField
          label={t('wizard.maxLead')}
          inputMode="numeric"
          value={draft.maxLead}
          onChange={(e) => patch({ maxLead: e.target.value })}
        />
        {scheduled ? (
          <>
            <InputField
              label={t('wizard.minDuration')}
              inputMode="numeric"
              value={draft.minDuration}
              onChange={(e) => patch({ minDuration: e.target.value })}
            />
            <InputField
              label={t('wizard.maxDuration')}
              inputMode="numeric"
              value={draft.maxDuration}
              onChange={(e) => patch({ maxDuration: e.target.value })}
            />
          </>
        ) : null}
        <InputField
          label={t('wizard.minQty')}
          inputMode="numeric"
          value={draft.minQty}
          onChange={(e) => patch({ minQty: e.target.value })}
        />
        <InputField
          label={t('wizard.maxQty')}
          inputMode="numeric"
          value={draft.maxQty}
          onChange={(e) => patch({ maxQty: e.target.value })}
        />
      </div>

      <div className="stack-tight">
        <h3 style={{ margin: 0 }}>{t('offer.cancellation')}</h3>
        {draft.bands.map((b) => (
          <div key={b.key} className="row" style={{ alignItems: 'flex-end', gap: 12 }}>
            <InputField
              label={t('wizard.policyHours')}
              inputMode="numeric"
              value={b.hours_before}
              onChange={(e) => setBand(b.key, { hours_before: e.target.value })}
            />
            <InputField
              label={t('wizard.policyRefund')}
              inputMode="numeric"
              value={b.refund_pct}
              onChange={(e) => setBand(b.key, { refund_pct: e.target.value })}
            />
            <Button
              size="sm"
              variant="ghost"
              onClick={() => patch({ bands: draft.bands.filter((x) => x.key !== b.key) })}
            >
              {t('common.delete')}
            </Button>
          </div>
        ))}
        <div>
          <Button
            size="sm"
            onClick={() =>
              patch({
                bands: [...draft.bands, { key: uid(), hours_before: '2', refund_pct: '0' }],
              })
            }
          >
            {t('wizard.addBand')}
          </Button>
        </div>
      </div>
    </div>
  );
}

function MediaStep({ draft, patch }: { draft: Draft; patch: (p: Partial<Draft>) => void }) {
  const { t } = useI18n();

  const setPhotos = (key: string, p: Partial<MediaDraft>) =>
    patch({ photos: draft.photos.map((m) => (m.key === key ? { ...m, ...p } : m)) });

  const setDocuments = (key: string, p: Partial<MediaDraft>) =>
    patch({ documents: draft.documents.map((m) => (m.key === key ? { ...m, ...p } : m)) });

  return (
    <div className="stack">
      <div className="stack-tight">
        <h3 style={{ margin: 0 }}>{t('wizard.step.media')}</h3>
        {draft.photos.map((m) => (
          <div key={m.key} className="row" style={{ alignItems: 'flex-end', gap: 12 }}>
            <InputField
              label={t('wizard.photoUrl')}
              value={m.a}
              onChange={(e) => setPhotos(m.key, { a: e.target.value })}
            />
            <InputField
              label={t('wizard.photoCaption')}
              value={m.b}
              onChange={(e) => setPhotos(m.key, { b: e.target.value })}
            />
            <Button
              size="sm"
              variant="ghost"
              onClick={() => patch({ photos: draft.photos.filter((x) => x.key !== m.key) })}
            >
              {t('common.delete')}
            </Button>
          </div>
        ))}
        <div>
          <Button
            size="sm"
            onClick={() => patch({ photos: [...draft.photos, { key: uid(), a: '', b: '', c: '' }] })}
          >
            {t('wizard.addPhoto')}
          </Button>
        </div>
      </div>

      <div className="stack-tight">
        <h3 style={{ margin: 0 }}>{t('wizard.docName')}</h3>
        {draft.documents.map((m) => (
          <div key={m.key} className="row" style={{ alignItems: 'flex-end', gap: 12 }}>
            <InputField
              label={t('wizard.docName')}
              value={m.a}
              onChange={(e) => setDocuments(m.key, { a: e.target.value })}
            />
            <InputField
              label={t('wizard.docUrl')}
              value={m.b}
              onChange={(e) => setDocuments(m.key, { b: e.target.value })}
            />
            <InputField
              label={t('wizard.docKind')}
              value={m.c}
              onChange={(e) => setDocuments(m.key, { c: e.target.value })}
            />
            <Button
              size="sm"
              variant="ghost"
              onClick={() => patch({ documents: draft.documents.filter((x) => x.key !== m.key) })}
            >
              {t('common.delete')}
            </Button>
          </div>
        ))}
        <div>
          <Button
            size="sm"
            onClick={() =>
              patch({ documents: [...draft.documents, { key: uid(), a: '', b: '', c: '' }] })
            }
          >
            {t('wizard.addDoc')}
          </Button>
        </div>
      </div>
    </div>
  );
}

function Preview({ draft }: { draft: Draft }) {
  const { t } = useI18n();
  const priceCents = Math.round((toFloatOrNull(draft.price) ?? 0) * 100);
  return (
    <div className="stack">
      <h2 style={{ margin: 0 }}>{t('wizard.summary')}</h2>
      <div className="kv">
        <span>{t('wizard.step.resource')}</span>
        <strong>{draft.resourceName}</strong>
        <span>{t('wizard.units')}</span>
        <span>{draft.units.map((u) => `${u.name || '—'} (${u.unit_label || '?'})`).join(', ')}</span>
        <span>{t('wizard.step.location')}</span>
        <span>
          {[draft.line1, draft.city, draft.state, draft.postalCode, draft.country]
            .filter(Boolean)
            .join(', ') || '—'}
        </span>
        <span>{t('wizard.timezone')}</span>
        <span className="mono">{draft.timezone}</span>
        <span>{t('offer.details')}</span>
        <span>{draft.offerTitle}</span>
        <span>{t('common.price')}</span>
        <span className="mono">{formatMoney(priceCents, draft.currency || 'USD')}</span>
        <span>{t('market.bookingMode')}</span>
        <span>
          {draft.bookingMode === 'instant' ? t('market.instant') : t('market.requestConfirm')}
        </span>
        <span>{t('offer.holdMinutes')}</span>
        <span className="mono">{draft.holdMinutes}</span>
        <span>{t('offer.cancellation')}</span>
        <span>
          {draft.bands
            .map((b) => t('book.refundPct', { pct: b.refund_pct, hours: b.hours_before }))
            .join(' · ') || t('common.none')}
        </span>
      </div>
      <div className="row row-tight">
        <Badge tone={draft.publishNow ? 'success' : 'warning'}>
          {draft.publishNow ? t('offer.status.published') : t('offer.status.draft')}
        </Badge>
        <span className="small muted">{t('wizard.stepHint.preview')}</span>
      </div>
    </div>
  );
}

/** Per-step readiness. Mirrors the server's field validators, not its error text. */
function validate(step: number, d: Draft): boolean {
  switch (step) {
    case 0:
      return !!d.categoryId;
    case 1:
      return d.resourceName.trim().length >= 2;
    case 2:
      return (
        d.units.length > 0 &&
        d.units.every(
          (u) =>
            u.name.trim().length >= 2 &&
            u.unit_label.trim().length >= 1 &&
            toInt(u.min_quantity, 0) >= 1 &&
            toInt(u.max_quantity, 0) >= toInt(u.min_quantity, 1) &&
            (d.capacityMode !== 'scheduled' || toInt(u.slot_duration_minutes, 0) >= 5),
        )
      );
    case 3:
      return (
        d.line1.trim().length >= 1 &&
        d.city.trim().length >= 1 &&
        d.timezone.trim().length >= 1 &&
        (d.country === '' || d.country.length === 2) &&
        (d.lat === '' || toFloatOrNull(d.lat) !== null) &&
        (d.lon === '' || toFloatOrNull(d.lon) !== null)
      );
    case 4:
      return (
        d.rules.length > 0 &&
        d.rules.every(
          (r) =>
            !!r.unitKey &&
            r.start_time >= '00:00' &&
            r.end_time > r.start_time &&
            toInt(r.quantity, 0) >= 1,
        ) &&
        d.overrides.every(
          (o) => !!o.unitKey && /^\d{4}-\d{2}-\d{2}$/.test(o.override_date),
        )
      );
    case 5:
      return (
        d.offerTitle.trim().length >= 3 &&
        d.offerDescription.trim().length >= 10 &&
        (toFloatOrNull(d.price) ?? -1) >= 0 &&
        d.currency.trim().length === 3
      );
    case 6: {
      const hold = toInt(d.holdMinutes, 0);
      const minD = toIntOrNull(d.minDuration);
      const maxD = toIntOrNull(d.maxDuration);
      const minQ = toIntOrNull(d.minQty);
      const maxQ = toIntOrNull(d.maxQty);
      return (
        hold >= 5 &&
        hold <= 120 &&
        toInt(d.minLead, -1) >= 0 &&
        (minD === null || minD >= 5) &&
        (maxD === null || minD === null || maxD >= minD) &&
        (minQ === null || minQ >= 1) &&
        (maxQ === null || minQ === null || maxQ >= minQ) &&
        d.bands.every(
          (b) => toInt(b.hours_before, 0) >= 1 && toInt(b.refund_pct, -1) >= 0 && toInt(b.refund_pct, 101) <= 100,
        )
      );
    }
    case 7:
      return d.photos.every((m) => m.a.trim() === '' || /^https?:\/\//i.test(m.a.trim())) &&
        d.documents.every((m) => m.b.trim() === '' || /^https?:\/\//i.test(m.b.trim()));
    default:
      return (
        validate(0, d) &&
        validate(1, d) &&
        validate(2, d) &&
        validate(3, d) &&
        validate(4, d) &&
        validate(5, d) &&
        validate(6, d) &&
        validate(7, d)
      );
  }
}

/** The publish sequence: resource + units, then availability, then the listing. */
async function runPublish(d: Draft): Promise<{ resourceId: string; offerId: string | null }> {
  const definitions: DefinitionInput[] = d.units.map((u) => ({
    name: u.name.trim(),
    unit_label: u.unit_label.trim(),
    min_quantity: toInt(u.min_quantity, 1),
    max_quantity: toInt(u.max_quantity, 1),
    slot_duration_minutes: d.capacityMode === 'scheduled' ? toInt(u.slot_duration_minutes, 60) : null,
    buffer_before_minutes: toInt(u.buffer_before_minutes, 0),
    buffer_after_minutes: toInt(u.buffer_after_minutes, 0),
  }));

  const resource = await capacityApi.create({
    category_id: d.categoryId,
    name: d.resourceName.trim(),
    description: d.resourceDescription.trim() || null,
    capacity_mode: d.capacityMode,
    address: {
      line1: d.line1.trim() || null,
      line2: d.line2.trim() || null,
      city: d.city.trim() || null,
      state: d.state.trim() || null,
      postal_code: d.postalCode.trim() || null,
      country: d.country.trim() || null,
    },
    lat: toFloatOrNull(d.lat),
    lon: toFloatOrNull(d.lon),
    timezone: d.timezone.trim(),
    photos: d.photos
      .filter((m) => m.a.trim())
      .map((m, i) => ({ url: m.a.trim(), caption: m.b.trim() || null, sort_order: i })),
    documents: d.documents
      .filter((m) => m.a.trim() && m.b.trim())
      .map((m) => ({ name: m.a.trim(), url: m.b.trim(), kind: m.c.trim() || null })),
    definitions,
  });

  const created = resource.definitions ?? [];
  // Rules were written against local unit rows; resolve them to minted ids by name.
  const idFor = (unitKey: string): string | null => {
    const local = d.units.find((u) => u.key === unitKey);
    if (!local) return null;
    const byName = created.find((c) => c.name === local.name.trim());
    const index = d.units.findIndex((u) => u.key === unitKey);
    return byName?.id ?? created[index]?.id ?? null;
  };

  for (const r of d.rules) {
    const definition_id = idFor(r.unitKey);
    if (!definition_id) continue;
    const input: RecurringAvailabilityInput = {
      definition_id,
      dow: toInt(r.dow, 0),
      start_time: r.start_time,
      end_time: r.end_time,
      quantity: toInt(r.quantity, 1),
    };
    await capacityApi.createAvailability(resource.id, input);
  }

  for (const o of d.overrides) {
    const definition_id = idFor(o.unitKey);
    if (!definition_id) continue;
    const input: OverrideInput = {
      override_date: o.override_date,
      kind: o.kind,
      start_time: o.kind === 'closed' ? null : o.start_time || null,
      end_time: o.kind === 'closed' ? null : o.end_time || null,
      quantity: o.kind === 'closed' ? null : toIntOrNull(o.quantity),
      reason: o.reason.trim() || null,
    };
    await capacityApi.createOverride(resource.id, definition_id, input);
  }

  const primary = created[0]?.id ?? null;
  if (!primary) return { resourceId: resource.id, offerId: null };

  const offer = await offerApi.create({
    definition_id: primary,
    title: d.offerTitle.trim(),
    description: d.offerDescription.trim(),
    pricing_mode: d.pricingMode,
    unit_amount_cents: Math.round((toFloatOrNull(d.price) ?? 0) * 100),
    currency: d.currency.trim(),
    booking_mode: d.bookingMode,
    hold_minutes: toInt(d.holdMinutes, 15),
    min_lead_time_minutes: toInt(d.minLead, 0),
    max_lead_time_days: toIntOrNull(d.maxLead),
    min_duration_minutes: toIntOrNull(d.minDuration),
    max_duration_minutes: toIntOrNull(d.maxDuration),
    min_quantity: toIntOrNull(d.minQty),
    max_quantity: toIntOrNull(d.maxQty),
    cancellation_policy: d.bands.map((b) => ({
      hours_before: toInt(b.hours_before, 1),
      refund_pct: toInt(b.refund_pct, 0),
    })),
  });

  if (d.publishNow) await offerApi.publish(offer.id);
  return { resourceId: resource.id, offerId: offer.id };
}
