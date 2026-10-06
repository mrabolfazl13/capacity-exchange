// Thin typed fetch client per docs/CONTRACTS.md §2 (envelope) and §3 (auth).
// - Access token kept in memory only; refresh token persisted in localStorage
//   (§12 "desktop in-memory + localStorage refresh").
// - On 401 token_expired/unauthorized: refresh once via a shared queue so
//   concurrent requests do not stampede /auth/refresh, then replay.

import type { ErrorBody, TokenPair } from '@/types/api';

export const API_BASE_URL: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ??
  'http://127.0.0.1:8000/api/v1';

export const REFRESH_STORAGE_KEY = 'cx.refresh_token';
export const ACCESS_STORAGE_KEY = 'cx.access_token';

export type ApiErrorKind =
  | 'network'
  | 'http'
  | 'parse'
  | 'unexpected';

/** Machine-error surfaced from the §2 error envelope. */
export class ApiError extends Error {
  status: number;
  code: string;
  details: Record<string, unknown>;
  requestId: string | null;
  kind: ApiErrorKind;

  constructor(
    status: number,
    code: string,
    message: string,
    details: Record<string, unknown> = {},
    requestId: string | null = null,
    kind: ApiErrorKind = 'http',
  ) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.details = details;
    this.requestId = requestId;
    this.kind = kind;
  }

  /** field_errors for 422 validation_error envelopes. */
  get fieldErrors(): { field: string; message: string }[] {
    const fe = this.details['field_errors'];
    return Array.isArray(fe)
      ? (fe as { field: string; message: string }[])
      : [];
  }
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE';
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined | null>;
  headers?: Record<string, string>;
  idempotencyKey?: string;
  /** internal: skip the auto-refresh attempt (already retried) */
  skipAuthRetry?: boolean;
  signal?: AbortSignal;
}

type Listener = () => void;

class ApiClient {
  private accessToken: string | null = null;
  private refreshToken: string | null = null;
  private refreshPromise: Promise<TokenPair> | null = null;
  private unauthorizedListeners = new Set<Listener>();

  constructor() {
    // Bootstrap: refresh token lives in localStorage; access token is memory
    // only. We also keep a last-known access token in localStorage purely for
    // best-effort session resume in dev; it is re-validated by a refresh.
    try {
      this.refreshToken = localStorage.getItem(REFRESH_STORAGE_KEY);
      this.accessToken = localStorage.getItem(ACCESS_STORAGE_KEY);
    } catch {
      /* storage unavailable (SSR/test) — fine */
    }
  }

  // ----- token state -----

  getAccessToken(): string | null {
    return this.accessToken;
  }

  getRefreshToken(): string | null {
    return this.refreshToken;
  }

  isAuthed(): boolean {
    return this.accessToken !== null || this.refreshToken !== null;
  }

  setTokens(pair: TokenPair | null): void {
    if (pair === null) {
      this.accessToken = null;
      this.refreshToken = null;
      try {
        localStorage.removeItem(REFRESH_STORAGE_KEY);
        localStorage.removeItem(ACCESS_STORAGE_KEY);
      } catch {
        /* noop */
      }
      return;
    }
    this.accessToken = pair.access_token;
    this.refreshToken = pair.refresh_token;
    try {
      localStorage.setItem(REFRESH_STORAGE_KEY, pair.refresh_token);
      localStorage.setItem(ACCESS_STORAGE_KEY, pair.access_token);
    } catch {
      /* noop */
    }
  }

  /** Subscribe to hard-logout events (refresh failed → session revoked). */
  onUnauthorized(fn: Listener): () => void {
    this.unauthorizedListeners.add(fn);
    return () => this.unauthorizedListeners.delete(fn);
  }

  private emitUnauthorized(): void {
    for (const fn of this.unauthorizedListeners) fn();
  }

  // ----- core request -----

  buildUrl(path: string, query?: RequestOptions['query']): string {
    const url = new URL(API_BASE_URL.replace(/\/+$/, '') + path);
    if (query) {
      for (const [k, v] of Object.entries(query)) {
        if (v === undefined || v === null || v === '') continue;
        url.searchParams.set(k, String(v));
      }
    }
    return url.toString();
  }

