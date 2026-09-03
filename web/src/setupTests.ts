// Vitest global setup.
// - jest-dom adds custom matchers (toBeInTheDocument, etc.).
// - fake-indexeddb/auto installs an in-memory IndexedDB so the Local_Store
//   can be exercised in jsdom without a real browser.
import '@testing-library/jest-dom';
import 'fake-indexeddb/auto';
import { webcrypto } from 'node:crypto';

// WebCrypto polyfill (defensive): Node 20+ and modern jsdom expose a global
// `crypto.subtle`, which the Crypto service (AES-GCM/PBKDF2) relies on. If a
// runtime lacks it, fall back to Node's WebCrypto so crypto tests still run.
// setupTests.ts is test-only and never bundled for the browser.
if (!globalThis.crypto || !globalThis.crypto.subtle) {
  Object.defineProperty(globalThis, 'crypto', {
    value: webcrypto,
    configurable: true,
    writable: true,
  });
}


import { activateOwnerDataKey } from './store/localStore';
import { sha256Base64Url } from './domain/crypto';
import type { LocalDataOwner } from './domain/types';

/** Build a real test capability backed by the same durable non-extractable key store as production. */
export async function activateTestOwner(
  subject = 'test-subject',
  scope?: string,
  generation = 1,
  isCurrent: () => boolean = () => true,
): Promise<LocalDataOwner> {
  const activated = await activateOwnerDataKey(subject);
  const opaqueScope = scope ?? `v2:${await sha256Base64Url(JSON.stringify([
    subject, 'manager', 'test-location', null,
  ]))}`;
  return Object.freeze({
    subject,
    scope: opaqueScope,
    keyId: activated.keyId,
    key: activated.key,
    generation,
    isCurrent,
  });
}
