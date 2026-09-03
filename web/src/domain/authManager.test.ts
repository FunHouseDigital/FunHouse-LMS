import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  AuthManager,
  SecureStorageUnavailableError,
  decodeRoleFromJwt,
  validateCredentials,
  SESSION_META_KEY,
  CRYPTO_SALT_META_KEY,
  GRACE_MS,
  SESSION_ACTIVE_STORAGE_KEY,
} from './authManager';
import { UnauthorizedError } from '../api/client';
import {
  DB_NAME,
  closeDb,
  countOperationalUnsynced,
  countQuarantinedLegacyActions,
  countQuarantinedLegacyData,
  enqueueAction,
  getDb,
  getMeta,
  getUnsyncedActions,
  replaceAuthMetadata,
  writeCachedRead,
  getCachedRead,
} from '../store/localStore';
import {
  clearSessionKey,
  decryptPayload,
  encryptPayload,
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

function loginResponse(role: string, expiresAtMs: number, location_id: string | null = 'loc-1', sub = 'u1'): LoginResponse {
  return {
    access_token: makeJwt({ sub, role, location_id, iat: 1, exp: Math.floor(expiresAtMs / 1000) }),
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
    localStorage.clear();
    await resetDb();
    clearSessionKey();
  });

  afterEach(() => {
    clearSessionKey();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
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
    expect(am.getLocalDataOwner()).toMatchObject({
      subject: 'u1',
      scope: expect.stringMatching(/^v2:[A-Za-z0-9_-]{43}$/),
    });
    expect(am.getSession()?.local_data_scope).toBe(am.getLocalDataOwner()?.scope);

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

  it('restores and safely upgrades a legacy encrypted session missing the opaque scope', async () => {
    const first = new AuthManager({ loginFn: async () => loginResponse('manager', NOW + 60_000), now: () => NOW });
    expect((await first.login('loyiso', 'secret')).ok).toBe(true);
    const expectedScope = first.getLocalDataOwner()!.scope;
    const key = getSessionKey()!;
    const legacy = { ...first.getSession()! } as Partial<Session>;
    delete legacy.local_data_scope;
    const marker = localStorage.getItem(SESSION_ACTIVE_STORAGE_KEY)!;
    await replaceAuthMetadata(await encryptPayload(key, legacy), key, marker);
    clearSessionKey();

    const restoredManager = new AuthManager({
      loginFn: async () => { throw new Error('not used'); },
      now: () => NOW,
    });
    const restored = await restoredManager.restoreSession();

    expect(restored?.local_data_scope).toBe(expectedScope);
    expect(restoredManager.getLocalDataOwner()?.scope).toBe(expectedScope);
    const upgraded = await decryptPayload<Session>(key, (await getMeta<EncryptedField>(SESSION_META_KEY))!);
    expect(upgraded.local_data_scope).toBe(expectedScope);
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

  it('classifies secure-storage failure and clears a previous key and session', async () => {
    const am = new AuthManager({
      loginFn: async () => loginResponse('manager', NOW + 60_000),
      now: () => NOW,
    });
    await am.login('loyiso', 'first-secret');
    expect(am.getToken()).not.toBeNull();
    expect(hasSessionKey()).toBe(true);

    const actualCrypto = globalThis.crypto;
    vi.stubGlobal('crypto', {
      getRandomValues: actualCrypto.getRandomValues.bind(actualCrypto),
      subtle: {
        importKey: actualCrypto.subtle.importKey.bind(actualCrypto.subtle),
        deriveKey: vi.fn(async () => {
          throw new DOMException('Operation not supported', 'NotSupportedError');
        }),
      },
    } as unknown as Crypto);

    await expect(am.login('another-user', 'replacement-secret')).rejects.toBeInstanceOf(
      SecureStorageUnavailableError,
    );
    expect(am.getSession()).toBeNull();
    expect(am.getToken()).toBeNull();
    expect(hasSessionKey()).toBe(false);
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

    const owner = am.getLocalDataOwner()!;
    await enqueueAction({
      client_id: 'q1',
      entity: 'session',
      created_at: new Date(NOW).toISOString(),
      payload: { player_id: 'p1' },
    }, { owner });

    am.handleUnauthorized();

    // Session cleared but the queue is untouched (retained).
    expect(am.getSession()).toBeNull();
    expect(am.getLocalDataOwner()).toBeNull();
    expect(am.isLocalDataOwnerCurrent(owner)).toBe(false);
    expect(await countOperationalUnsynced()).toBe(1);
  });

  it('serializes auth replacement after an actual write final guard and IndexedDB commit', async () => {
    const am = new AuthManager({ loginFn: async () => loginResponse('manager', NOW + 60_000), now: () => NOW });
    expect((await am.login('loyiso', 'secret')).ok).toBe(true);
    const publishedOwner = am.getLocalDataOwner()!;
    const initialMarker = localStorage.getItem(SESSION_ACTIVE_STORAGE_KEY);
    let checks = 0;
    let markerChangedDuringWrite = false;
    let replacement!: AuthManager;
    let replacementAttempt: Promise<unknown> | null = null;
    const writeOwner = {
      ...publishedOwner,
      isCurrent: () => {
        checks += 1;
        if (localStorage.getItem(SESSION_ACTIVE_STORAGE_KEY) !== initialMarker) {
          markerChangedDuringWrite = true;
        }
        if (checks === 9) {
          replacement = new AuthManager({
            loginFn: async () => loginResponse('manager', NOW + 60_000),
            now: () => NOW,
          });
          replacementAttempt = replacement.login('replacement', 'new-secret');
        }
        return publishedOwner.isCurrent();
      },
    };

    await writeCachedRead('post-final-guard', [{ id: 'committed' }], writeOwner);
    expect(checks).toBeGreaterThanOrEqual(9);
    expect(markerChangedDuringWrite).toBe(false);
    await expect(replacementAttempt).resolves.toMatchObject({ ok: true });
    expect((await getCachedRead<Array<{ id: string }>>(
      'post-final-guard',
      replacement.getLocalDataOwner()!,
    ))?.data).toEqual([{ id: 'committed' }]);
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

  it('prevents an older deferred login from replacing a peer that won after request start', async () => {
    let releaseOlder!: (response: LoginResponse) => void;
    let markOlderStarted!: () => void;
    const olderStarted = new Promise<void>((resolve) => { markOlderStarted = resolve; });
    const olderResponse = new Promise<LoginResponse>((resolve) => { releaseOlder = resolve; });
    const older = new AuthManager({
      loginFn: async () => {
        markOlderStarted();
        return olderResponse;
      },
      now: () => NOW,
    });
    const olderAttempt = older.login('older', 'secret');
    await olderStarted;

    const winner = new AuthManager({
      loginFn: async () => loginResponse('manager', NOW + 60_000, 'loc-1', 'peer-winner'),
      now: () => NOW,
    });
    expect((await winner.login('winner', 'secret')).ok).toBe(true);
    const winnerMarker = localStorage.getItem(SESSION_ACTIVE_STORAGE_KEY);

    releaseOlder(loginResponse('manager', NOW + 60_000));
    await expect(olderAttempt).resolves.toEqual({ ok: false, kind: 'invalid_credentials' });
    expect(localStorage.getItem(SESSION_ACTIVE_STORAGE_KEY)).toBe(winnerMarker);
    expect(winner.getLocalDataOwner()?.isCurrent()).toBe(true);
    expect(older.getLocalDataOwner()).toBeNull();
  });

  it('aborts a losing pre-publication migration when a peer replaces the observed marker', async () => {
    const legacyScope = 'v1:u1:manager:loc-1:no-school';
    const db = await getDb();
    await db.put('sync_queue', {
      client_id: 'losing-migration',
      entity: 'session',
      created_at: new Date(NOW).toISOString(),
      payload: { player_id: 'legacy-player' },
      status: 'unsynced',
      attempt_count: 0,
      sync_scope: legacyScope,
    });
    await db.put('meta', {
      key: `player_id_resolutions:${legacyScope}`,
      value: { 'legacy-player': 'server-player' },
    });
    await closeDb();
    await getDb();
    expect(await countQuarantinedLegacyActions()).toBe(1);
    expect(await countQuarantinedLegacyData()).toBe(2);
    localStorage.setItem(SESSION_ACTIVE_STORAGE_KEY, 'incumbent-marker');

    const actualDecrypt = globalThis.crypto.subtle.decrypt.bind(globalThis.crypto.subtle);
    let replaced = false;
    vi.spyOn(globalThis.crypto.subtle, 'decrypt').mockImplementation(async (...args) => {
      const result = await actualDecrypt(...args);
      if (!replaced) {
        replaced = true;
        localStorage.setItem(SESSION_ACTIVE_STORAGE_KEY, 'peer-winner-marker');
      }
      return result;
    });

    const am = new AuthManager({ loginFn: async () => loginResponse('manager', NOW + 60_000), now: () => NOW });
    await expect(am.login('loyiso', 'secret')).resolves.toEqual({
      ok: false,
      kind: 'invalid_credentials',
    });
    expect(am.getLocalDataOwner()).toBeNull();
    expect(localStorage.getItem(SESSION_ACTIVE_STORAGE_KEY)).toBe('peer-winner-marker');
    expect(await countQuarantinedLegacyActions()).toBe(1);
    expect(await countQuarantinedLegacyData()).toBe(2);
  });

  it('invalidates published authority synchronously when a peer replaces the shared marker', async () => {
    const am = new AuthManager({ loginFn: async () => loginResponse('manager', NOW + 60_000), now: () => NOW });
    expect((await am.login('loyiso', 'secret')).ok).toBe(true);
    const owner = am.getLocalDataOwner()!;
    const originalMarker = localStorage.getItem(SESSION_ACTIVE_STORAGE_KEY)!;

    localStorage.setItem(SESSION_ACTIVE_STORAGE_KEY, 'peer-replacement-marker');

    expect(owner.isCurrent()).toBe(false);
    expect(am.getToken()).toBeNull();
    await expect(getUnsyncedActions(owner)).rejects.toThrow('stale');

    localStorage.setItem(SESSION_ACTIVE_STORAGE_KEY, originalMarker);
  });

  it('persists only opaque v2 routing and owner-key identifiers outside ciphertext', async () => {
    const response = loginResponse('manager', NOW + 60_000, 'private-location', 'private-subject');
    const am = new AuthManager({ loginFn: async () => response, now: () => NOW });
    expect((await am.login('private-user', 'secret')).ok).toBe(true);
    const owner = am.getLocalDataOwner()!;
    await enqueueAction({
      client_id: 'opaque-row',
      entity: 'session',
      created_at: new Date(NOW).toISOString(),
      payload: { player_id: 'private-player' },
    }, { owner });

    const db = await getDb();
    const raw = JSON.stringify({
      queue: await db.get('sync_queue', 'opaque-row'),
      keyring: await db.getAll('owner_data_keys'),
      metaKeys: (await db.getAll('meta')).map((entry) => entry.key),
    });
    expect(owner.scope).toMatch(/^v2:[A-Za-z0-9_-]{43}$/);
    expect(raw).not.toContain('private-subject');
    expect(raw).not.toContain('private-location');
    expect(raw).not.toContain('manager');
    expect(raw).not.toContain('v1%3A');
    expect(Object.keys((await db.getAll('owner_data_keys'))[0]).sort()).toEqual([
      'created_at', 'key', 'key_id', 'owner_key_id',
    ]);
  });

  it('revokes an old same-subject generation while a replacement password can read the durable queue', async () => {
    const am = new AuthManager({ loginFn: async () => loginResponse('manager', NOW + 60_000), now: () => NOW });
    expect((await am.login('loyiso', 'old-password')).ok).toBe(true);
    const oldOwner = am.getLocalDataOwner()!;
    await enqueueAction({
      client_id: 'survives-password-replacement', entity: 'session',
      created_at: new Date(NOW).toISOString(), payload: { player_id: 'p1' },
    }, { owner: oldOwner });

    am.handleUnauthorized();
    expect(oldOwner.isCurrent()).toBe(false);
    expect((await am.login('loyiso', 'new-password')).ok).toBe(true);
    const replacementOwner = am.getLocalDataOwner()!;

    expect(replacementOwner.generation).not.toBe(oldOwner.generation);
    expect(replacementOwner.keyId).toBe(oldOwner.keyId);
    await expect(getUnsyncedActions(oldOwner)).rejects.toThrow('stale');
    expect((await getUnsyncedActions(replacementOwner)).map((item) => item.client_id)).toEqual([
      'survives-password-replacement',
    ]);
  });

  it('uses distinct owner-data keys for equal passwords under different JWT subjects', async () => {
    const first = new AuthManager({ loginFn: async () => loginResponse('manager', NOW + 60_000, 'loc-1', 'subject-a'), now: () => NOW });
    expect((await first.login('first', 'same-password')).ok).toBe(true);
    const firstKeyId = first.getLocalDataOwner()!.keyId;

    first.handleUnauthorized();
    await vi.waitFor(() => {
      expect(localStorage.getItem(SESSION_ACTIVE_STORAGE_KEY)).toBeNull();
    });
    const second = new AuthManager({ loginFn: async () => loginResponse('manager', NOW + 60_000, 'loc-1', 'subject-b'), now: () => NOW });
    expect((await second.login('second', 'same-password')).ok).toBe(true);
    expect(second.getLocalDataOwner()!.keyId).not.toBe(firstKeyId);
  });
});