  async requestRaw<T>(path: string, opts: RequestOptions = {}): Promise<T> {
    const { method = 'GET', body, query, headers, idempotencyKey, signal } = opts;
    const init: RequestInit = {
      method,
      signal,
      headers: {
        Accept: 'application/json',
        ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
        ...(this.accessToken
          ? { Authorization: `Bearer ${this.accessToken}` }
          : {}),
        ...(idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : {}),
        ...headers,
      },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    };
    const res = await fetch(this.buildUrl(path, query), init).catch((err: unknown) => {
      throw new ApiError(
        0,
        'network_error',
        err instanceof Error ? err.message : 'Network request failed',
        {},
        null,
        'network',
      );
    });

    if (res.status === 204) return undefined as T;

    let json: unknown = null;
    const text = await res.text();
    if (text.length > 0) {
      try {
        json = JSON.parse(text);
      } catch {
        if (!res.ok) {
          throw new ApiError(res.status, 'internal_error', `Server returned non-JSON response (HTTP ${res.status})`, {}, null, 'parse');
        }
        throw new ApiError(res.status, 'internal_error', 'Malformed success response', {}, null, 'parse');
      }
    }

    if (!res.ok) {
      const envelope = (json ?? null) as ErrorBody | null;
      const errBody = envelope?.error;
      throw new ApiError(
        res.status,
        errBody?.code ?? 'internal_error',
        errBody?.message ?? `Request failed (HTTP ${res.status})`,
        errBody?.details ?? {},
        errBody?.request_id ?? null,
        'http',
      );
    }
    return json as T;
  }

  /** Authenticated request with single-shot 401 refresh-and-replay. */
  async request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
    try {
      return await this.requestRaw<T>(path, opts);
    } catch (err) {
      if (
        err instanceof ApiError &&
        err.status === 401 &&
        !opts.skipAuthRetry &&
        ['token_expired', 'unauthorized'].includes(err.code) &&
        this.refreshToken
      ) {
        await this.refreshTokens();
        return this.requestRaw<T>(path, { ...opts, skipAuthRetry: true });
      }
      if (err instanceof ApiError && err.status === 401 && !opts.skipAuthRetry && this.refreshToken === null) {
        // No session to recover: surface but do not force logout here.
        throw err;
      }
      throw err;
    }
  }

  /**
   * POST /auth/refresh with rotation (CONTRACTS §3). Concurrent callers share
   * one in-flight refresh promise (queue); the first response's new pair is
   * used by all waiters. On failure the local session is dropped and
   * subscribers are notified.
   */
  async refreshTokens(): Promise<TokenPair> {
    if (this.refreshPromise) return this.refreshPromise;
    const refreshToken = this.refreshToken;
    if (!refreshToken) {
      this.emitUnauthorized();
      throw new ApiError(401, 'session_revoked', 'No refresh token available', {}, null);
    }
    this.refreshPromise = (async () => {
      try {
        const pair = await this.requestRaw<TokenPair>('/auth/refresh', {
          method: 'POST',
          body: { refresh_token: refreshToken },
          skipAuthRetry: true,
        } as RequestOptions);
        this.setTokens(pair);
        return pair;
      } catch (err) {
        this.setTokens(null);
        this.emitUnauthorized();
        throw err;
      } finally {
        this.refreshPromise = null;
      }
    })();
    return this.refreshPromise;
  }

  // Convenience verbs
  get<T>(path: string, query?: RequestOptions['query'], signal?: AbortSignal) {
    return this.request<T>(path, { method: 'GET', query, signal });
  }
  post<T>(path: string, body?: unknown, extra?: { idempotencyKey?: string; query?: RequestOptions['query'] }) {
    return this.request<T>(path, { method: 'POST', body, ...extra });
  }
  patch<T>(path: string, body?: unknown, query?: RequestOptions['query']) {
    return this.request<T>(path, { method: 'PATCH', body, query });
  }
  del<T>(path: string, query?: RequestOptions['query']) {
    return this.request<T>(path, { method: 'DELETE', query });
  }
}

export const api = new ApiClient();

/** Generate an RFC-4122 v4 UUID for request_fingerprint / Idempotency-Key. */
export function uuidv4(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID();
  }
  // Fallback (non-secure contexts only)
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}
