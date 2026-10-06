import { useState, type FormEvent } from 'react';
import { Link, Navigate, useNavigate } from 'react-router-dom';
import { useAuth } from '@/auth/AuthProvider';
import { useI18n } from '@/i18n';
import { describeError } from '@/api/errors';
import { InputField, SelectField } from '@/components/ui/Field';
import { Button } from '@/components/ui/Button';

type AccountType = 'customer' | 'provider';

export function RegisterPage() {
  const { t } = useI18n();
  const { register, status } = useAuth();
  const navigate = useNavigate();
  const [accountType, setAccountType] = useState<AccountType>('customer');
  const [fullName, setFullName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [phone, setPhone] = useState('');
  const [orgName, setOrgName] = useState('');
  const [orgCountry, setOrgCountry] = useState('');
  const [orgCurrency, setOrgCurrency] = useState('USD');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (status === 'authenticated') return <Navigate to="/dashboard" replace />;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const payload: Record<string, unknown> = {
        email,
        password,
        full_name: fullName,
        phone: phone || null,
      };
      if (accountType === 'provider') {
        payload.account_type = 'provider';
        payload.organization = {
          name: orgName,
          country: orgCountry.toUpperCase() || null,
          currency: orgCurrency.toUpperCase(),
          contact_email: email,
        };
      } else {
        payload.account_type = 'customer';
      }
      await register(payload);
      navigate('/dashboard', { replace: true });
    } catch (err) {
      setError(describeError(err, t));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', padding: 24 }}>
      <form
        className="card stack"
        onSubmit={onSubmit}
        style={{ width: 520, maxWidth: '100%' }}
        aria-label={t('auth.register')}
      >
        <h1>{t('auth.register')}</h1>
        {error ? <div className="error-panel" role="alert">{error}</div> : null}
        <SelectField
          label={t('auth.accountType')}
          value={accountType}
          onChange={(e) => setAccountType(e.target.value as AccountType)}
          options={[
            { value: 'customer', label: t('auth.accountCustomer') },
            { value: 'provider', label: t('auth.accountProvider') },
          ]}
        />
        <InputField
          label={t('auth.fullName')}
          required
          value={fullName}
          onChange={(e) => setFullName(e.target.value)}
          autoComplete="name"
        />
        <InputField
          label={t('auth.email')}
          type="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          autoComplete="email"
        />
        <InputField
          label={t('auth.password')}
          type="password"
          required
          minLength={8}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="new-password"
          hint="min. 8 characters"
        />
        <InputField
          label={`${t('auth.phone')} (${t('common.optional')})`}
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
          autoComplete="tel"
        />
        {accountType === 'provider' ? (
          <div className="grid form-grid">
            <InputField
              label={t('auth.orgName')}
              required
              value={orgName}
              onChange={(e) => setOrgName(e.target.value)}
            />
            <InputField
              label={t('auth.orgCountry')}
              maxLength={2}
              value={orgCountry}
              onChange={(e) => setOrgCountry(e.target.value)}
              placeholder="DE"
            />
            <InputField
              label={t('auth.orgCurrency')}
              maxLength={3}
              value={orgCurrency}
              onChange={(e) => setOrgCurrency(e.target.value)}
            />
          </div>
        ) : null}
        <Button type="submit" variant="primary" loading={busy} className="btn-block">
          {t('auth.register')}
        </Button>
        <p className="small muted">
          {t('auth.hasAccount')} <Link to="/login">{t('auth.signIn')}</Link>
        </p>
      </form>
    </div>
  );
}
