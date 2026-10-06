import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { QueryClientProvider } from '@tanstack/react-query';
import { App } from './App';
import { AuthProvider } from '@/auth/AuthProvider';
import { I18nProvider, dirFor, type Locale } from '@/i18n/index';
import { ToastProvider } from '@/components/ui/Toast';
import { createAppQueryClient } from '@/lib/queryClient';
import '@/styles/tokens.css';
import '@/styles/base.css';

const queryClient = createAppQueryClient();

// The locale switch owns the document direction, not a CSS file: a Persian catalog
// has to flip the whole shell, including portals rendered outside the tree.
const stored = ((): Locale => {
  try {
    const raw = localStorage.getItem('cx.locale');
    return raw === 'fa' ? 'fa' : 'en';
  } catch {
    return 'en';
  }
})();
document.documentElement.dir = dirFor(stored);
document.documentElement.lang = stored;

createRoot(document.getElementById('root') as HTMLElement).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <I18nProvider>
        <ToastProvider>
          <BrowserRouter>
            <AuthProvider>
              <App />
            </AuthProvider>
          </BrowserRouter>
        </ToastProvider>
      </I18nProvider>
    </QueryClientProvider>
  </StrictMode>,
);
