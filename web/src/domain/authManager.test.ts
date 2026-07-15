import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  AuthManager,
  decodeRoleFromJwt,
  validateCredentials,
  SESSION_META_KEY,
  CRYPTO_SALT_META_KEY,
  GRACE_MS,
} from './authManager';
import { UnauthorizedError } from '../api/client';
import {
  DB_NAME,
  closeDb,
  enqueueAction,
  getActionsByStatus,
  getMeta,
} from '../store/localStore';
import {
  clearSessionKey,
  decryptPayload,
  getSessionKey,
  hasSessionKey,
} from './crypto';
import type { EncryptedField, LoginResponse, Session } from './types';

/** Build an unsigned JWT (header.payload.sig) carrying the given claims. */
function makeJwt(claims: Record<string, unknown>): string {
  const b64url = (obj: unknown) =>
    btoa(JSON.stringify(obj)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return `${b64url({ alg: 'HS256', typ: 'JWT' })}.${b64url(claims)}.sig`;
}

function loginResponse(role: string, expiresAtMs: number, location_id: string | null = 'loc-1'): LoginResponse {
  return {
    access_token: makeJwt({ sub: 'u1', role, location_id, iat: 1, exp: Math.floor(expiresAtMs / 1000) }),
    token_type: 'bearer',
    expires_at: new Date(expiresAtMs).toISOString(),
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

describe('validateCredentials (Req 1.4)', () => {
  it('flags empty identifier and password', () => {
    expect(validateCredentials('', '')).toEqual({
      identifier: 'Identifier is required',
      password: 'Password is required',
    });
  });

  it('treats a whitespace-only identifier as empty', () => {
    expect(validateCredentials('   ', 'pw').identifier).toBeDefined();
  });

  it('accepts non-empty values', () => {
    expect(validateCredentials('loyiso', 'secret')).toEqual({});
  });
});

describe('decodeRoleFromJwt (client-side, unverified — nav gating only)', () => {
  it('extracts a valid role claim', () => {
    expect(decodeRoleFromJwt(makeJwt({ role: 'founder' }))).toBe('founder');
    expect(decodeRoleFromJwt(makeJwt({ role: 'manager' }))).toBe('manager');
  });

  it('returns null for an invalid or missing role', () => {
    expect(decodeRoleFromJwt(makeJwt({ role: 'root' }))).toBeNull();
    expect(decodeRoleFromJwt(makeJwt({}))).toBeNull();
    expect(decodeRoleFromJwt('not-a-jwt')).toBeNull();
  });
});

describe('AuthManager', () => {
  const NOW = Date.UTC(2024, 0, 1, 12, 0, 0);

  beforeEach(async () => {
    await resetDb();
    clearSessionKey();
  });

  afterEach(() => {
    clearSessionKey();
  });

  it('blocks submission and does not call the API on empty fields (Req 1.4)', async () => {
    const loginFn = vi.fn();
    const am = new AuthManager({ loginFn, now: () => NOW });

    const outcome = await am.login('', '');

    expect(outcome).toEqual({
      ok: false,
      kind: 'validation',
      fieldErrors: { identifier: 'Identifier is required', password: 'Password is required' },
    });
    expect(loginFn).not.toHaveBeenCalled();
    expect(am.getSession()).toBeNull();
  });

  it('stores the token in ENCRYPTED meta.session on a 200 (Req 1.2, 17.1)', async () => {
    const expiresAt = NOW + 60 * 60 * 1000; // 1h ahead
    const loginFn = vi.fn(async () => loginResponse('manager', expiresAt));
    const am = new AuthManager({ loginFn, now: () => NOW });

    const outcome = await am.login('loyiso', 'secret');

    expect(outcome.ok).toBe(true);
    expect(loginFn).toHaveBeenCalledWith('loyiso', 'secret');

    // Role decoded from the JWT (login response has no role) (Req 2).
    expect(am.getRole()).toBe('manager');
    expect(am.getSession()?.location_id).toBe('loc-1');

    // crypto_salt persisted on first login.
    expect(await getMeta<string>(CRYPTO_SALT_META_KEY)).toBeTruthy();

    // meta.session is an encrypted envelope, NOT plaintext.
    const stored = await getMeta<EncryptedField>(SESSION_META_KEY);
    expect(stored).toBeDefined();
    expect(stored).toHaveProperty('iv');
    expect(stored).toHaveProperty('ciphertext');
    expect(JSON.stringify(stored)).not.toContain(am.getSession()!.access_token);

    // It decrypts back to the session with the in-memory key.
    const key = getSessionKey()!;
    const decrypted = await decryptPayload<Session>(key, stored!);
    expect(decrypted.role).toBe('manager');
    expect(decrypted.access_token).toBe(am.getSession()!.access_token);
  });

  it('shows a generic invalid-credentials failure on a 401 and stores nothing (Req 1.3)', async () => {
    const loginFn = vi.fn(async () => {
      throw new UnauthorizedError();
    });
    const am = new AuthManager({ loginFn, now: () => NOW });

    const outcome = await am.login('loyiso', 'wrong');

    expect(outcome).toEqual({ ok: false, kind: 'invalid_credentials' });
    expect(am.getSession()).toBeNull();
    expect(await getMeta(SESSION_META_KEY)).toBeUndefined();
    expect(hasSessionKey()).toBe(false);
  });

  it('rethrows non-401 errors (network/422) so the caller can react', async () => {
    const loginFn = vi.fn(async () => {
      throw new Error('network down');
    });
    const am = new AuthManager({ loginFn, now: () => NOW });

    await expect(am.login('loyiso', 'secret')).rejects.toThrow('network down');
  });

  it('exposes the bearer token only while unexpired (Req 1.5, 1.6)', async () => {
    const expiresAt = NOW + 60_000;
    const am = new AuthManager({ loginFn: async () => loginResponse('manager', expiresAt), now: () => NOW });
    await am.login('loyiso', 'secret');

    expect(am.getToken(NOW)).toBe(am.getSession()!.access_token);
    expect(am.isAuthenticated(NOW)).toBe(true);

    // At/after expiry the token is withheld and the manager routes to login.
    expect(am.getToken(expiresAt)).toBeNull();
    expect(am.isAuthenticated(expiresAt)).toBe(false);
    expect(am.isExpired(expiresAt)).toBe(true);
  });

  it('retains queued Unsynced_Items when routing to login on expiry (Req 1.6)', async () => {
    const am = new AuthManager({ loginFn: async () => loginResponse('manager', NOW + 60_000), now: () => NOW });
    await am.login('loyiso', 'secret');

    await enqueueAction({
      client_id: 'q1',
      entity: 'session',
      created_at: new Date(NOW).toISOString(),
      payload: { player_id: 'p1' },
    });

    am.handleUnauthorized();

    // Session cleared but the queue is untouched (retained).
    expect(am.getSession()).toBeNull();
    const unsynced = await getActionsByStatus('unsynced');
    expect(unsynced.map((a) => a.client_id)).toEqual(['q1']);
  });

  it('clears the JWT and session key on a 401 from any call (Req 1.7)', async () => {
    const am = new AuthManager({ loginFn: async () => loginResponse('founder', NOW + 60_000), now: () => NOW });
    await am.login('aya', 'secret');
    expect(hasSessionKey()).toBe(true);

    am.handleUnauthorized();

    expect(am.getToken(NOW)).toBeNull();
    expect(am.getSession()).toBeNull();
    expect(hasSessionKey()).toBe(false);
  });

  it('maintains ≤30s personal-data access after expiry, then withholds (Req 17.3)', async () => {
    const expiresAt = NOW + 60_000;
    const am = new AuthManager({ loginFn: async () => loginResponse('manager', expiresAt), now: () => NOW });
    await am.login('loyiso', 'secret');

    // Before expiry: accessible.
    expect(am.canAccessPersonalData(NOW)).toBe(true);
    // Within the grace window just after expiry: still accessible.
    expect(am.canAccessPersonalData(expiresAt + GRACE_MS - 1)).toBe(true);
    // Past the grace window: withheld.
    expect(am.canAccessPersonalData(expiresAt + GRACE_MS + 1)).toBe(false);
  });

  it('withholds personal data once the session key is cleared (Req 17.2)', async () => {
    const am = new AuthManager({ loginFn: async () => loginResponse('manager', NOW + 60_000), now: () => NOW });
    await am.login('loyiso', 'secret');
    expect(am.canAccessPersonalData(NOW)).toBe(true);

    clearSessionKey();
    expect(am.canAccessPersonalData(NOW)).toBe(false);
  });

  it('reuses the persisted crypto_salt across logins (Req 17.1)', async () => {
    const am = new AuthManager({ loginFn: async () => loginResponse('manager', NOW + 60_000), now: () => NOW });
    await am.login('loyiso', 'secret');
    const salt1 = await getMeta<string>(CRYPTO_SALT_META_KEY);

    am.handleUnauthorized();
    await am.login('loyiso', 'secret');
    const salt2 = await getMeta<string>(CRYPTO_SALT_META_KEY);

    expect(salt1).toBeTruthy();
    expect(salt2).toBe(salt1);
  });
});
