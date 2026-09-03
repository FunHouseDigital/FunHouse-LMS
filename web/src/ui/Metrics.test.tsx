import { beforeEach, afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../state/authState';
import { ReferenceDataProvider } from '../state/referenceDataState';
import { SyncStatusProvider } from '../state/syncState';
import { ServicesProvider } from '../state/servicesState';
import { Metrics } from './Metrics';
import { AuthManager } from '../domain/authManager';
import { clearSessionKey } from '../domain/crypto';
import {
  DB_NAME,
  closeDb,
  getActionsByStatus,
  writeCachedRead,
} from '../store/localStore';
import type { LoginResponse, PlayerOut } from '../domain/types';

function makeJwt(claims: Record<string, unknown>): string {
  const b64url = (obj: unknown) =>
    btoa(JSON.stringify(obj)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return `${b64url({ alg: 'HS256', typ: 'JWT' })}.${b64url(claims)}.sig`;
}

function founderResponse(): LoginResponse {
  const exp = Date.now() + 60 * 60 * 1000;
  return {
    access_token: makeJwt({ sub: 'u1', role: 'founder', location_id: 'loc-1', iat: 1, exp: Math.floor(exp / 1000) }),
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

async function renderMetrics() {
  const am = new AuthManager({ loginFn: async () => founderResponse() });
  await am.login('aya', 'secret');
  const owner = am.getLocalDataOwner()!;
  await writeCachedRead<PlayerOut[]>(`players:${owner.scope}`, [PLAYER], owner);
  const view = render(
    <AuthProvider authManager={am}>
      <ReferenceDataProvider>
        <SyncStatusProvider>
        <ServicesProvider scheduler={{ onEnqueue: async () => {} }}>
          <MemoryRouter>
            <Metrics />
          </MemoryRouter>
        </ServicesProvider>
        </SyncStatusProvider>
      </ReferenceDataProvider>
    </AuthProvider>,
  );
  return { view, owner };
}

describe('Metrics screen (Req 15, D1 resolved)', () => {
  beforeEach(async () => {
    await resetDb();
    clearSessionKey();
  });

  afterEach(async () => {
    cleanup();
    await closeDb();
  });

  it('lists registered players via the reused picker for each row (Req 15.1)', async () => {
    await renderMetrics();
    await waitFor(() => {
      expect(screen.getAllByRole('button', { name: /Ada Lovelace/ }).length).toBeGreaterThan(0);
    });
  });

  it('requires a selected player before a row can be saved', async () => {
    const user = userEvent.setup();
    await renderMetrics();

    const row = screen.getAllByRole('row')[1];
    // Enter a valid WPM but pick no player → this row's Save stays disabled.
    const wpm = within(row).getByLabelText(/^WPM /);
    await user.type(wpm, '55');
    expect(within(row).getByRole('button', { name: 'Save' })).toBeDisabled();
  });

  it('saves a metric keyed on player_id as a live unsynced student_metrics action (Req 15.3, D1)', async () => {
    const user = userEvent.setup();
    const { owner } = await renderMetrics();

    const row = screen.getAllByRole('row')[1];
    // Select the registered player in the same row used for entry/save.
    const playerButton = await within(row).findByRole('button', { name: /Ada Lovelace/ });
    await user.click(playerButton);

    // Enter a non-negative WPM value.
    await user.type(within(row).getByLabelText(/^WPM /), '55');

    const save = within(row).getByRole('button', { name: 'Save' });
    await waitFor(() => expect(save).toBeEnabled());
    await user.click(save);

    await waitFor(async () => {
      const unsynced = await getActionsByStatus('unsynced', owner);
      const metric = unsynced.find((a) => a.entity === 'student_metrics');
      expect(metric).toBeDefined();
      expect(metric!.status).toBe('unsynced');
      expect((metric!.payload as { player_id: string }).player_id).toBe('player-1');
      expect((metric!.payload as { metric_type: string }).metric_type).toBe('typing_wpm');
      // The natural-key timestamp is present; the personal name is not on the wire.
      expect((metric!.payload as { measured_at?: string }).measured_at).toBeDefined();
      expect((metric!.payload as Record<string, unknown>).player_name).toBeUndefined();
      // The by_player index mirror is populated for the metric.
      expect(metric!.player_id).toBe('player-1');
    });
  });
});
