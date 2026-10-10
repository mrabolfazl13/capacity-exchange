// Phase 12 coverage: the client-side RBAC that decides who reaches the moderation
// surface. The screens themselves are proven against a live backend in the browser
// (docs/TESTING_STRATEGY.md, layer 4); what the suite can pin cheaply is the gate in
// front of them, because a screen that is reachable by the wrong role is a privilege
// bug no amount of rendering tests would catch.

import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { RequireRole } from '@/routes/guards';
import { AppLayout } from '@/components/AppLayout';
import { I18nProvider } from '@/i18n/index';
import type { RoleKey } from '@/types/api';

const state = vi.hoisted(() => ({ roles: [] as string[] }));

vi.mock('@/auth/AuthProvider', async (importOriginal) => {
  const mod = await importOriginal<typeof import('@/auth/AuthProvider')>();
  return {
    ...mod,
    useAuth: () => {
      const roles = state.roles as RoleKey[];
      return {
        user: {
          id: '00000000-0000-0000-0000-000000000001',
          tenant_id: '00000000-0000-0000-0000-000000000002',
          email: 'session@capacityexchange.test',
          phone: null,
          full_name: 'Session Subject',
          preferred_locale: 'en',
          is_active: true,
          last_login_at: null,
          created_at: '2026-10-01T00:00:00Z',
          roles,
          active_org_id: null,
        },
        status: 'authenticated',
        login: async () => ({}),
        register: async () => ({}),
        logout: async () => {},
        refreshUser: async () => {},
        hasRole: (...wanted: RoleKey[]) => wanted.some((r) => roles.includes(r)),
      };
    },
  };
});

vi.mock('@/hooks/useNotifications', () => ({
  useNotifications: () => ({ unread: 0, live: false }),
}));

const ADMIN_ROLES: RoleKey[] = ['platform_admin'];
const DISPUTE_ROLES: RoleKey[] = ['platform_admin', 'support'];

function guardAs(roles: RoleKey[], allowed: RoleKey[]) {
  state.roles = roles;
  return render(
    <I18nProvider>
      <MemoryRouter initialEntries={['/protected']}>
        <Routes>
          <Route
            path="/protected"
            element={
              <RequireRole roles={allowed} redirectTo="/kicked">
                <p>the screen</p>
              </RequireRole>
            }
          />
          <Route path="/kicked" element={<p>redirected</p>} />
          <Route path="/dashboard" element={<p>home</p>} />
        </Routes>
      </MemoryRouter>
    </I18nProvider>,
  );
}

function navFor(roles: RoleKey[]) {
  state.roles = roles;
  return render(
    <I18nProvider>
      <MemoryRouter initialEntries={['/dashboard']}>
        <Routes>
          <Route path="/" element={<AppLayout />}>
            <Route path="/dashboard" element={<p>content</p>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </I18nProvider>,
  );
}

const ADMIN_LINKS = ['Users', 'Providers', 'Disputes', 'Audit Log', 'Categories', 'Promotions'];

describe('RequireRole', () => {
  it('lets a platform admin through to a platform-only screen', () => {
    guardAs(['platform_admin', 'customer'], ADMIN_ROLES);
    expect(screen.getByText('the screen')).toBeInTheDocument();
  });

  it('kicks a support session off a platform-only screen without rendering it', () => {
    guardAs(['support', 'customer'], ADMIN_ROLES);
    expect(screen.queryByText('the screen')).not.toBeInTheDocument();
    expect(screen.getByText('redirected')).toBeInTheDocument();
  });

  it('keeps disputes reachable for support, which is the one admin screen it may open', () => {
    guardAs(['support', 'customer'], DISPUTE_ROLES);
    expect(screen.getByText('the screen')).toBeInTheDocument();
  });

  it('sends an unqualified session to the default target rather than nowhere', () => {
    guardAs(['customer'], ADMIN_ROLES);
    expect(screen.getByText('redirected')).toBeInTheDocument();
  });
});

describe('the admin navigation group', () => {
  it('shows a platform admin the whole moderation surface', () => {
    navFor(['platform_admin', 'customer']);
    for (const link of ADMIN_LINKS) expect(screen.getByText(link)).toBeInTheDocument();
  });

  it('offers support the dispute queue and nothing else', () => {
    navFor(['support', 'customer']);
    expect(screen.getByText('Disputes')).toBeInTheDocument();
    for (const link of ADMIN_LINKS.filter((l) => l !== 'Disputes')) {
      expect(screen.queryByText(link)).not.toBeInTheDocument();
    }
  });

  it('hides the admin group entirely from an ordinary account', () => {
    navFor(['customer']);
    for (const link of ADMIN_LINKS) expect(screen.queryByText(link)).not.toBeInTheDocument();
  });

  it('shows the provider group to a provider', () => {
    navFor(['provider', 'customer']);
    expect(screen.getByText('Provider Workspace')).toBeInTheDocument();
  });

  it('does not show the provider group to a platform admin, who has no org to run', () => {
    navFor(['platform_admin', 'customer']);
    expect(screen.queryByText('Provider Workspace')).not.toBeInTheDocument();
  });
});
