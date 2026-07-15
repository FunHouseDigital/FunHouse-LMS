import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../state/authState';
import { Alerts as AlertsScreen } from './Alerts';
import { DB_NAME, closeDb, getCachedRead, writeCachedRead } from '../store/localStore';
import type { ContainerApiClient } from '../api/client';
import type { Alert } from '../domain/types';

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
  return { getAlerts } as unknown as ContainerApiClient;
}

function renderAlerts(client: ContainerApiClient) {
  return render(
    <AuthProvider client={client}>
      <MemoryRouter>
        <AlertsScreen />
      </MemoryRouter>
    </AuthProvider>,
  );
}

describe('Alerts view (Req 16)', () => {
  beforeEach(async () => {
    await resetDb();
    setOnline(true);
  });

  afterEach(() => setOnline(true));

  it('renders each alert type and subject from GET /alerts (Req 16.1, 16.2)', async () => {
    const client = makeClient({ alerts: ALERTS });
    renderAlerts(client);

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
      expect(await getCachedRead('alerts')).toBeTruthy();
    });
  });

  it('renders the last cached alerts with a cached indicator when offline (Req 16.3)', async () => {
    await writeCachedRead('alerts', ALERTS);
    setOnline(false);

    const client = makeClient({ fail: true }); // must not be reached offline
    renderAlerts(client);

    expect(await screen.findByText(/showing cached data/i)).toBeInTheDocument();
    const list = await screen.findByRole('list', { name: /operational alerts/i });
    expect(within(list).getAllByRole('listitem')).toHaveLength(4);
  });

  it('shows an empty state when there are no alerts', async () => {
    const client = makeClient({ alerts: [] });
    renderAlerts(client);
    expect(await screen.findByText(/no alerts/i)).toBeInTheDocument();
  });
});
