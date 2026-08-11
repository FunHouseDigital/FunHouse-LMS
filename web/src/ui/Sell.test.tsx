import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { AuthManager } from '../domain/authManager';
import { clearSessionKey } from '../domain/crypto';
import type { ContainerApiClient } from '../api/client';
import type { LoginResponse, PlayerOut, ProductOut } from '../domain/types';
import { AuthProvider } from '../state/authState';
import { ReferenceDataProvider } from '../state/referenceDataState';
import { ServicesProvider } from '../state/servicesState';
import { SyncStatusProvider } from '../state/syncState';
import {
  DB_NAME,
  closeDb,
  getAllLocalRecords,
  writeCachedRead,
} from '../store/localStore';
import { Sell } from './Sell';

const CACHE_SCOPE = 'v1:manager-1:manager:loc-1:no-school';
const PLAYERS_KEY = `players:${CACHE_SCOPE}`;
const PRODUCTS_KEY = `products:${CACHE_SCOPE}`;

const PLAYER: PlayerOut = {
  id: 'player-1',
  first_name: 'Ada',
  last_name: 'Lovelace',
  birth_date: null,
  grade: null,
  school_id: null,
  location_id: 'loc-1',
  consent_status: 'complete',
  active: true,
};

function product(overrides: Partial<ProductOut>): ProductOut {
  return {
    id: 'product-1',
    name: 'Product',
    type: 'pay_per_use',
    price_cents: 1_000,
    rules: {},
    location_id: 'loc-1',
    ...overrides,
  };
}

