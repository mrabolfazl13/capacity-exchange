// Application shell: role-aware sidebar navigation + topbar with session menu.

import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { useAuth, isAdminUser, isProviderUser, isSupportUser } from '@/auth/AuthProvider';
import { useI18n, LOCALES, type Locale } from '@/i18n/index';
import { useNotifications } from '@/hooks/useNotifications';
import { Button } from '@/components/ui/Button';
import type { ReactNode } from 'react';

function NavItem({ to, label, icon, badge }: { to: string; label: string; icon: string; badge?: number }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}
    >
      <span aria-hidden="true">{icon}</span>
      <span className="nav-label">{label}</span>
      {badge && badge > 0 ? <span className="nav-badge">{badge > 9 ? '9+' : badge}</span> : null}
    </NavLink>
  );
}

function NavGroup({ title, children }: { title: string; children: ReactNode }) {
  return (
    <>
      <div className="nav-group-title nav-label">{title}</div>
      {children}
    </>
  );
}

export function AppLayout() {
  const { t } = useI18n();
  const { locale, setLocale } = useI18n();
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const { unread } = useNotifications();

  async function onLogout() {
    await logout();
    navigate('/login');
  }

  return (
    <div className="shell">
      <nav className="sidebar" aria-label="Primary">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            CE
          </span>
          <span className="nav-label">{t('app.name')}</span>
        </div>

        <NavItem to="/marketplace" label={t('nav.marketplace')} icon="⌕" />
        <NavGroup title={t('app.tagline')}>
          <NavItem to="/dashboard" label={t('nav.dashboard')} icon="▦" />
          <NavItem to="/bookings" label={t('nav.bookings')} icon="▤" />
          <NavItem to="/demands" label={t('nav.demands')} icon="◈" />
          <NavItem to="/messages" label={t('nav.messages')} icon="✉" />
          <NavItem to="/notifications" label={t('nav.notifications')} icon="◉" badge={unread} />
        </NavGroup>

        {isProviderUser(user) ? (
          <NavGroup title={t('nav.provider')}>
            <NavItem to="/provider" label={t('nav.providerDashboard')} icon="▲" />
            <NavItem to="/provider/new" label={t('nav.wizard')} icon="＋" />
            <NavItem to="/provider/resources" label={t('nav.resources')} icon="▦" />
            <NavItem to="/provider/offers" label={t('nav.providerOffers')} icon="◇" />
            <NavItem to="/provider/matches" label={t('nav.providerMatches')} icon="◈" />
            <NavItem to="/provider/bookings" label={t('nav.providerBookings')} icon="▥" />
            <NavItem to="/provider/reviews" label={t('nav.reviews')} icon="★" />
          </NavGroup>
        ) : null}

        {isSupportUser(user) ? (
          <NavGroup title={t('nav.admin')}>
            <NavItem to="/admin/disputes" label={t('nav.adminDisputes')} icon="⚠" />
            {isAdminUser(user) ? (
              <>
                <NavItem to="/admin/users" label={t('nav.adminUsers')} icon="◇" />
                <NavItem to="/admin/providers" label={t('nav.adminProviders')} icon="□" />
                <NavItem to="/admin/audit-logs" label={t('nav.adminAudit')} icon="≡" />
                <NavItem to="/admin/categories" label={t('nav.adminCategories')} icon="✦" />
                <NavItem to="/admin/promotions" label={t('nav.adminPromotions')} icon="%" />
              </>
            ) : null}
          </NavGroup>
        ) : null}
      </nav>

      <div className="main-area">
        <header className="topbar">
          <span className="small muted">
            {user ? `${user.full_name} · ${user.roles.join(', ')}` : ''}
          </span>
          <div className="spacer" />
          <label className="row row-tight" style={{ gap: 6 }}>
            <span className="small muted">{t('nav.language')}</span>
            <select
              className="select"
              style={{ width: 110, minHeight: 32 }}
              value={locale}
              onChange={(e) => setLocale(e.target.value as Locale)}
              aria-label={t('nav.language')}
            >
              {LOCALES.map((l) => (
                <option key={l.value} value={l.value}>
                  {l.label}
                </option>
              ))}
            </select>
          </label>
          <Button size="sm" onClick={onLogout}>
            {t('nav.logout')}
          </Button>
        </header>
        <main className="page" id="main">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
