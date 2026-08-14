/**
 * Auth_Manager — login, JWT lifecycle, token attachment, and re-auth handling
 * (Req 1, 2, 17.2, 17.3). See design.md "Auth_Manager".
 *
 * This is the pure(ish) domain core; the React binding lives in
 * `src/state/authState.tsx`. It coordinates three collaborators:
 *  - the Container_API login call (injected as `loginFn` so it is trivially
 *    mockable in tests and so this module has no hard dependency on a
 *    constructed client),
 *  - the Local_Store `meta` entries (`crypto_salt`, encrypted `session`), and
 *  - the Crypto service (derive/hold the in-memory AES session key).
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
  getMeta,
  setMeta,
} from '../store/localStore';
import {
  clearSessionKey,
  encryptPayload,
  generateSalt,
  getSessionKey,
  hasSessionKey,
  initSessionKey,
} from './crypto';
import { UnauthorizedError } from '../api/client';

/** Roles the client understands (matches the API's `VALID_ROLES`). */
export type Role = Session['role'];

const VALID_ROLES: readonly Role[] = ['manager', 'founder', 'facilitator'];

/** Default re-auth grace for already-displayed personal data (Req 17.3). */
export const GRACE_MS = 30_000;

/** Meta keys owned by the Auth_Manager. */
export const SESSION_META_KEY = 'session';
export const CRYPTO_SALT_META_KEY = 'crypto_salt';

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

  constructor(options: AuthManagerOptions) {
    this.loginFn = options.loginFn;
    this.nowFn = options.now ?? (() => Date.now());
    this.graceMs = options.graceMs ?? GRACE_MS;
  }

  /** The in-memory authenticated session, or `null`. */
  getSession(): Session | null {
    return this.session;
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

    let response: LoginResponse;
    try {
      response = await this.loginFn(identifier, password);
    } catch (err) {
      if (err instanceof UnauthorizedError) {
        return { ok: false, kind: 'invalid_credentials' };
      }
      throw err;
    }

    const role = decodeRoleFromJwt(response.access_token);
    if (role === null) {
      clearSessionKey();
      return { ok: false, kind: 'invalid_credentials' };
    }
    const location_id = decodeLocationFromJwt(response.access_token);

    try {
      // Derive/hold the encryption key from the login secret + per-device salt.
      let salt = await getMeta<string>(CRYPTO_SALT_META_KEY);
      if (!salt) {
        salt = generateSalt();
        await setMeta(CRYPTO_SALT_META_KEY, salt);
      }
      await initSessionKey(password, salt);

      const session: Session = {
        access_token: response.access_token,
        // `expires_at` arrives as an ISO string over JSON; normalise defensively.
        expires_at: String(response.expires_at),
        role,
        location_id,
      };

      // Persist encrypted-at-rest (Req 1.2, 17.1) using the in-memory session key.
      const key = getSessionKey();
      if (key) {
        const encrypted = await encryptPayload(key, session);
        await setMeta(SESSION_META_KEY, encrypted);
      }

      this.session = session;
      return { ok: true, session };
    } catch (error) {
      // Never leave either a previous session or a derived key active after
      // incomplete persistence of replacement credentials.
      this.session = null;
      clearSessionKey();
      throw new SecureStorageUnavailableError(error);
    }
  }

  /**
   * Clear the JWT and in-memory session key and route intent to login. This is
   * the handler for an expired token (Req 1.6) and for a `401` from any call
   * (Req 1.7). It deliberately does NOT touch the Sync_Queue, so all queued
   * Unsynced_Items are retained in the Local_Store (Req 1.6).
   */
  handleUnauthorized(): void {
    this.session = null;
    clearSessionKey();
  }

  /** Explicit logout — same effect as {@link handleUnauthorized}. */
  logout(): void {
    this.handleUnauthorized();
  }
}