function makeJwt(claims: Record<string, unknown>): string {
  const b64url = (obj: unknown) =>
    btoa(JSON.stringify(obj)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return `${b64url({ alg: 'HS256', typ: 'JWT' })}.${b64url(claims)}.sig`;
}

function managerResponse(): LoginResponse {
  const expiresAt = Date.now() + 60 * 60 * 1000;
  return {
    access_token: makeJwt({
      sub: 'manager-1',
      role: 'manager',
      location_id: 'loc-1',
      exp: Math.floor(expiresAt / 1000),
    }),
    token_type: 'bearer',
    expires_at: new Date(expiresAt).toISOString(),
  };
}

async function resetDb(): Promise<void> {
  await closeDb();
  await new Promise<void>((resolve, reject) => {
    const request = indexedDB.deleteDatabase(DB_NAME);
    request.onsuccess = () => resolve();
    request.onerror = () => reject(request.error);
    request.onblocked = () => resolve();
  });
}

function makeClient(products: ProductOut[], players: PlayerOut[] = [PLAYER]): ContainerApiClient {
  return {
    getPlayers: vi.fn(async () => players),
    getProducts: vi.fn(async () => products),
  } as unknown as ContainerApiClient;
}

async function renderSell(products: ProductOut[], players: PlayerOut[] = [PLAYER]) {
  await writeCachedRead(PLAYERS_KEY, players);
  await writeCachedRead(PRODUCTS_KEY, products);
  const authManager = new AuthManager({ loginFn: async () => managerResponse() });
  await authManager.login('manager', 'secret');

  return render(
    <AuthProvider authManager={authManager} client={makeClient(products, players)}>
      <ReferenceDataProvider>
        <SyncStatusProvider>
          <ServicesProvider scheduler={{ onEnqueue: async () => {} }}>
            <MemoryRouter>
              <Sell />
            </MemoryRouter>
          </ServicesProvider>
        </SyncStatusProvider>
      </ReferenceDataProvider>
    </AuthProvider>,
  );
}

async function selectPlayer(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  const playerButton = await waitFor(() =>
    within(screen.getByRole('list', { name: 'Players' })).getByRole('button', {
      name: 'Ada Lovelace',
    }),
  );
  await user.click(playerButton);
}

describe('Sell screen catalog prices', () => {
  beforeEach(async () => {
    await resetDb();
    clearSessionKey();
  });

  it('selects the named once_off_pass Holiday Special regardless of catalog order', async () => {
    const unrelated = product({
      id: 'unrelated-pass',
      name: 'Birthday Pass',
      type: 'once_off_pass',
      price_cents: 9_999,
    });
    const holiday = product({
      id: 'holiday-pass',
      name: 'Holiday Special',
      type: 'once_off_pass',
      price_cents: 25_000,
    });
    const user = userEvent.setup();
    await renderSell([unrelated, holiday]);

    const holidayRadio = screen.getByRole('radio', { name: /Holiday Special/ });
    await user.click(holidayRadio);
    expect(holidayRadio.closest('label')).toHaveTextContent('R250.00');
    expect(holidayRadio.closest('label')).not.toHaveTextContent('R99.99');

    await selectPlayer(user);
    const complete = screen.getByRole('button', { name: 'Complete sale' });
    expect(complete).toBeEnabled();
    await user.click(complete);

    await waitFor(async () => {
      const payments = await getAllLocalRecords('payments', CACHE_SCOPE);
      expect(payments).toHaveLength(1);
      expect(payments[0]).toMatchObject({
        amount_cents: 25_000,
        product_id: 'holiday-pass',
      });
    });
  });

  it('rejects an out-of-contract holiday_special product type', async () => {
    const user = userEvent.setup();
    await renderSell([
      product({
        id: 'legacy-holiday',
        name: 'Holiday Special',
        type: 'holiday_special',
        price_cents: 25_000,
      }),
    ]);

    const holidayRadio = screen.getByRole('radio', { name: /Holiday Special/ });
    await user.click(holidayRadio);
    expect(holidayRadio.closest('label')).toHaveTextContent('Unavailable');

    await selectPlayer(user);
    expect(screen.getByRole('button', { name: 'Complete sale' })).toBeDisabled();
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Price unavailable. Refresh data before completing this sale.',
    );
  });

  it.each([
    {
      caseName: 'only an unrelated once-off pass exists',
      products: [product({ id: 'other-pass', name: 'Birthday Pass', type: 'once_off_pass', price_cents: 5_000 })],
    },
    {
      caseName: 'the Holiday name uses different casing',
      products: [product({ id: 'holiday-case', name: 'HOLIDAY SPECIAL', type: 'once_off_pass', price_cents: 25_000 })],
    },
    {
      caseName: 'the Holiday name has surrounding whitespace',
      products: [product({ id: 'holiday-space', name: ' Holiday Special ', type: 'once_off_pass', price_cents: 25_000 })],
    },
    {
      caseName: 'the named Holiday price is a wrong positive amount',
      products: [product({ id: 'holiday-stale', name: 'Holiday Special', type: 'once_off_pass', price_cents: 20_000 })],
    },
    {
      caseName: 'the named Holiday price is non-finite',
      products: [product({ id: 'holiday-invalid', name: 'Holiday Special', type: 'once_off_pass', price_cents: Number.POSITIVE_INFINITY })],
    },
    {
      caseName: 'the named Holiday price is zero',
      products: [product({ id: 'holiday-zero', name: 'Holiday Special', type: 'once_off_pass', price_cents: 0 })],
    },
    {
      caseName: 'the named Holiday price is negative',
      products: [product({ id: 'holiday-negative', name: 'Holiday Special', type: 'once_off_pass', price_cents: -1 })],
    },
  ])('disables completion and shows guidance when $caseName', async ({ products }) => {
    const user = userEvent.setup();
    await renderSell(products);

    const holidayRadio = screen.getByRole('radio', { name: /Holiday Special/ });
    await user.click(holidayRadio);
    await selectPlayer(user);

    expect(holidayRadio.closest('label')).toHaveTextContent('Unavailable');
    expect(screen.getByRole('button', { name: 'Complete sale' })).toBeDisabled();
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Price unavailable. Refresh data before completing this sale.',
    );
  });

  it('accepts any finite positive cached subscription price and records that catalog amount', async () => {
    const user = userEvent.setup();
    await renderSell([
      product({
        id: 'subscription-flexible',
        name: 'Subscription',
        type: 'subscription',
        price_cents: 12_345,
      }),
    ]);

    const subscriptionRadio = screen.getByRole('radio', { name: /New subscription/ });
    await user.click(subscriptionRadio);
    expect(subscriptionRadio.closest('label')).toHaveTextContent('R123.45');

    await selectPlayer(user);
    const complete = screen.getByRole('button', { name: 'Complete sale' });
    expect(complete).toBeEnabled();
    await user.click(complete);

    await waitFor(async () => {
      const payments = await getAllLocalRecords('payments', CACHE_SCOPE);
      expect(payments[0]).toMatchObject({
        amount_cents: 12_345,
        product_id: 'subscription-flexible',
      });
    });
  });

  it('shows player loading during cache hydration without announcing an empty roster', async () => {
    await renderSell([]);

    expect(screen.getByRole('status')).toHaveTextContent('Loading players…');
    expect(screen.queryByText('No players are available yet.')).not.toBeInTheDocument();

    expect(await screen.findByRole('button', { name: 'Ada Lovelace' })).toBeInTheDocument();
    expect(screen.queryByText('Loading players…')).not.toBeInTheDocument();
    expect(screen.queryByText('No players are available yet.')).not.toBeInTheDocument();
  });

  it('announces no players only after an empty cache has hydrated', async () => {
    await renderSell([], []);

    expect(screen.getByRole('status')).toHaveTextContent('Loading players…');
    expect(screen.queryByText('No players are available yet.')).not.toBeInTheDocument();

    expect(await screen.findByText('No players are available yet.')).toBeInTheDocument();
    expect(screen.queryByText('Loading players…')).not.toBeInTheDocument();
  });

  it('keeps blank pay-per-use disabled and records a valid decimal Rand value as integer cents', async () => {
    const user = userEvent.setup();
    await renderSell([]);
    await selectPlayer(user);

    const complete = screen.getByRole('button', { name: 'Complete sale' });
    const cashAmount = screen.getByLabelText('Cash amount');
    expect(cashAmount).toHaveValue(null);
    expect(complete).toBeDisabled();

    await user.type(cashAmount, '12.34');
    expect(complete).toBeEnabled();
    await user.click(complete);

    expect(await screen.findByRole('status', { name: '' })).toHaveTextContent('Sale recorded');
    await waitFor(async () => {
      const payments = await getAllLocalRecords('payments', CACHE_SCOPE);
      expect(payments).toHaveLength(1);
      expect(payments[0].amount_cents).toBe(1_234);
    });
  });
});
