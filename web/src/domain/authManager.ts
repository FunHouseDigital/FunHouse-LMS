/**
 * Auth_Manager — login, JWT lifecycle, token attachment, and re-auth handling
 * (Req 1, 2, 17.2, 17.3). See design.md "Auth_Manager".
 *
 * This is the pure(ish) domain core; the React binding lives in
 * `src/state/authState.tsx`. It coordinates three collaborators:
 *  - the Container_API login call (injected as `loginFn` so it is trivially
 *    mockable in tests and so this module has no hard dependency on a
 *    constructed client),
 *  - the Local_Store `meta` entries (`crypto_salt` and an atomically replaced
 *    encrypted-session / non-extractable-key / lifecycle-owner tuple), and
 *  - the Crypto service (derive/hold the AES session key).
 *
 * ### Role source (documented decision)
 * The Spec 2 `POST /auth/login` `200` body is only
 * `{access_token, token_type, expires_at}` — it does NOT carry `role` or
 * `location_id`. Those live inside the **JWT payload** (`auth/service.py`
 * `issue_token`: `role`, `location_id`, `school_id`, `sub`, `iat`, `exp`),
 * signed with HS256. The client does NOT hold the signing secret, so the token
 * is cryptographically opaque to it. For **navigation gating only** (Req 2) we
 * base64url-decode the JWT payload and read the `role`/`location_id` claims
 * WITHOUT verifying the signature. This is safe because the client never makes
 * a trust decision on the decoded role beyond which local screens to show — the
 * server independently re-authorises every request against the verified token.
 */
import type { LoginResponse, Session } from './types';
import {
  deleteAuthMetadataIfOwnedBy,
  getAuthMetadata,
  getMeta,
  replaceAuthMetadata,
  SESSION_KEY_META_KEY,
  SESSION_META_KEY,
  SESSION_OWNER_META_KEY,
  setMeta,
} from '../store/localStore';
import {
  clearSessionKey,
  decryptPayload,
  encryptPayload,
  generateSalt,
  deriveKey,
  hasSessionKey,
  setSessionKey,
} from './crypto';
import { UnauthorizedError } from '../api/client';

/** Roles the client understands (matches the API's `VALID_ROLES`). */
export type Role = Session['role'];

const VALID_ROLES: readonly Role[] = ['manager', 'founder', 'facilitator'];

/** Default re-auth grace for already-displayed personal data (Req 17.3). */
export const GRACE_MS = 30_000;

/** Auth metadata keys owned by the Auth_Manager. */
export { SESSION_KEY_META_KEY, SESSION_META_KEY, SESSION_OWNER_META_KEY };
export const CRYPTO_SALT_META_KEY = 'crypto_salt';

/** Maximum permitted rounding difference between JWT `exp` and `expires_at`. */
export const JWT_EXPIRY_TOLERANCE_MS = 1_000;

/**
 * Non-sensitive application marker used for synchronous, cross-context
 * revocation. The bearer token and CryptoKey never enter localStorage.
 */
export const SESSION_ACTIVE_STORAGE_KEY = 'funhouse_session_active';

/** Cross-context mutex for auth tuple restore, replacement, and cleanup. */
export const AUTH_SESSION_LOCK_NAME = 'funhouse-auth-session';

let sameContextAuthTail: Promise<void> = Promise.resolve();

/**
 * Serialize the full durable-auth critical section across same-origin contexts
 * when Web Locks are available. The module tail preserves ordering between
 * managers in test/legacy environments; transaction-time owner checks remain
 * the correctness backstop for contexts that cannot share this lock.
 */
function withAuthSessionLock<T>(operation: () => Promise<T>): Promise<T> {
  const locks = typeof globalThis.navigator === 'undefined'
    ? undefined
    : globalThis.navigator.locks;
  if (locks) {
    // The Web Locks runtime awaits promise-returning callbacks. Older DOM
    // typings model the callback result synchronously, hence this narrowing.
    return locks.request(AUTH_SESSION_LOCK_NAME, () => operation()) as unknown as Promise<T>;
  }

  const result = sameContextAuthTail.then(operation, operation);
  sameContextAuthTail = result.then(
    () => undefined,
    () => undefined,
  );
  return result;
}

