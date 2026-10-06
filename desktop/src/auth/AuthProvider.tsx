// Session management per CONTRACTS §3/§12:
// - access token in memory (client.ts), refresh persisted; rotation handled there.
// - restore: on boot, if a stored session exists, GET /auth/me (the client
//   transparently refreshes an expired access token first).
// - hard logout when refresh fails (session_revoked / rotation reuse).

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { api } from '@/api/client';
import { authApi } from '@/api/endpoints';
import type { RoleKey, User } from '@/types/api';

export type SessionStatus = 'loading' | 'authenticated' | 'unauthenticated';

export interface AuthContextValue {
  user: User | null;
  status: SessionStatus;
  login: (email: string, password: string) => Promise<User>;
  register: (payload: Record<string, unknown>) => Promise<User>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<void>;
  hasRole: (...roles: RoleKey[]) => boolean;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [status, setStatus] = useState<SessionStatus>(
    api.isAuthed() ? 'loading' : 'unauthenticated',
  );

  const hardLogout = useCallback(() => {
    api.setTokens(null);
    setUser(null);
    setStatus('unauthenticated');
  }, []);

  const loadMe = useCallback(async (): Promise<User | null> => {
    try {
      const me = await authApi.me();
      setUser(me);
      setStatus('authenticated');
      return me;
    } catch {
      // Unreadable session: if the client still holds tokens, they were
      // invalid; drop them. Otherwise stay anonymous.
      if (api.getRefreshToken()) hardLogout();
      else setStatus('unauthenticated');
      return null;
    }
  }, [hardLogout]);

  // Session restore on boot.
  useEffect(() => {
    if (api.isAuthed()) {
      void loadMe();
    }
  }, [loadMe]);

  // Refresh failure anywhere in the client → hard logout (§3 rotation reuse).
  useEffect(() => api.onUnauthorized(hardLogout), [hardLogout]);

  const login = useCallback(
    async (email: string, password: string) => {
      const pair = await authApi.login(email, password);
      api.setTokens(pair);
      const me = await loadMe();
      if (!me) throw new Error('Session restore failed after login');
      return me;
    },
    [loadMe],
  );

  const register = useCallback(
    async (payload: Record<string, unknown>) => {
      const res = await authApi.register(payload);
      // Register returns a token pair (auto-login) + optional user object.
      if (res.access_token && res.refresh_token) {
        api.setTokens({
          access_token: res.access_token,
          refresh_token: res.refresh_token,
          token_type: 'bearer',
        });
      }
      const me = await loadMe();
      if (!me) throw new Error('Session restore failed after register');
      return me;
    },
    [loadMe],
  );

  const logout = useCallback(async () => {
    try {
      await authApi.logout(api.getRefreshToken());
    } catch {
      /* server-side revoke best-effort; local wipe always happens */
    }
    hardLogout();
  }, [hardLogout]);

  const refreshUser = useCallback(async () => {
    await loadMe();
  }, [loadMe]);

  const hasRole = useCallback(
    (...roles: RoleKey[]) => {
      if (!user) return false;
      return roles.some((r) => user.roles.includes(r));
    },
    [user],
  );

  const value = useMemo<AuthContextValue>(
    () => ({ user, status, login, register, logout, refreshUser, hasRole }),
    [user, status, login, register, logout, refreshUser, hasRole],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider');
  return ctx;
}

export function isProviderUser(user: User | null): boolean {
  return !!user && (user.roles.includes('provider') || user.roles.includes('org_admin'));
}

export function isAdminUser(user: User | null): boolean {
  return !!user && user.roles.includes('platform_admin');
}

export function isSupportUser(user: User | null): boolean {
  return !!user && (user.roles.includes('support') || user.roles.includes('platform_admin'));
}
