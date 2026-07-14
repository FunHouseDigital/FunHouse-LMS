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
