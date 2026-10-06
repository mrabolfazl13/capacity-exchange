// Vitest setup. The app guards `typeof EventSource` and reads `localStorage` through
// try/catch, so jsdom needs no shims beyond the matcher extensions and teardown.

import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach, vi } from 'vitest';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
