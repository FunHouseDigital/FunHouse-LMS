import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../state/authState';
import { RevenueDashboard } from './RevenueDashboard';
import { DB_NAME, closeDb, getCachedRead, writeCachedRead } from '../store/localStore';
import { DEFAULT_REVENUE_CACHE_KEY, revenueCacheKey } from '../domain/revenue';
import type { ContainerApiClient, RevenueSummaryParams } from '../api/client';
import type { RevenueSummary } from '../domain/types';

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

const MONTHLY: RevenueSummary = {
  pay_per_use_cents: 12500,
  subscription_cents: 35000,
  school_contracts_cents: 900000,
};
const DAILY: RevenueSummary = {
  pay_per_use_cents: 2500,
  subscription_cents: 0,
  school_contracts_cents: 0,
};
const WEEKLY: RevenueSummary = {
  pay_per_use_cents: 7000,
  subscription_cents: 35000,
  school_contracts_cents: 100000,
};

interface FakeClientOpts {
  byPeriod?: Record<string, RevenueSummary>;
  fixed?: RevenueSummary;
  fail?: boolean;
  onCall?: (params: RevenueSummaryParams) => void;
}

function makeClient(opts: FakeClientOpts): ContainerApiClient {
  const getRevenueSummary = vi.fn(async (params: RevenueSummaryParams = {}) => {
    opts.onCall?.(params);
    if (opts.fail) throw new Error('network down');
    if (opts.fixed) return opts.fixed;
    const byPeriod = opts.byPeriod ?? {};
    return byPeriod[params.period ?? 'monthly'] ?? MONTHLY;
  });
  return { getRevenueSummary } as unknown as ContainerApiClient;
}

function renderDashboard(client: ContainerApiClient) {
  return render(
    <AuthProvider client={client}>
      <MemoryRouter>
        <RevenueDashboard />
      </MemoryRouter>
    </AuthProvider>,
  );
}

describe('Revenue Dashboard (Req 13)', () => {
  beforeEach(async () => {
    await resetDb();
    setOnline(true);
  });

  afterEach(() => {
    setOnline(true);
  });

  it('renders the three revenue streams in Rand (Req 13.1)', async () => {
    // Distinct daily vs monthly ⇒ params are treated as supported.
    const client = makeClient({ byPeriod: { daily: DAILY, weekly: WEEKLY, monthly: MONTHLY } });
    renderDashboard(client);

    const list = await screen.findByRole('list', { name: /revenue streams/i });

    expect(within(list).getByText('Pay-per-use')).toBeInTheDocument();
    expect(within(list).getByText('Subscription')).toBeInTheDocument();
    expect(within(list).getByText('School contract')).toBeInTheDocument();

    // Monthly is the default selection → monthly Rand values.
    expect(within(list).getByText('R125.00')).toBeInTheDocument(); // pay-per-use
    expect(within(list).getByText('R350.00')).toBeInTheDocument(); // subscription
    expect(within(list).getByText('R9000.00')).toBeInTheDocument(); // school contract
  });

  it('always shows the school-contract stream even at R0 (Req 13.2)', async () => {
    // Distinct daily vs monthly ⇒ params supported; the default monthly summary
    // carries an R0 school-contract value, which must still be rendered.
    const client = makeClient({
      byPeriod: {
        daily: WEEKLY,
        weekly: WEEKLY,
        monthly: { pay_per_use_cents: 100, subscription_cents: 200, school_contracts_cents: 0 },
      },
    });
    renderDashboard(client);

    const list = await screen.findByRole('list', { name: /revenue streams/i });
    const schoolRow = list.querySelector('[data-stream="school_contract"]');
    expect(schoolRow).not.toBeNull();
    expect(schoolRow).toHaveTextContent('School contract');
    expect(schoolRow).toHaveTextContent('R0.00');
  });

  it('renders the last cached summary with a cached indicator when offline (Req 13.5)', async () => {
    // Seed the cache for the default (monthly, all-locations) selection.
    await writeCachedRead(revenueCacheKey('monthly', ''), MONTHLY);
    setOnline(false);

    const client = makeClient({ fail: true }); // must never be reached offline
    renderDashboard(client);

    expect(await screen.findByText(/showing cached data/i)).toBeInTheDocument();
    const list = await screen.findByRole('list', { name: /revenue streams/i });
    expect(within(list).getByText('R9000.00')).toBeInTheDocument();
  });

  it('wires the period/location selection into the query params (Req 13.3, 13.4)', async () => {
    const calls: RevenueSummaryParams[] = [];
    const client = makeClient({
      byPeriod: { daily: DAILY, weekly: WEEKLY, monthly: MONTHLY },
      onCall: (p) => calls.push(p),
    });
    const user = userEvent.setup();
    renderDashboard(client);

    // Wait for the initial monthly render.
    const list = await screen.findByRole('list', { name: /revenue streams/i });
    await within(list).findByText('R125.00');

    // Switch to daily → the daily summary is fetched and displayed.
    await user.selectOptions(screen.getByLabelText('Period'), 'daily');
    await waitFor(() =>
      expect(calls.some((c) => c.period === 'daily' && !c.location)).toBe(true),
    );
    await within(await screen.findByRole('list', { name: /revenue streams/i })).findByText('R25.00');

    // Provide a location → it is passed through as the location query param.
    await user.type(screen.getByLabelText('Location'), 'loc-9');
    await waitFor(() =>
      expect(calls.some((c) => c.location === 'loc-9')).toBe(true),
    );

    // Each (period, location) result is cached under its own key (Req 13.5).
    await waitFor(async () => {
      expect(await getCachedRead(revenueCacheKey('daily', 'loc-9'))).toBeTruthy();
    });
  });

  it('D3 fallback: disables filters when the endpoint ignores params', async () => {
    // A fixed summary regardless of params ⇒ probe sees daily == monthly ⇒ ignored.
    const client = makeClient({ fixed: MONTHLY });
    renderDashboard(client);

    expect(await screen.findByText(/filters unavailable/i)).toBeInTheDocument();
    expect(screen.getByLabelText('Period')).toBeDisabled();
    expect(screen.getByLabelText('Location')).toBeDisabled();

    // The default scoped summary is still rendered.
    const list = await screen.findByRole('list', { name: /revenue streams/i });
    expect(within(list).getByText('R9000.00')).toBeInTheDocument();

    // Cached under the default fallback key.
    await waitFor(async () => {
      expect(await getCachedRead(DEFAULT_REVENUE_CACHE_KEY)).toBeTruthy();
    });
  });

  it('D3 fallback: on fetch failure falls back to the cached default summary', async () => {
    await writeCachedRead(DEFAULT_REVENUE_CACHE_KEY, MONTHLY);
    const client = makeClient({ fail: true });
    renderDashboard(client);

    expect(await screen.findByText(/showing cached data/i)).toBeInTheDocument();
    expect(screen.getByLabelText('Period')).toBeDisabled();
    const list = await screen.findByRole('list', { name: /revenue streams/i });
    expect(within(list).getByText('R9000.00')).toBeInTheDocument();
  });
});
