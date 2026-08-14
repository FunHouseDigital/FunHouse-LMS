import { beforeEach, describe, expect, it } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../state/authState';
import { AppShell } from '../App';
import { AuthManager } from '../domain/authManager';
import { clearSessionKey } from '../domain/crypto';
import { DB_NAME, closeDb } from '../store/localStore';
import type { LoginResponse } from '../domain/types';

function makeJwt(claims: Record<string, unknown>): string {
  const b64url = (obj: unknown) =>
    btoa(JSON.stringify(obj)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return `${b64url({ alg: 'HS256', typ: 'JWT' })}.${b64url(claims)}.sig`;
}

function responseFor(role: string): LoginResponse {
  const exp = Date.now() + 60 * 60 * 1000;
  return {
    access_token: makeJwt({ sub: 'u1', role, location_id: 'loc-1', iat: 1, exp: Math.floor(exp / 1000) }),
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

/** Build an AuthManager already authenticated as `role`. */
async function authedManager(role: string): Promise<AuthManager> {
  const am = new AuthManager({ loginFn: async () => responseFor(role) });
  await am.login('user', 'secret');
  return am;
}

function renderApp(am: AuthManager | undefined, initialPath: string) {
  render(
    <AuthProvider authManager={am}>
      <MemoryRouter initialEntries={[initialPath]}>
        <AppShell />
      </MemoryRouter>
    </AuthProvider>,
  );
}

describe('Role-gated navigation + route guard (Req 2)', () => {
  beforeEach(async () => {
    await resetDb();
    clearSessionKey();
  });

  it('restricts a protected route to login when no valid JWT is present (Req 2.3)', () => {
    const am = new AuthManager({ loginFn: async () => responseFor('manager') });
    renderApp(am, '/players');

    // Guard redirects to /login.
    expect(screen.getByRole('button', { name: /log in/i })).toBeInTheDocument();
    expect(screen.queryByRole('navigation', { name: /primary/i })).not.toBeInTheDocument();
    expect(screen.getByRole('contentinfo', { name: 'Application release' })).toHaveTextContent(
      'Release local',
    );
  });

  it('exposes exactly the manager screens for a manager (Req 2.1, 2.4)', async () => {
    const am = await authedManager('manager');
    renderApp(am, '/log-session');

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Log Session' })).toBeInTheDocument();
    });
    expect(screen.getByRole('contentinfo', { name: 'Application release' })).toHaveTextContent(
      'Release local',
    );

    // Manager nav links present.
    for (const label of ['Log Session', 'Players', 'Today', 'Sell']) {
      expect(screen.getByRole('link', { name: label })).toBeInTheDocument();
    }
    // Founder screens excluded from nav (Req 2.4).
    for (const label of ['Revenue Dashboard', 'Attendance & Sessions', 'Metrics Entry', 'Alerts']) {
      expect(screen.queryByRole('link', { name: label })).not.toBeInTheDocument();
    }
  });

  it("redirects a manager away from a founder-only route to their home (Req 2.4)", async () => {
    const am = await authedManager('manager');
    renderApp(am, '/revenue');

    // Not the founder screen; redirected to the manager home (Log Session).
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Log Session' })).toBeInTheDocument();
    });
    expect(screen.queryByRole('heading', { name: 'Revenue Dashboard' })).not.toBeInTheDocument();
  });

  it('exposes exactly the founder screens for a founder (Req 2.2, 2.4)', async () => {
    const am = await authedManager('founder');
    renderApp(am, '/revenue');

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Revenue Dashboard' })).toBeInTheDocument();
    });

    for (const label of ['Revenue Dashboard', 'Attendance & Sessions', 'Metrics Entry', 'Alerts']) {
      expect(screen.getByRole('link', { name: label })).toBeInTheDocument();
    }
    for (const label of ['Log Session', 'Players', 'Today', 'Sell']) {
      expect(screen.queryByRole('link', { name: label })).not.toBeInTheDocument();
    }
  });

  it('redirects a founder away from a manager-only route to their home (Req 2.4)', async () => {
    const am = await authedManager('founder');
    renderApp(am, '/players');

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Revenue Dashboard' })).toBeInTheDocument();
    });
  });

  it('logging out returns to the login screen and hides role nav (Req 1.6, 2.3)', async () => {
    const user = (await import('@testing-library/user-event')).default.setup();
    const am = await authedManager('manager');
    renderApp(am, '/today');

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Today' })).toBeInTheDocument();
    });

    await user.click(screen.getByRole('button', { name: /^log out$/i }));
    expect(screen.getByRole('group', { name: /confirm logout/i })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /yes, log out/i }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /log in/i })).toBeInTheDocument();
    });
    expect(screen.queryByRole('navigation', { name: /primary/i })).not.toBeInTheDocument();
  });
});
