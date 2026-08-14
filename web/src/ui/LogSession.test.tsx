import { beforeEach, describe, expect, it } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../state/authState';
import { SyncStatusProvider } from '../state/syncState';
import { ServicesProvider } from '../state/servicesState';
import { LogSession } from './LogSession';
import { AuthManager } from '../domain/authManager';
import { clearSessionKey } from '../domain/crypto';
import {
  DB_NAME,
  closeDb,
  writeBalances,
  writeCachedRead,
} from '../store/localStore';
import type { BalanceOut, LoginResponse, PlayerOut } from '../domain/types';

function makeJwt(claims: Record<string, unknown>): string {
  const b64url = (obj: unknown) =>
    btoa(JSON.stringify(obj)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return `${b64url({ alg: 'HS256', typ: 'JWT' })}.${b64url(claims)}.sig`;
}

function managerResponse(): LoginResponse {
  const exp = Date.now() + 60 * 60 * 1000;
  return {
    access_token: makeJwt({ sub: 'u1', role: 'manager', location_id: 'loc-1', iat: 1, exp: Math.floor(exp / 1000) }),
    token_type: 'bearer',
    expires_at: new Date(exp).toISOString(),
  };
}

async function resetDb(): Promise<void> {
  await closeDb();
  await new Promise<void>((resolve, reject) => {
    const req = indexedDB.deleteDatabase(DB_NAME);
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error);
    req.onblocked = () => resolve();
  });
}

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

const BALANCE: BalanceOut = {
  entitlement_id: 'ent-1',
  product_id: 'prod-1',
  remaining_units: 120,
  valid_from: '2024-01-01',
  valid_to: '2024-12-31',
  status: 'active',
};

async function renderLogSession() {
  const am = new AuthManager({ loginFn: async () => managerResponse() });
  await am.login('loyiso', 'secret');
  render(
    <AuthProvider authManager={am}>
      <SyncStatusProvider>
        <ServicesProvider scheduler={{ onEnqueue: async () => {} }}>
          <MemoryRouter>
            <LogSession />
          </MemoryRouter>
        </ServicesProvider>
      </SyncStatusProvider>
    </AuthProvider>,
  );
}

describe('Log Session screen (Req 7.1–7.6, 8.1)', () => {
  beforeEach(async () => {
    await resetDb();
    clearSessionKey();
    await writeCachedRead<PlayerOut[]>('players', [PLAYER]);
    await writeBalances(PLAYER.id, [BALANCE]);
  });

  it('presents player, console, duration, and payment controls (Req 7.1–7.5)', async () => {
    await renderLogSession();

    // Player list sourced from the Local_Store cached roster (Req 7.2).
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Ada Lovelace/ })).toBeInTheDocument();
    });

    // Console options PS5 | PS4 (Req 7.3).
    expect(screen.getByRole('radio', { name: 'PS5' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'PS4' })).toBeInTheDocument();

    // Duration presets + custom (Req 7.4).
    expect(screen.getByRole('button', { name: '20 min' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '60 min' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '120 min' })).toBeInTheDocument();
    expect(screen.getByLabelText('Custom minutes')).toBeInTheDocument();

    // Payment methods (Req 7.5).
    expect(screen.getByRole('radio', { name: 'Cash' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Entitlement draw' })).toBeInTheDocument();
  });

  it('enables confirm only when player + duration + payment are chosen (Req 7.6)', async () => {
    const user = userEvent.setup();
    await renderLogSession();

    const confirm = () => screen.getByRole('button', { name: /confirm session/i });
    expect(confirm()).toBeDisabled();

    await user.click(await screen.findByRole('button', { name: /Ada Lovelace/ }));
    expect(confirm()).toBeDisabled(); // still missing duration + payment

    await user.click(screen.getByRole('button', { name: '60 min' }));
    expect(confirm()).toBeDisabled(); // still missing payment

    await user.click(screen.getByRole('radio', { name: 'Cash' }));
    await user.type(screen.getByLabelText('Cash amount'), '30');

    await waitFor(() => expect(confirm()).toBeEnabled());
  });

  it('shows the pre-confirm entitlement balance for the selected player (Req 8.1)', async () => {
    const user = userEvent.setup();
    await renderLogSession();

    await user.click(await screen.findByRole('button', { name: /Ada Lovelace/ }));

    const balancePanel = await screen.findByRole('region', { name: /entitlement balance/i });
    expect(balancePanel).toHaveTextContent('120 min');
    expect(balancePanel).toHaveTextContent('prod-1');
  });
});
