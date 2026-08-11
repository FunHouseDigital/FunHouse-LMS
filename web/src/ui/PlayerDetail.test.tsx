import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { AuthManager } from '../domain/authManager';
import { clearSessionKey } from '../domain/crypto';
import type { ContainerApiClient } from '../api/client';
import type { LoginResponse, PlayerHistory } from '../domain/types';
import { AuthProvider } from '../state/authState';
import { ReferenceDataProvider } from '../state/referenceDataState';
import { DB_NAME, closeDb, enqueueAction } from '../store/localStore';
import { PlayerDetail } from './PlayerDetail';

const PLAYER_ID = 'player-1';
const CACHE_SCOPE = 'v1:manager-1:manager:loc-1:no-school';

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

function emptyHistory(): PlayerHistory {
  return { player_id: PLAYER_ID, sessions: [], payments: [], entitlement_draws: [] };
}

function formatExpectedDate(value: string): string {
  return new Intl.DateTimeFormat('en-ZA', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value));
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

function makeClient(getPlayerHistory: () => Promise<PlayerHistory>): ContainerApiClient {
  return {
    getPlayerHistory: vi.fn(getPlayerHistory),
    getPlayers: vi.fn(async () => []),
    getProducts: vi.fn(async () => []),
  } as unknown as ContainerApiClient;
}

async function renderDetail(client: ContainerApiClient) {
  const authManager = new AuthManager({ loginFn: async () => managerResponse() });
  await authManager.login('manager', 'secret');
  return render(
    <AuthProvider authManager={authManager} client={client}>
      <ReferenceDataProvider>
        <MemoryRouter initialEntries={[`/players/${PLAYER_ID}`]}>
          <Routes>
            <Route path="/players/:id" element={<PlayerDetail />} />
          </Routes>
        </MemoryRouter>
      </ReferenceDataProvider>
    </AuthProvider>,
  );
}

