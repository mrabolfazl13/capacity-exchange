import type { HTMLAttributes, ReactNode } from 'react';

export function Card({
  hover,
  className,
  children,
  ...rest
}: HTMLAttributes<HTMLDivElement> & { hover?: boolean; children: ReactNode }) {
  return (
    <div
      className={['card', hover ? 'card-hover' : '', className ?? ''].filter(Boolean).join(' ')}
      {...rest}
    >
      {children}
    </div>
  );
}

export type BadgeTone = 'default' | 'primary' | 'success' | 'warning' | 'danger' | 'info';

export function Badge({
  tone = 'default',
  children,
}: {
  tone?: BadgeTone;
  children: ReactNode;
}) {
  const cls =
    tone === 'default'
      ? 'badge'
      : `badge badge-${tone}`;
  return <span className={cls}>{children}</span>;
}
