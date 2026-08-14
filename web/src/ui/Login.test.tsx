import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../state/authState';
import { AppShell } from '../App';
import { Login } from './Login';
import { AuthManager } from '../domain/authManager';
import { UnauthorizedError } from '../api/client';
import { clearSessionKey } from '../domain/crypto';
import { DB_NAME, closeDb } from '../store/localStore';
import type { LoginResponse } from '../domain/types';

function makeJwt(claims: Record<string, unknown>): string {
  const b64url = (obj: unknown) =>
    btoa(JSON.stringify(obj)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return `${b64url({ alg: 'HS256', typ: 'JWT' })}.${b64url(claims)}.sig`;
}

function successResponse(role: string): LoginResponse {
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

function renderLogin(loginFn: (id: string, pw: string) => Promise<LoginResponse>) {
  const authManager = new AuthManager({ loginFn });
  render(
    <AuthProvider authManager={authManager}>
      <MemoryRouter initialEntries={['/login']}>
        <Login />
      </MemoryRouter>
    </AuthProvider>,
  );
  return authManager;
}

describe('Login screen (Req 1.1, 1.3, 1.4)', () => {
  beforeEach(async () => {
    await resetDb();
    clearSessionKey();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('blocks submit and shows field-level validation on empty input (Req 1.4)', async () => {
    const user = userEvent.setup();
    const loginFn = vi.fn(async () => successResponse('manager'));
    renderLogin(loginFn);

    await user.click(screen.getByRole('button', { name: /log in/i }));

    // Validation messages appear and the API is never called.
    const alerts = await screen.findAllByRole('alert');
    expect(alerts.length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('Identifier is required')).toBeInTheDocument();
    expect(screen.getByText('Password is required')).toBeInTheDocument();
    expect(loginFn).not.toHaveBeenCalled();
  });

  it('blocks submit when only one field is filled (Req 1.4)', async () => {
    const user = userEvent.setup();
    const loginFn = vi.fn(async () => successResponse('manager'));
    renderLogin(loginFn);

    await user.type(screen.getByLabelText('Identifier'), 'loyiso');
    await user.click(screen.getByRole('button', { name: /log in/i }));

    expect(screen.getByText('Password is required')).toBeInTheDocument();
    expect(loginFn).not.toHaveBeenCalled();
  });

  it('shows a generic "Invalid credentials" message on a failed (401) login (Req 1.3)', async () => {
    const user = userEvent.setup();
    const loginFn = vi.fn(async () => {
      throw new UnauthorizedError();
    });
    renderLogin(loginFn);

    await user.type(screen.getByLabelText('Identifier'), 'loyiso');
    await user.type(screen.getByLabelText('Password'), 'wrong');
    await user.click(screen.getByRole('button', { name: /log in/i }));

    expect(await screen.findByTestId('form-error')).toHaveTextContent('Invalid credentials');
    expect(loginFn).toHaveBeenCalledWith('loyiso', 'wrong');
  });

  it('trims the identifier without changing the password', async () => {
    const user = userEvent.setup();
    const loginFn = vi.fn(async () => {
      throw new UnauthorizedError();
    });
    renderLogin(loginFn);

    await user.type(screen.getByLabelText('Identifier'), '  Loyiso  ');
    await user.type(screen.getByLabelText('Password'), ' secret ');
    await user.click(screen.getByRole('button', { name: /log in/i }));

    await screen.findByTestId('form-error');
    expect(loginFn).toHaveBeenCalledWith('Loyiso', ' secret ');
  });

  it('shows an actionable error and re-enables submit after an unexpected failure', async () => {
    const user = userEvent.setup();
    const loginFn = vi.fn(async () => {
      throw new TypeError('Failed to fetch');
    });
    renderLogin(loginFn);

    await user.type(screen.getByLabelText('Identifier'), 'Loyiso');
    await user.type(screen.getByLabelText('Password'), 'secret');
    await user.click(screen.getByRole('button', { name: /log in/i }));

    expect(await screen.findByTestId('form-error')).toHaveTextContent(
      'Unable to sign in. Check your connection and try again.',
    );
    expect(screen.getByRole('button', { name: 'Log in' })).toBeEnabled();
  });

  it('does not activate a session when mandatory cache isolation fails', async () => {
    const user = userEvent.setup();
    vi.stubGlobal('caches', {
      delete: vi.fn(async () => {
        throw new Error('CacheStorage unavailable');
      }),
    });
    const loginFn = vi.fn(async () => successResponse('manager'));
    const authManager = renderLogin(loginFn);

    await user.type(screen.getByLabelText('Identifier'), 'Loyiso');
    await user.type(screen.getByLabelText('Password'), 'secret');
    await user.click(screen.getByRole('button', { name: /log in/i }));

    expect(await screen.findByTestId('form-error')).toHaveTextContent(
      'Unable to sign in. Check your connection and try again.',
    );
    expect(loginFn).not.toHaveBeenCalled();
    expect(authManager.getToken()).toBeNull();
    expect(screen.getByRole('button', { name: 'Log in' })).toBeEnabled();
  });

  it('routes to the role home on a successful login (Req 1.2, 2.1)', async () => {
    const user = userEvent.setup();
    const authManager = new AuthManager({ loginFn: async () => successResponse('manager') });
    render(
      <AuthProvider authManager={authManager}>
        <MemoryRouter initialEntries={['/login']}>
          <AppShell />
        </MemoryRouter>
      </AuthProvider>,
    );

    // On the login screen first (no nav chrome while unauthenticated).
    expect(screen.getByRole('button', { name: /log in/i })).toBeInTheDocument();
    expect(screen.queryByRole('navigation', { name: /primary/i })).not.toBeInTheDocument();

    await user.type(screen.getByLabelText('Identifier'), 'loyiso');
    await user.type(screen.getByLabelText('Password'), 'secret');
    await user.click(screen.getByRole('button', { name: /log in/i }));

    // Redirected to the manager home (Log Session) with the manager nav shown.
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Log Session' })).toBeInTheDocument();
    });
    expect(screen.getByRole('navigation', { name: /primary/i })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Sell' })).toBeInTheDocument();
  });
});