/** Immutable identity captured once when a protected request starts. */
export interface AuthRequestSnapshot {
  readonly token: string | null;
  readonly generation: number;
}

function readActiveSessionMarker(): string | null {
  try {
    const marker = globalThis.localStorage?.getItem(SESSION_ACTIVE_STORAGE_KEY);
    return marker && marker.length > 0 ? marker : null;
  } catch {
    return null;
  }
}

function createSessionMarker(): string {
  return globalThis.crypto.randomUUID();
}

function markSessionActive(marker: string): void {
  globalThis.localStorage.setItem(SESSION_ACTIVE_STORAGE_KEY, marker);
}

function removeSessionMarker(expectedMarker: string | null): void {
  try {
    const current = readActiveSessionMarker();
    if (current === expectedMarker) {
      globalThis.localStorage?.removeItem(SESSION_ACTIVE_STORAGE_KEY);
    }
  } catch {
    // A denied localStorage read is fail-closed: restoration also returns null.
  }
}

function isPersistedSessionKey(value: unknown): value is CryptoKey {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Partial<CryptoKey>;
  const algorithm = candidate.algorithm as KeyAlgorithm | undefined;
  return (
    candidate.type === 'secret' &&
    candidate.extractable === false &&
    algorithm?.name === 'AES-GCM' &&
    Array.isArray(candidate.usages) &&
    candidate.usages.includes('encrypt') &&
    candidate.usages.includes('decrypt')
  );
}

/**
 * Validate local coherence of a previously issued session. This deliberately
 * does not attempt JWT signature verification; the server remains authoritative.
 */
function isCoherentFreshSession(value: unknown, now: number): value is Session {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Partial<Session>;
  if (
    typeof candidate.access_token !== 'string' ||
    typeof candidate.expires_at !== 'string' ||
    typeof candidate.role !== 'string' ||
    !(VALID_ROLES as readonly string[]).includes(candidate.role) ||
    (candidate.location_id !== null && typeof candidate.location_id !== 'string')
  ) {
    return false;
  }

  const claims = decodeJwtPayload(candidate.access_token);
  const subject = typeof claims?.sub === 'string' ? claims.sub.trim() : '';
  const issuedAtSeconds = claims?.iat;
  const tokenExpirySeconds = claims?.exp;
  const tokenRole = claims?.role;
  const tokenLocation = claims?.location_id;
  const responseExpiryMs = new Date(candidate.expires_at).getTime();

  return (
    subject !== '' &&
    typeof issuedAtSeconds === 'number' &&
    Number.isFinite(issuedAtSeconds) &&
    Number.isInteger(issuedAtSeconds) &&
    typeof tokenExpirySeconds === 'number' &&
    Number.isFinite(tokenExpirySeconds) &&
    Number.isInteger(tokenExpirySeconds) &&
    tokenExpirySeconds > issuedAtSeconds &&
    Number.isFinite(responseExpiryMs) &&
    responseExpiryMs > now &&
    Math.abs(tokenExpirySeconds * 1000 - responseExpiryMs) < JWT_EXPIRY_TOLERANCE_MS &&
    tokenRole === candidate.role &&
    (typeof tokenLocation === 'string' || tokenLocation === null) &&
    tokenLocation === candidate.location_id
  );
}

class StaleAuthLifecycleError extends Error {
  constructor() {
    super('Authentication lifecycle changed');
    this.name = 'StaleAuthLifecycleError';
  }
}

/** Field-level validation errors for the login form (Req 1.4). */
export interface FieldErrors {
  identifier?: string;
  password?: string;
}

export interface LoginSuccess {
  ok: true;
  session: Session;
}

export interface LoginFailure {
  ok: false;
  /**
   * - `validation`: client-side empty-field validation blocked submission (1.4).
   * - `invalid_credentials`: the API returned `401` (1.3) — generic message.
   */
  kind: 'validation' | 'invalid_credentials';
  fieldErrors?: FieldErrors;
}

