/**
 * Crypto service — POPIA on-device protection (Req 17.1).
 *
 * Personal-data payloads are encrypted with WebCrypto AES-GCM (256-bit) before
 * being written to IndexedDB. The AES key is derived via PBKDF2 from a
 * login-time secret combined with a stored per-device `crypto_salt`. The key is
 * non-extractable: its material can never be exported. While an unexpired
 * authenticated session is active, the CryptoKey is structured-cloned into the
 * origin-scoped IndexedDB so an Android-installed PWA can survive browser
 * process recreation without exporting the key bytes. The encrypted session,
 * key, and opaque lifecycle owner are replaced atomically in one IndexedDB
 * transaction. Restore/replacement/cleanup critical sections use a same-origin
 * Web Lock where available (with same-context ordering otherwise), and cleanup
 * deletes the tuple only when its owner still matches inside the deleting
 * transaction. A later absent-marker startup retries stale or legacy ownerless
 * cleanup. These are application coordination guarantees, not a stronger key
 * boundary: non-extractability blocks export, not cryptographic use by
 * same-origin code holding the key object. Explicit logout, expiry, or a
 * current-lifecycle 401 drops the in-memory handle synchronously; marker
 * compare-removal and owner-conditional tuple deletion share the durable-auth
 * lock. An external marker-removal observer clears only its local handle. Each
 * record uses a fresh random 96-bit IV, stored alongside the ciphertext in the
 * `EncryptedField` envelope. See design.md "POPIA on-device protection" /
 * "Crypto service".
 *
 * Environment note: this uses `globalThis.crypto.subtle`, which is available in
 * browsers and in Node 20+ (Node exposes a global WebCrypto). Under
 * vitest/jsdom the same global `crypto.subtle` is present; `setupTests.ts`
 * additionally polyfills it from `node:crypto` if a runtime ever lacks it.
 */
import type { EncryptedField } from './types';

const PBKDF2_ITERATIONS = 100_000;
const IV_LENGTH_BYTES = 12; // 96-bit IV for AES-GCM
const SALT_LENGTH_BYTES = 16;
const AES_KEY_LENGTH_BITS = 256;

/** Resolve the SubtleCrypto implementation or throw a clear, actionable error. */
function getSubtle(): SubtleCrypto {
  const c = globalThis.crypto;
  if (c && c.subtle) return c.subtle;
  throw new Error(
    'WebCrypto SubtleCrypto is unavailable in this environment; cannot protect personal data at rest.',
  );
}

// ---- base64 <-> bytes (avoid depending on Buffer so it works in the browser) ----

function bytesToBase64(bytes: Uint8Array): string {
  let binary = '';
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary);
}

function base64ToBytes(b64: string): Uint8Array {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

/**
 * Present a byte array to the WebCrypto API as a `BufferSource`. TypeScript's
 * DOM lib models `BufferSource` as strictly `ArrayBuffer`-backed, while our
 * `Uint8Array`s are `ArrayBufferLike`-backed; this narrowing cast is safe.
 */
function buf(bytes: Uint8Array): BufferSource {
  return bytes as unknown as BufferSource;
}

/** Generate a fresh random salt (base64) for PBKDF2 key derivation. */
export function generateSalt(): string {
  const salt = new Uint8Array(SALT_LENGTH_BYTES);
  globalThis.crypto.getRandomValues(salt);
  return bytesToBase64(salt);
}

/**
 * Derive a non-extractable AES-GCM 256-bit `CryptoKey` from a login-time secret
 * and the stored `crypto_salt` (base64) via PBKDF2/SHA-256.
 */
export async function deriveKey(secret: string, saltB64: string): Promise<CryptoKey> {
  const subtle = getSubtle();
  const baseKey = await subtle.importKey(
    'raw',
    buf(new TextEncoder().encode(secret)),
    'PBKDF2',
    false,
    ['deriveKey'],
  );
  return subtle.deriveKey(
    {
      name: 'PBKDF2',
      salt: buf(base64ToBytes(saltB64)),
      iterations: PBKDF2_ITERATIONS,
      hash: 'SHA-256',
    },
    baseKey,
    { name: 'AES-GCM', length: AES_KEY_LENGTH_BITS },
    false, // non-extractable: the key material cannot be exported
    ['encrypt', 'decrypt'],
  );
}

/** Encrypt a UTF-8 string to an `EncryptedField` with a fresh random 96-bit IV. */
export async function encrypt(key: CryptoKey, plaintext: string): Promise<EncryptedField> {
  const subtle = getSubtle();
  const iv = new Uint8Array(IV_LENGTH_BYTES);
  globalThis.crypto.getRandomValues(iv);
  const ciphertext = await subtle.encrypt(
    { name: 'AES-GCM', iv: buf(iv) },
    key,
    buf(new TextEncoder().encode(plaintext)),
  );
  return {
    iv: bytesToBase64(iv),
    ciphertext: bytesToBase64(new Uint8Array(ciphertext)),
  };
}

/** Decrypt an `EncryptedField` back to its UTF-8 string. */
export async function decrypt(key: CryptoKey, field: EncryptedField): Promise<string> {
  const subtle = getSubtle();
  const plaintext = await subtle.decrypt(
    { name: 'AES-GCM', iv: buf(base64ToBytes(field.iv)) },
    key,
    buf(base64ToBytes(field.ciphertext)),
  );
  return new TextDecoder().decode(plaintext);
}

/** Encrypt an arbitrary personal-data payload (JSON) before a Local_Store write. */
export async function encryptPayload(key: CryptoKey, payload: unknown): Promise<EncryptedField> {
  return encrypt(key, JSON.stringify(payload));
}

/** Decrypt a personal-data payload read from the Local_Store. */
export async function decryptPayload<T = unknown>(
  key: CryptoKey,
  field: EncryptedField,
): Promise<T> {
  return JSON.parse(await decrypt(key, field)) as T;
}

// ---- Active in-memory handle to the persistently cloned session key ----

let sessionKey: CryptoKey | null = null;

/**
 * Derive and hold the session key in memory. Called at login time with the
 * login secret and the device's stored `crypto_salt`.
 */
export async function initSessionKey(secret: string, saltB64: string): Promise<CryptoKey> {
  sessionKey = await deriveKey(secret, saltB64);
  return sessionKey;
}

/** Directly set the in-memory session key (mainly for tests/wiring). */
export function setSessionKey(key: CryptoKey | null): void {
  sessionKey = key;
}

/** Return the in-memory session key, or `null` when no user is authenticated. */
export function getSessionKey(): CryptoKey | null {
  return sessionKey;
}

/** True when a session key is in memory (i.e. personal data can be decrypted). */
export function hasSessionKey(): boolean {
  return sessionKey !== null;
}

/** Drop the in-memory session key (logout / expiry) so personal data is withheld. */
export function clearSessionKey(): void {
  sessionKey = null;
}
