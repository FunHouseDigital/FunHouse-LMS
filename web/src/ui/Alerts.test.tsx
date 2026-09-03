import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within, act } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider, useAuth } from '../state/authState';
import { ReferenceDataProvider } from '../state/referenceDataState';
import { AuthManager } from '../domain/authManager';
import { Alerts as AlertsScreen } from './Alerts';
import { DB_NAME, closeDb, getCachedRead, getDb, writeCachedRead } from '../store/localStore';
import { alertsCacheKey } from '../domain/alerts';
import type { ContainerApiClient } from '../api/client';
import type { Alert, LoginResponse } from '../domain/types';

async function resetDb(): Promise<void> {
  await closeDb();
  await new Promise<void>((resolve, reject) => {
    const req = indexedDB.deleteDatabase(DB_NAME);
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error);
    req.onblocked = () => resolve();
  });
}

function setOnline(value: boolean): void {
  Object.defineProperty(navigator, 'onLine', { configurable: true, value });
}

function makeJwt(claims: Record<string, unknown>): string {
  const b64url = (obj: unknown) =>
    btoa(JSON.stringify(obj)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return `${b64url({ alg: 'HS256', typ: 'JWT' })}.${b64url(claims)}.sig`;
}

function loginResponse(): LoginResponse {
  const expiresAt = Date.now() + 60 * 60 * 1000;
  return {
    access_token: makeJwt({
      sub: 'founder-1',
      role: 'founder',
      location_id: 'loc-1',
      iat: 1,
      exp: Math.floor(expiresAt / 1000),
    }),
    token_type: 'bearer',
    expires_at: new Date(expiresAt).toISOString(),
  };
}

const ALERTS: Alert[] = [
  { type: 'no-session-in-7-days', subject_id: 'player-1', detail: 'No visit in 8 days' },
  { type: 'entitlement-expiring', subject_id: 'player-2', detail: 'Expires in 2 days' },
  { type: 'subscription-payment-due', subject_id: 'player-3', detail: 'Due tomorrow' },
  { type: 'unsynced-device-older-than-5-days', subject_id: 'device-1', detail: '6 days stale' },
];

function makeClient(opts: { alerts?: Alert[]; fail?: boolean }): ContainerApiClient {
  const getAlerts = vi.fn(async () => {
    if (opts.fail) throw new Error('offline');
    return opts.alerts ?? [];
  });
  return {
    getAlerts,
    getPlayers: vi.fn(async () => []),
    getProducts: vi.fn(async () => []),
  } as unknown as ContainerApiClient;
}

async function authenticatedManager(): Promise<AuthManager> {
  const authManager = new AuthManager({ loginFn: async () => loginResponse() });
  await authManager.login('founder', 'secret');
  return authManager;
}

async function renderAlerts(client: ContainerApiClient, authManager?: AuthManager) {
  const manager = authManager ?? await authenticatedManager();
  const view = render(
    <AuthProvider authManager={manager} client={client}>
      <ReferenceDataProvider>
        <MemoryRouter>
          <AlertsScreen />
        </MemoryRouter>
      </ReferenceDataProvider>
    </AuthProvider>,
  );
  return { view, owner: manager.getLocalDataOwner()! };
}

let replaceLogin: ((identifier: string, password: string) => Promise<unknown>) | null = null;
function AlertsWithLoginHandle() {
  const { login } = useAuth();
  replaceLogin = login;
  return <AlertsScreen />;
}

describe('Alerts view (Req 16)', () => {
  beforeEach(async () => {
    await resetDb();
    setOnline(true);
  });

  afterEach(() => setOnline(true));

  it('renders each alert type and subject from GET /alerts (Req 16.1, 16.2)', async () => {
    const client = makeClient({ alerts: ALERTS });
    const { owner } = await renderAlerts(client);

    const list = await screen.findByRole('list', { name: /operational alerts/i });
    const items = within(list).getAllByRole('listitem');
    expect(items).toHaveLength(4);

    for (const alert of ALERTS) {
      const row = list.querySelector(`[data-alert-type="${alert.type}"]`);
      expect(row).not.toBeNull();
      expect(row).toHaveTextContent(alert.subject_id);
      expect(row).toHaveTextContent(alert.detail);
    }

    // The fetched alerts are cached for offline use (Req 16.3).
    await waitFor(async () => {
      expect(await getCachedRead(alertsCacheKey(owner.scope), owner)).toBeTruthy();
    });
  });

  it('renders the last cached alerts with a cached indicator when offline (Req 16.3)', async () => {
    const authManager = await authenticatedManager();
    const owner = authManager.getLocalDataOwner()!;
    await writeCachedRead(alertsCacheKey(owner.scope), ALERTS, owner);
    setOnline(false);

    const client = makeClient({ fail: true }); // must not be reached offline
    await renderAlerts(client, authManager);

    expect(await screen.findByText(/showing cached data/i)).toBeInTheDocument();
    const list = await screen.findByRole('list', { name: /operational alerts/i });
    expect(within(list).getAllByRole('listitem')).toHaveLength(4);
  });

  it('withholds old plaintext and rejects an old request cache write after same-scope replacement', async () => {
    const manager = await authenticatedManager();
    const oldOwner = manager.getLocalDataOwner()!;
    let resolveFirst!: (alerts: Alert[]) => void;
    let calls = 0;
    const client = makeClient({ alerts: [] });
    client.getAlerts = vi.fn(() => {
      calls += 1;
      return calls === 1
        ? new Promise<Alert[]>((resolve) => { resolveFirst = resolve; })
        : Promise.resolve([]);
    });

    render(
      <AuthProvider authManager={manager} client={client}>
        <ReferenceDataProvider>
          <MemoryRouter><AlertsWithLoginHandle /></MemoryRouter>
        </ReferenceDataProvider>
      </AuthProvider>,
    );
    await waitFor(() => expect(client.getAlerts).toHaveBeenCalledTimes(1));

    await act(async () => { await replaceLogin!('founder', 'replacement-password'); });
    expect(oldOwner.isCurrent()).toBe(false);
    await act(async () => { resolveFirst(ALERTS); });

    await waitFor(() => expect(screen.queryByText('No visit in 8 days')).not.toBeInTheDocument());
    const raw = JSON.stringify(await (await getDb()).getAll('cached_reads'));
    expect(raw).not.toContain('No visit in 8 days');
  });

  it('shows an empty state when there are no alerts', async () => {
    const client = makeClient({ alerts: [] });
    await renderAlerts(client);
    expect(await screen.findByText(/no alerts/i)).toBeInTheDocument();
  });
});