export type LoginOutcome = LoginSuccess | LoginFailure;

/**
 * Raised after a valid API response when encrypted local session persistence
 * cannot be completed. Callers can distinguish browser storage/crypto support
 * from connectivity without exposing authentication details.
 */
export class SecureStorageUnavailableError extends Error {
  readonly cause?: unknown;

  constructor(cause?: unknown) {
    super('Secure device storage is unavailable');
    this.name = 'SecureStorageUnavailableError';
    this.cause = cause;
  }
}

export interface AuthManagerOptions {
  /** Performs `POST /auth/login`; injected so it can be mocked in tests. */
  loginFn: (identifier: string, password: string) => Promise<LoginResponse>;
  /** Current time source (ms since epoch); injectable for deterministic tests. */
  now?: () => number;
  /** Re-auth grace window in ms (defaults to {@link GRACE_MS}). */
  graceMs?: number;
}

/**
 * Client-side non-empty validation of the login fields (Req 1.1, 1.4).
 * The identifier is trimmed (whitespace-only is empty); the password is checked
 * for zero length only (a password may legitimately be spaces).
 */
export function validateCredentials(identifier: string, password: string): FieldErrors {
  const errors: FieldErrors = {};
  if (!identifier || identifier.trim().length === 0) {
    errors.identifier = 'Identifier is required';
  }
  if (!password || password.length === 0) {
    errors.password = 'Password is required';
  }
  return errors;
}

export function hasFieldErrors(errors: FieldErrors): boolean {
  return Boolean(errors.identifier || errors.password);
}

/**
 * Decode a JWT payload WITHOUT verifying its signature (see module docstring).
 * Returns `null` for a structurally invalid token.
 */
