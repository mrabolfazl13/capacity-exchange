import { useState, type FormEvent } from 'react';
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '@/auth/AuthProvider';
import { useI18n } from '@/i18n';
import { describeError } from '@/api/errors';
import { InputField } from '@/components/ui/Field';
import { Button } from '@/components/ui/Button';
import { ApiError } from '@/api/client';

export function LoginPage() {
  const { t } = useI18n();
  const { login, status } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (status === 'authenticated') return <Navigate to="/dashboard" replace />;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      const from = (location.state as { from?: string } | null)?.from;
      navigate(from ?? '/dashboard', { replace: true });
    } catch (err) {
      setError(
        err instanceof ApiError && err.code === 'invalid_credentials'
          ? t('err.invalid_credentials')
          : describeError(err, t),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', padding: 24 }}>
      <form className="card stack" onSubmit={onSubmit} style={{ width: 400, maxWidth: '100%' }} aria-label={t('auth.login')}>
        <div>
          <h1>{t('app.name')}</h1>
          <p className="muted small">{t('app.tagline')}</p>
        </div>
        {error ? (
          <div className="error-panel" role="alert">
            {error}
          </div>
        ) : null}
        <InputField
          label={t('auth.email')}
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <InputField
          label={t('auth.password')}
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <Button type="submit" variant="primary" loading={busy} className="btn-block">
          {t('auth.login')}
        </Button>
        <p className="small muted">
          {t('auth.noAccount')}{' '}
          <Link to="/register">{t('auth.signUp')}</Link>
        </p>
      </form>
    </div>
  );
}
