import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
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

  afterEach(() => {
    vi.unstubAllGlobals();
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

    const nav = screen.getByRole('navigation', { name: /primary/i });
    expect(within(nav).getAllByRole('link').map((link) => link.textContent)).toEqual([
      'Log Session',
      'Players',
      'Today',
      'Sell',
      'Field acceptance',
    ]);
    // Founder-only screens excluded from nav (Req 2.4).
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

    const nav = screen.getByRole('navigation', { name: /primary/i });
    expect(within(nav).getAllByRole('link').map((link) => link.textContent)).toEqual([
      'Revenue Dashboard',
      'Attendance & Sessions',
      'Metrics Entry',
      'Alerts',
      'Field acceptance',
    ]);
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

  it.each(['manager', 'founder'])('allows a %s to open the shared field-acceptance route', async (role) => {
    const am = await authedManager(role);
    renderApp(am, '/field-acceptance');

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Field acceptance rehearsal' })).toBeInTheDocument();
    });
    expect(screen.getByRole('link', { name: 'Field acceptance' })).toHaveAttribute(
      'aria-current',
      'page',
    );
  });

  it.each(['/field-acceptance', '/field-acceptance/', '/FIELD-ACCEPTANCE'])(
    'isolates the field-acceptance route variant %s from retry, reference-data, and protected network actions',
    async (path) => {
      const fetchMock = vi.fn(async () => {
        throw new Error('The read-only field-acceptance route must not fetch');
      });
      vi.stubGlobal('fetch', fetchMock);
      const am = await authedManager('manager');
      renderApp(am, path);

      await waitFor(() => {
        expect(screen.getByRole('heading', { name: 'Field acceptance rehearsal' })).toBeInTheDocument();
      });
      await act(async () => {
        await new Promise((resolve) => window.setTimeout(resolve, 0));
      });

      expect(screen.queryByRole('button', { name: /retry sync/i })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /refresh data/i })).not.toBeInTheDocument();
      expect(screen.queryByRole('region', { name: /sync status/i })).not.toBeInTheDocument();
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it('excludes the facilitator and redirects direct access to facilitator home', async () => {
    const am = await authedManager('facilitator');
    renderApp(am, '/field-acceptance');

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Attendance & Sessions' })).toBeInTheDocument();
    });
    expect(screen.queryByRole('heading', { name: 'Field acceptance rehearsal' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Field acceptance' })).not.toBeInTheDocument();
    const nav = screen.getByRole('navigation', { name: /primary/i });
    expect(within(nav).getAllByRole('link').map((link) => link.textContent)).toEqual([
      'Attendance & Sessions',
      'Learners',
      'Metrics Entry',
    ]);
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