export function decodeJwtPayload(token: string): Record<string, unknown> | null {
  const parts = token.split('.');
  if (parts.length !== 3) return null;
  try {
    let b64 = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    const pad = b64.length % 4;
    if (pad === 2) b64 += '==';
    else if (pad === 3) b64 += '=';
    else if (pad === 1) return null;
    const json = atob(b64);
    const parsed = JSON.parse(json);
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

/** Extract a valid `role` claim from a JWT payload, or `null` (nav gating only). */
export function decodeRoleFromJwt(token: string): Role | null {
  const payload = decodeJwtPayload(token);
  const role = payload?.role;
  return typeof role === 'string' && (VALID_ROLES as readonly string[]).includes(role)
    ? (role as Role)
    : null;
}

/** Extract the `location_id` claim from a JWT payload, or `null`. */
export function decodeLocationFromJwt(token: string): string | null {
  const payload = decodeJwtPayload(token);
  const loc = payload?.location_id;
  return typeof loc === 'string' ? loc : null;
}

/** Parse an ISO `expires_at` to epoch ms; `NaN` when unparseable. */
function expiryMs(session: Session): number {
  return new Date(session.expires_at).getTime();
}

/**
 * The Auth_Manager coordinates login, holds the authenticated session in memory,
 * and exposes the bearer token for the Container_API client (Req 1.5). It never
 * touches the Sync_Queue, so clearing a session inherently retains all queued
 * Unsynced_Items (Req 1.6).
 */
export class AuthManager {
  private readonly loginFn: AuthManagerOptions['loginFn'];
  private readonly nowFn: () => number;
  private readonly graceMs: number;
  private session: Session | null = null;
  private pendingRevocation: Promise<void> = Promise.resolve();
  private lifecycleGeneration = 0;
  private activeSessionMarker: string | null = null;

  constructor(options: AuthManagerOptions) {
    this.loginFn = options.loginFn;
    this.nowFn = options.now ?? (() => Date.now());
    this.graceMs = options.graceMs ?? GRACE_MS;
  }

  /**
   * Restore an active, locally coherent session after Android recreates the
   * installed PWA process. This proves neither the JWT signature nor current
   * server validity; the next protected request remains server-authoritative.
   */
  async restoreSession(): Promise<Session | null> {
    if (this.session && this.isAuthenticated()) return this.session;

    const restoreGeneration = this.lifecycleGeneration;
    await this.pendingRevocation;
    if (this.lifecycleGeneration !== restoreGeneration) {
      return this.session && this.isAuthenticated() ? this.session : null;
    }

    try {
      return await withAuthSessionLock(async () => {
        if (this.lifecycleGeneration !== restoreGeneration) {
          return this.session && this.isAuthenticated() ? this.session : null;
        }

        const restoreMarker = readActiveSessionMarker();
        const metadata = await getAuthMetadata();
        if (this.lifecycleGeneration !== restoreGeneration) {
          return this.session && this.isAuthenticated() ? this.session : null;
        }

        if (restoreMarker === null) {
          // Read metadata first, then recheck the marker. If a lock-less peer
          // published a new marker meanwhile, leave its lifecycle untouched.
          // Otherwise transaction-time ownership decides whether stale owned
          // or legacy ownerless metadata may be removed.
          if (readActiveSessionMarker() === null) {
            this.invalidateLocalLifecycle();
            await deleteAuthMetadataIfOwnedBy(metadata.owner);
          }
          return null;
        }

        // A non-null marker may precede a concurrent login's atomic metadata
        // replacement on browsers without Web Locks. Fail closed on mismatch;
        // never delete metadata or remove a marker whose ownership is unclear.
        if (metadata.owner !== restoreMarker) return null;

        if (!metadata.encryptedSession || !isPersistedSessionKey(metadata.sessionKey)) {
          this.invalidateLocalLifecycle();
          removeSessionMarker(restoreMarker);
          await deleteAuthMetadataIfOwnedBy(restoreMarker);
          return null;
        }

        let restored: unknown;
        try {
          restored = await decryptPayload<unknown>(
            metadata.sessionKey,
            metadata.encryptedSession,
          );
        } catch {
          restored = null;
        }
        if (!isCoherentFreshSession(restored, this.nowFn())) {
          if (this.lifecycleGeneration === restoreGeneration) {
            this.invalidateLocalLifecycle();
            removeSessionMarker(restoreMarker);
            await deleteAuthMetadataIfOwnedBy(restoreMarker);
          }
          return this.session && this.isAuthenticated() ? this.session : null;
        }

        // Compare-and-commit: a local revoke or a lock-less peer's marker
        // replacement during any await wins. No await occurs after this guard.
        if (
          this.lifecycleGeneration !== restoreGeneration ||
          readActiveSessionMarker() !== metadata.owner
        ) {
          return this.session && this.isAuthenticated() ? this.session : null;
        }

        this.lifecycleGeneration += 1;
        this.activeSessionMarker = metadata.owner;
        setSessionKey(metadata.sessionKey);
        this.session = restored;
        return restored;
      });
    } catch {
      // Storage/lock failures are fail-closed. Ownership is unknown here, so
      // leave durable metadata for an owner-aware retry rather than deleting it.
      return this.session && this.isAuthenticated() ? this.session : null;
    }
  }

  /** The in-memory authenticated session, or `null`. */
  getSession(): Session | null {
    return this.session;
  }

  /** Capture the token and lifecycle identity atomically at request start. */
  getAuthRequestSnapshot(now: number = this.nowFn()): AuthRequestSnapshot {
    return Object.freeze({
      token: this.getToken(now),
      generation: this.lifecycleGeneration,
    });
  }

  /**
   * Apply a cross-context marker-removal event only to the lifecycle that owned
   * the removed marker. A delayed event cannot revoke a replacement marker.
   */
  handleExternalMarkerRemoval(removedMarker: string | null): boolean {
    if (!removedMarker || removedMarker !== this.activeSessionMarker) return false;
    this.revokeForExternalMarkerChange();
    return true;
  }

  /** Revalidate marker ownership after foregrounding when events may be missed. */
  handleExternalMarkerInvalidation(): boolean {
    if (
      this.activeSessionMarker !== null &&
      readActiveSessionMarker() === this.activeSessionMarker
    ) {
      return false;
    }
    if (!this.session) return false;
    this.revokeForExternalMarkerChange();
    return true;
  }

  /** The role for nav gating, or `null` when unauthenticated. */
  getRole(): Role | null {
    return this.session?.role ?? null;
  }

  /** True while a session exists and its `expires_at` is strictly in the future. */
  isAuthenticated(now: number = this.nowFn()): boolean {
    if (!this.session) return false;
    return expiryMs(this.session) > now;
  }

  /** True when there is no session or the session's `expires_at` is at/before now (Req 1.6). */
  isExpired(now: number = this.nowFn()): boolean {
    if (!this.session) return true;
    return expiryMs(this.session) <= now;
  }

  /**
   * The bearer token to attach to Container_API/sync requests, or `null`.
   * Only returned while the token is present and unexpired (Req 1.5).
   */
  getToken(now: number = this.nowFn()): string | null {
    if (!this.session) return null;
    return expiryMs(this.session) > now ? this.session.access_token : null;
  }

  /**
   * True when already-displayed personal data may still be shown: the session
   * is valid, or within the ≤30s re-auth grace after expiry, AND the in-memory
   * decryption key is still present (Req 17.2, 17.3).
   */
  canAccessPersonalData(now: number = this.nowFn()): boolean {
    if (!this.session || !hasSessionKey()) return false;
    return now <= expiryMs(this.session) + this.graceMs;
  }

  /**
   * Attempt a login (Req 1.1–1.3, 1.2 storage, 17.1 encrypted-at-rest).
   *
   * 1. Client-side non-empty validation (1.4) — blocks the API call on empty.
   * 2. `POST /auth/login`. A `401`/`UnauthorizedError` → generic
   *    `invalid_credentials`, nothing stored (1.3). Other errors (network, 422)
   *    propagate to the caller.
   * 3. On `200`: derive the AES session key from the password + the device's
   *    `crypto_salt` (creating and persisting the salt on first login), decode
   *    `role`/`location_id` from the JWT for nav gating, and persist the session
   *    `{access_token, expires_at, role, location_id}` encrypted in `meta.session`.
   */
  async login(identifier: string, password: string): Promise<LoginOutcome> {
    const fieldErrors = validateCredentials(identifier, password);
    if (hasFieldErrors(fieldErrors)) {
      return { ok: false, kind: 'validation', fieldErrors };
    }
    // Reserve a unique lifecycle before the first await. Every revoke after
    // login invocation therefore advances past this attempt and wins.
    const loginGeneration = ++this.lifecycleGeneration;
    const cleanupAtStart = this.pendingRevocation;
    await cleanupAtStart;
    if (this.lifecycleGeneration !== loginGeneration) {
      return { ok: false, kind: 'invalid_credentials' };
    }

    let response: LoginResponse;
    try {
      response = await this.loginFn(identifier, password);
    } catch (err) {
      if (err instanceof UnauthorizedError) {
        return { ok: false, kind: 'invalid_credentials' };
      }
      throw err;
    }

    if (this.lifecycleGeneration !== loginGeneration) {
      return { ok: false, kind: 'invalid_credentials' };
    }

    const role = decodeRoleFromJwt(response.access_token);
    const location_id = decodeLocationFromJwt(response.access_token);
    if (role === null) {
      return { ok: false, kind: 'invalid_credentials' };
    }
    const session: Session = {
      access_token: response.access_token,
      expires_at: String(response.expires_at),
      role,
      location_id,
    };
    if (!isCoherentFreshSession(session, this.nowFn())) {
      return { ok: false, kind: 'invalid_credentials' };
    }

    let loginOwner: string | null = null;
    try {
      await withAuthSessionLock(async () => {
        this.assertLifecycle(loginGeneration);

        let salt = await getMeta<string>(CRYPTO_SALT_META_KEY);
        this.assertLifecycle(loginGeneration);
        if (!salt) {
          salt = generateSalt();
          await setMeta(CRYPTO_SALT_META_KEY, salt);
          this.assertLifecycle(loginGeneration);
        }

        // Derive and encrypt before disturbing the current lifecycle. The key
        // remains attempt-local until owner-tagged persistence succeeds.
        const key = await deriveKey(password, salt);
        this.assertLifecycle(loginGeneration);
        const encrypted = await encryptPayload(key, session);
        this.assertLifecycle(loginGeneration);
        loginOwner = createSessionMarker();
        const attemptOwner = loginOwner;

        // Publish the opaque owner before writing its metadata. The remove/set
        // pair has no await: old contexts receive the removal event, while an
        // absent-marker cleanup can only have observed the previous owner.
        this.session = null;
        clearSessionKey();
        const replacedMarker = readActiveSessionMarker();
        removeSessionMarker(replacedMarker);
        markSessionActive(attemptOwner);
        this.activeSessionMarker = null;

        // Session, key, and owner commit all-or-nothing. If this attempt becomes
        // stale, cleanup below is conditional on attemptOwner at transaction time.
        await replaceAuthMetadata(encrypted, key, attemptOwner);
        this.assertLifecycle(loginGeneration);
        if (readActiveSessionMarker() !== attemptOwner) {
          throw new StaleAuthLifecycleError();
        }

        // No await may occur between the final guards and local publication.
        this.activeSessionMarker = attemptOwner;
        setSessionKey(key);
        this.session = session;
      });
      return { ok: true, session };
    } catch (error) {
      // This attempt can remove only its own marker/metadata. If a peer has
      // already installed owner N, both operations are harmless no-ops.
      if (loginOwner !== null) {
        this.queuePersistedSessionCleanup(loginOwner);
      }
      await this.pendingRevocation;

      if (
        error instanceof StaleAuthLifecycleError ||
        this.lifecycleGeneration !== loginGeneration
      ) {
        return { ok: false, kind: 'invalid_credentials' };
      }

      // Preserve every persisted owner except this failed attempt. The current
      // manager still fails closed in memory, matching a storage/crypto outage.
      this.invalidateLocalLifecycle();
      throw new SecureStorageUnavailableError(error);
    }
  }

  private assertLifecycle(generation: number): void {
    if (this.lifecycleGeneration !== generation) {
      throw new StaleAuthLifecycleError();
    }
  }

  private invalidateLocalLifecycle(): void {
    this.lifecycleGeneration += 1;
    this.session = null;
    this.activeSessionMarker = null;
    clearSessionKey();
  }

  private queuePersistedSessionCleanup(expectedOwner: string | null): void {
    this.pendingRevocation = this.pendingRevocation.then(async () => {
      try {
        await withAuthSessionLock(async () => {
          // Compare-and-remove runs under the same cross-context lock as marker
          // replacement; an old M cleanup therefore cannot remove marker N.
          removeSessionMarker(expectedOwner);
          await deleteAuthMetadataIfOwnedBy(expectedOwner);
        });
      } catch {
        // Best-effort cleanup is retried by a later absent-marker startup.
      }
    });
  }

  private revokeForExternalMarkerChange(): void {
    // The context that changed the shared marker owns durable cleanup. Other
    // contexts only invalidate their local lifecycle; delayed events therefore
    // cannot target either old or replacement IndexedDB credentials.
    this.invalidateLocalLifecycle();
  }

  private revokePersistedSession(
    expectedOwner: string | null = this.activeSessionMarker,
  ): void {
    // Revocation is fail-closed in memory immediately. Marker compare-removal
    // and durable owner-conditional deletion then share the replacement lock,
    // so neither can target a newer lifecycle.
    this.invalidateLocalLifecycle();
    this.queuePersistedSessionCleanup(expectedOwner);
  }

  /**
   * Revoke for a protected-request 401 only if its immutable request-start
   * identity still belongs to the current lifecycle.
   */
  handleUnauthorized(snapshot?: AuthRequestSnapshot): boolean {
    if (
      snapshot &&
      (snapshot.token === null ||
        snapshot.generation !== this.lifecycleGeneration ||
        snapshot.token !== this.session?.access_token)
    ) {
      return false;
    }
    this.revokePersistedSession();
    return true;
  }

  /** Explicit logout — same revocation while retaining queued/scoped data. */
  logout(): void {
    this.handleUnauthorized();
  }
}