describe('Player detail history', () => {
  beforeEach(async () => {
    await resetDb();
    clearSessionKey();
  });

  it('renders readable structured server history without raw JSON', async () => {
    const history: PlayerHistory = {
      player_id: PLAYER_ID,
      sessions: [
        {
          id: 'session-1',
          session_type: 'esports',
          reference: 'PS5',
          duration_minutes: 60,
          started_at: '2025-01-10T10:00:00.000Z',
          ended_at: '2025-01-10T11:00:00.000Z',
          school_id: null,
          logged_by: 'manager-1',
          location_id: 'loc-1',
        },
      ],
      payments: [
        {
          id: 'payment-1',
          amount_cents: 25_000,
          method: 'card',
          paid_at: '2025-01-10T11:05:00.000Z',
          product_id: 'holiday-product',
          logged_by: 'manager-2',
          location_id: 'loc-1',
        },
      ],
      entitlement_draws: [
        {
          entitlement_id: 'entitlement-1',
          logged_by: 'manager-3',
          server_timestamp: '2025-01-10T11:10:00.000Z',
          client_timestamp: '2025-01-10T11:09:00.000Z',
          product_id: 'holiday-product',
        },
      ],
    };

    await renderDetail(makeClient(async () => history));

    const sessions = screen.getByRole('list', { name: 'Sessions' });
    expect(await within(sessions).findByText('Esports')).toBeInTheDocument();
    expect(within(sessions).getByText('PS5')).toBeInTheDocument();
    expect(within(sessions).getByText('60 min')).toBeInTheDocument();
    expect(within(sessions).getByText('Recorded by')).toBeInTheDocument();
    expect(within(sessions).getByText('manager-1')).toBeInTheDocument();

    const payments = screen.getByRole('list', { name: 'Payments' });
    expect(within(payments).getByText('R250.00')).toBeInTheDocument();
    expect(within(payments).getByText('holiday-product')).toBeInTheDocument();
    expect(within(payments).getByText('Recorded by')).toBeInTheDocument();
    expect(within(payments).getByText('manager-2')).toBeInTheDocument();

    const draws = screen.getByRole('list', { name: 'Entitlement draws' });
    expect(within(draws).getByText('entitlement-1')).toBeInTheDocument();
    expect(within(draws).getByText('Recorded by')).toBeInTheDocument();
    expect(within(draws).getByText('manager-3')).toBeInTheDocument();
    expect(within(draws).getByText('Server recorded at')).toBeInTheDocument();
    expect(within(draws).getByText(formatExpectedDate('2025-01-10T11:10:00.000Z'))).toBeInTheDocument();
    expect(within(draws).getByText('Client captured at')).toBeInTheDocument();
    expect(within(draws).getByText(formatExpectedDate('2025-01-10T11:09:00.000Z'))).toBeInTheDocument();
    expect(within(draws).queryByText('Amount drawn')).not.toBeInTheDocument();
    expect(within(draws).queryByText('Location')).not.toBeInTheDocument();
    expect(document.body).not.toHaveTextContent('"session_type"');
    expect(document.body).not.toHaveTextContent('{"');
  });

  it('keeps scoped local rows marked with data-local and a visible Pending sync badge', async () => {
    await enqueueAction(
      {
        client_id: 'local-session',
        entity: 'session',
        created_at: '2025-01-11T09:00:00.000Z',
        payload: {
          player_id: PLAYER_ID,
          session_type: 'lounge',
          reference: 'PS4',
          duration_minutes: 30,
          started_at: '2025-01-11T09:00:00.000Z',
        },
      },
      { scope: CACHE_SCOPE },
    );
    await enqueueAction(
      {
        client_id: 'local-payment',
        entity: 'payment',
        created_at: '2025-01-11T09:30:00.000Z',
        payload: {
          player_id: PLAYER_ID,
          amount_cents: 1_250,
          method: 'cash',
          paid_at: '2025-01-11T09:30:00.000Z',
        },
      },
      { scope: CACHE_SCOPE },
    );
    await enqueueAction(
      {
        client_id: 'local-draw',
        entity: 'entitlement',
        created_at: '2025-01-11T09:31:00.000Z',
        payload: {
          player_id: PLAYER_ID,
          entitlement_id: 'ent-local',
          amount: 30,
          client_timestamp: '2025-01-11T09:31:00.000Z',
        },
      },
      { scope: CACHE_SCOPE },
    );

    await renderDetail(makeClient(async () => emptyHistory()));

    await screen.findByRole('heading', { name: 'Sessions (1)' });
    for (const name of ['Sessions', 'Payments', 'Entitlement draws']) {
      const item = within(screen.getByRole('list', { name })).getByRole('listitem');
      expect(item).toHaveAttribute('data-local', 'true');
      expect(within(item).getByText('Pending sync')).toBeVisible();
    }
    expect(screen.getAllByText('Pending sync')).toHaveLength(3);
    expect(screen.getByText('R12.50')).toBeInTheDocument();
    const localDraws = screen.getByRole('list', { name: 'Entitlement draws' });
    expect(within(localDraws).getByText('Amount drawn')).toBeInTheDocument();
    expect(within(localDraws).getByText('30 min')).toBeInTheDocument();
    expect(within(localDraws).getByText('Client captured at')).toBeInTheDocument();
    expect(within(localDraws).getByText('Saved on this device at')).toBeInTheDocument();
  });

  it('shows an explicit loading state followed by per-section empty states', async () => {
    let resolveHistory!: (history: PlayerHistory) => void;
    const pending = new Promise<PlayerHistory>((resolve) => {
      resolveHistory = resolve;
    });

    await renderDetail(makeClient(() => pending));
    expect(screen.getByRole('status')).toHaveTextContent('Loading player history…');

    resolveHistory(emptyHistory());
    expect(await screen.findByText('No sessions recorded.')).toBeInTheDocument();
    expect(screen.getByText('No payments recorded.')).toBeInTheDocument();
    expect(screen.getByText('No entitlement draws recorded.')).toBeInTheDocument();
    expect(screen.queryByText('Loading player history…')).not.toBeInTheDocument();
  });

  it('marks rejected server history as unavailable instead of authoritatively empty', async () => {
    await renderDetail(makeClient(async () => Promise.reject(new Error('offline'))));

    expect(
      await screen.findByText(
        'Server history unavailable. Showing saved activity from this device.',
      ),
    ).toHaveAttribute('role', 'status');
    expect(screen.queryByText('No sessions recorded.')).not.toBeInTheDocument();
    expect(screen.queryByText('No payments recorded.')).not.toBeInTheDocument();
    expect(screen.queryByText('No entitlement draws recorded.')).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Sessions (0)' })).toBeInTheDocument();
  });

  it('keeps local unsynced rows visible when the server history request rejects', async () => {
    await enqueueAction(
      {
        client_id: 'offline-payment',
        entity: 'payment',
        created_at: '2025-01-12T09:30:00.000Z',
        payload: {
          player_id: PLAYER_ID,
          amount_cents: 2_500,
          method: 'cash',
          paid_at: '2025-01-12T09:30:00.000Z',
        },
      },
      { scope: CACHE_SCOPE },
    );

    await renderDetail(makeClient(async () => Promise.reject(new Error('offline'))));

    expect(
      await screen.findByText(
        'Server history unavailable. Showing saved activity from this device.',
      ),
    ).toHaveAttribute('role', 'status');
    const payments = screen.getByRole('list', { name: 'Payments' });
    const localPayment = within(payments).getByRole('listitem');
    expect(localPayment).toHaveAttribute('data-local', 'true');
    expect(within(localPayment).getByText('Pending sync')).toBeVisible();
    expect(within(localPayment).getByText('R25.00')).toBeInTheDocument();
    expect(screen.queryByText('No sessions recorded.')).not.toBeInTheDocument();
    expect(screen.queryByText('No payments recorded.')).not.toBeInTheDocument();
    expect(screen.queryByText('No entitlement draws recorded.')).not.toBeInTheDocument();
  });

  it('formats malformed optional fields as Unknown without rendering invalid dates', async () => {
    const malformed: PlayerHistory = {
      player_id: PLAYER_ID,
      sessions: [
        {
          id: 'session-malformed',
          session_type: 'lesson',
          started_at: 'not-a-date',
          ended_at: null,
          duration_minutes: Number.NaN,
          reference: null,
          school_id: null,
          logged_by: null,
          location_id: '',
        },
      ],
      payments: [{ id: 'payment-malformed', amount_cents: Number.POSITIVE_INFINITY, paid_at: 'bad' }],
      entitlement_draws: [{ id: 'draw-malformed', entitlement_id: null, server_timestamp: 'bad' }],
    };

    await renderDetail(makeClient(async () => malformed));
    await screen.findByRole('heading', { name: 'Sessions (1)' });

    expect(screen.getAllByText('Unknown').length).toBeGreaterThan(0);
    expect(document.body).not.toHaveTextContent('Invalid Date');
    expect(document.body).not.toHaveTextContent('undefined');
    expect(document.body).not.toHaveTextContent('Infinity');
    expect(document.body).not.toHaveTextContent('NaN');
  });
});
