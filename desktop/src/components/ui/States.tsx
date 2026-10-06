import type { ReactNode } from 'react';
import { Button } from '@/components/ui/Button';

export function Skeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div aria-hidden="true" className="stack-tight">
      {Array.from({ length: lines }, (_, i) => (
        <div
          key={i}
          className="skeleton"
          style={{ height: 14, width: `${88 - i * 9}%` }}
        />
      ))}
    </div>
  );
}

export function SkeletonCard() {
  return (
    <div className="card stack-tight" aria-hidden="true">
      <div className="skeleton" style={{ height: 18, width: '70%' }} />
      <div className="skeleton" style={{ height: 14, width: '90%' }} />
      <div className="skeleton" style={{ height: 14, width: '50%' }} />
      <div className="skeleton" style={{ height: 32, width: '40%', marginTop: 8 }} />
    </div>
  );
}

export function Loading({ label }: { label?: string }) {
  return (
    <div className="row" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <span className="muted">{label ?? 'Loading…'}</span>
    </div>
  );
}

export function EmptyState({
  icon = '◇',
  title,
  hint,
  action,
}: {
  icon?: ReactNode;
  title: string;
  hint?: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <div className="empty-icon" aria-hidden="true">
        {icon}
      </div>
      <h3>{title}</h3>
      {hint ? <p className="muted small">{hint}</p> : null}
      {action}
    </div>
  );
}

export function ErrorPanel({
  message,
  onRetry,
  retryLabel = 'Retry',
}: {
  message: string;
  onRetry?: () => void;
  retryLabel?: string;
}) {
  return (
    <div className="error-panel" role="alert">
      <span>{message}</span>
      {onRetry ? (
        <Button size="sm" onClick={onRetry}>
          {retryLabel}
        </Button>
      ) : null}
    </div>
  );
}
