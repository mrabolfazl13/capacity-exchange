// Toast system fed by the §2 error envelope: call toastError(err, t) anywhere.

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { describeError } from '@/api/errors';
import type { TranslateFn } from '@/i18n/index';

type ToastKind = 'success' | 'error' | 'info';

interface ToastItem {
  id: number;
  kind: ToastKind;
  message: string;
}

interface ToastApi {
  push: (kind: ToastKind, message: string) => void;
  success: (message: string) => void;
  error: (message: string) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const nextId = useRef(1);

  const remove = useCallback((id: number) => {
    setItems((xs) => xs.filter((x) => x.id !== id));
  }, []);

  const push = useCallback(
    (kind: ToastKind, message: string) => {
      const id = nextId.current++;
      setItems((xs) => [...xs.slice(-4), { id, kind, message }]);
      window.setTimeout(() => remove(id), kind === 'error' ? 6500 : 4000);
    },
    [remove],
  );

  const api = useMemo<ToastApi>(
    () => ({
      push,
      success: (m: string) => push('success', m),
      error: (m: string) => push('error', m),
    }),
    [push],
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="toast-region" aria-live="polite" aria-label="Notifications">
        {items.map((item) => (
          <div key={item.id} className={`toast toast-${item.kind}`} role="status">
            <span>{item.message}</span>
            <button
              type="button"
              className="toast-close"
              onClick={() => remove(item.id)}
              aria-label="Dismiss notification"
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast must be used inside ToastProvider');
  return ctx;
}

/** Convenience: surface any thrown error through the §2 code→message map. */
export function toastError(toasts: ToastApi, err: unknown, t: TranslateFn): void {
  toasts.error(describeError(err, t));
}
