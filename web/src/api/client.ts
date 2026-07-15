/**
 * Container_API client — the only HTTP edge of the Revenue_PWA (Req 1.5, 1.7, 17.4).
 *
 * A thin `fetch` wrapper that:
 *  - rejects non-HTTPS base URLs at construction (Req 17.4), with a documented
 *    exception for `http://localhost` / loopback so the app can run against a
 *    local dev API and in tests;
 *  - attaches `Authorization: Bearer <token>` to every request when a valid
 *    session token is supplied via `getToken` (Req 1.5);
 *  - surfaces `401` distinctly as an `UnauthorizedError` so the Auth_Manager can
 *    clear the JWT and route to login (Req 1.7).
 *
 * Request/response shapes bind to the confirmed Spec 2 contract (design.md).
 */
import type {
  Alert,
  BalanceOut,
  LoginResponse,
  PlayerHistory,
  PlayerOut,
  ProductOut,
  RevenueSummary,
  SyncAction,
  SyncResult,
} from '../domain/types';

/** Thrown on any `401` response so the Auth_Manager can react (Req 1.7). */
export class UnauthorizedError extends Error {
  readonly status = 401;
  constructor(message = 'Unauthorized') {
    super(message);
    this.name = 'UnauthorizedError';
  }
}

/** Thrown for non-2xx responses other than `401`. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly body?: unknown,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export interface ApiClientConfig {
  /** Base URL of the Container_API. Must be HTTPS (or http://localhost for dev/tests). */
  baseUrl: string;
  /** Supplies the current bearer token, or null/undefined when unauthenticated. */
  getToken?: () => string | null | undefined;
  /** Injectable fetch implementation (defaults to the global `fetch`); used by tests. */
  fetchImpl?: typeof fetch;
}

/** Query parameters accepted by `GET /revenue/summary` (Dependency D3). */
export interface RevenueSummaryParams {
  period?: 'daily' | 'weekly' | 'monthly';
  location?: string;
}

/**
 * Validate a base URL for transport security (Req 17.4).
 *
 * HTTPS is always allowed. Plain HTTP is allowed ONLY for localhost/loopback,
 * which is the documented dev-and-test exception (no real personal data leaves
 * the machine over the loopback interface). Everything else is rejected.
 */
export function isAllowedBaseUrl(baseUrl: string): boolean {
  let url: URL;
  try {
    url = new URL(baseUrl);
  } catch {
    return false;
  }
  if (url.protocol === 'https:') return true;
  if (url.protocol === 'http:') {
    return url.hostname === 'localhost' || url.hostname === '127.0.0.1' || url.hostname === '::1';
  }
  return false;
}

export class ContainerApiClient {
  private readonly baseUrl: string;
  private readonly getToken: () => string | null | undefined;
  private readonly fetchImpl: typeof fetch;

  constructor(config: ApiClientConfig) {
    if (!isAllowedBaseUrl(config.baseUrl)) {
      throw new Error(
        `Refusing to use non-HTTPS Container_API base URL: "${config.baseUrl}". ` +
          'HTTPS is required (http:// is permitted only for localhost during dev/tests).',
      );
    }
    // Normalise: drop any trailing slash so path joining is predictable.
    this.baseUrl = config.baseUrl.replace(/\/+$/, '');
    this.getToken = config.getToken ?? (() => undefined);
    this.fetchImpl = config.fetchImpl ?? globalThis.fetch.bind(globalThis);
  }

  private async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const headers: Record<string, string> = { Accept: 'application/json' };

    const token = this.getToken();
    if (token) {
      headers.Authorization = `Bearer ${token}`;
    }

    const init: RequestInit = { method, headers };
    if (body !== undefined) {
      headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(body);
    }

    const response = await this.fetchImpl(`${this.baseUrl}${path}`, init);

    if (response.status === 401) {
      throw new UnauthorizedError();
    }

    if (!response.ok) {
      let errorBody: unknown;
      try {
        errorBody = await response.json();
      } catch {
        errorBody = undefined;
      }
      throw new ApiError(response.status, `Request to ${path} failed with ${response.status}`, errorBody);
    }

    if (response.status === 204) {
      return undefined as T;
    }
    return (await response.json()) as T;
  }

  // ---- Typed endpoint methods (Confirmed Container_API contract) ----

  /** `POST /auth/login` → `{access_token, token_type, expires_at}` (Req 1.1, 1.2). */
  login(identifier: string, password: string): Promise<LoginResponse> {
    return this.request<LoginResponse>('POST', '/auth/login', { identifier, password });
  }

  /** `GET /players` → roster rows (Req 9). */
  getPlayers(): Promise<PlayerOut[]> {
    return this.request<PlayerOut[]>('GET', '/players');
  }

  /** `GET /players/{id}/entitlements` → balances in integer minutes (Req 8). */
  getPlayerEntitlements(playerId: string): Promise<BalanceOut[]> {
    return this.request<BalanceOut[]>(
      'GET',
      `/players/${encodeURIComponent(playerId)}/entitlements`,
    );
  }

  /** `GET /players/{id}/history` → sessions/payments/draws (Req 9.3). */
  getPlayerHistory(playerId: string): Promise<PlayerHistory> {
    return this.request<PlayerHistory>('GET', `/players/${encodeURIComponent(playerId)}/history`);
  }

  /** `GET /revenue/summary` → three revenue streams (Req 13). */
  getRevenueSummary(params: RevenueSummaryParams = {}): Promise<RevenueSummary> {
    const query = new URLSearchParams();
    if (params.period) query.set('period', params.period);
    if (params.location) query.set('location', params.location);
    const suffix = query.toString() ? `?${query.toString()}` : '';
    return this.request<RevenueSummary>('GET', `/revenue/summary${suffix}`);
  }

  /** `GET /alerts` → server-computed operational alerts (Req 16). */
  getAlerts(): Promise<Alert[]> {
    return this.request<Alert[]>('GET', '/alerts');
  }

  /** `GET /products` → catalog used to price the Sell flow / derive units (Req 12). */
  getProducts(): Promise<ProductOut[]> {
    return this.request<ProductOut[]>('GET', '/products');
  }

  /** `POST /sync` → per-action results reconciled by `client_id` (Req 5). */
  sync(actions: SyncAction[]): Promise<SyncResult> {
    return this.request<SyncResult>('POST', '/sync', { actions });
  }
}
