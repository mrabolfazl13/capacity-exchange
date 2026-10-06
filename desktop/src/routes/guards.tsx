// Route guards: session-required and role-required (RBAC §4). Redirects keep
// the attempted location so login can bounce back.

import type { ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from '@/auth/AuthProvider';
import type { RoleKey } from '@/types/api';

export function RequireAuth({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  const location = useLocation();
  if (status === 'loading') {
    return (
      <div className="page row" role="status" aria-live="polite">
        <span className="spinner" aria-hidden="true" />
        <span className="muted">Restoring session…</span>
      </div>
    );
  }
  if (status !== 'authenticated') {
    return <Navigate to="/login" state={{ from: location.pathname }} replace />;
  }
  return <>{children}</>;
}

export function RequireRole({
  roles,
  children,
  redirectTo = '/dashboard',
}: {
  roles: RoleKey[];
  children: ReactNode;
  redirectTo?: string;
}) {
  const { user, hasRole, status } = useAuth();
  if (status === 'loading') return null;
  if (status !== 'authenticated' || !user) return <Navigate to="/login" replace />;
  if (!hasRole(...roles)) return <Navigate to={redirectTo} replace />;
  return <>{children}</>;
}
