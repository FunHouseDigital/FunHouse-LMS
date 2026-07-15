import { beforeAll, describe, it, expect } from 'vitest';
import fc from 'fast-check';
import {
  decrypt,
  decryptPayload,
  deriveKey,
  encrypt,
  encryptPayload,
  generateSalt,
} from './crypto';

/** True if `needle` appears as a contiguous byte subsequence of `haystack`. */
function containsSubsequence(haystack: Uint8Array, needle: Uint8Array): boolean {
  if (needle.length === 0) return true;
  outer: for (let i = 0; i + needle.length <= haystack.length; i++) {
    for (let j = 0; j < needle.length; j++) {
      if (haystack[i + j] !== needle[j]) continue outer;
    }
    return true;
  }
  return false;
}

function base64ToBytes(b64: string): Uint8Array {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

// A single derived key is reused across property runs: the round-trip/no-plaintext
// invariants hold for any key, and this avoids paying PBKDF2 cost per iteration.
let key: CryptoKey;

beforeAll(async () => {
  key = await deriveKey('login-time-secret', generateSalt());
});

describe('Crypto service (AES-GCM + PBKDF2)', () => {
  it('derived key round-trips a simple string and uses a fresh IV each time', async () => {
    const a = await encrypt(key, 'Thandi Mokoena');
    const b = await encrypt(key, 'Thandi Mokoena');
    // Random 96-bit IV => same plaintext yields different envelopes.
    expect(a.iv).not.toBe(b.iv);
    expect(a.ciphertext).not.toBe(b.ciphertext);
    expect(await decrypt(key, a)).toBe('Thandi Mokoena');
    expect(await decrypt(key, b)).toBe('Thandi Mokoena');
  });

  // Feature: revenue-pwa, Property 21: For any personal-data payload, encrypting
  // then decrypting with the session key yields the original payload, and the
  // bytes persisted to IndexedDB do not contain the plaintext personal fields.
  // Validates: Requirements 17.1
  it('Property 21: personal data round-trips and is never stored as plaintext', async () => {
    const personalArb = fc.record({
      name: fc.string({ minLength: 5, maxLength: 40 }),
      guardian_phone: fc.string({ minLength: 5, maxLength: 20 }),
      consent: fc.constantFrom('media', 'data_processing', 'participation', 'communications'),
    });

    await fc.assert(
      fc.asyncProperty(personalArb, async (payload) => {
        const field = await encryptPayload(key, payload);

        // Round-trip equals the original payload.
        const decrypted = await decryptPayload<typeof payload>(key, field);
        expect(decrypted).toEqual(payload);

        // The persisted bytes (iv + ciphertext) never contain the plaintext
        // personal fields.
        const plaintextBytes = new TextEncoder().encode(JSON.stringify(payload));
        const persisted = new Uint8Array([
          ...base64ToBytes(field.iv),
          ...base64ToBytes(field.ciphertext),
        ]);
        expect(containsSubsequence(persisted, plaintextBytes)).toBe(false);

        // Individual sensitive field values must also be absent from the bytes.
        const nameBytes = new TextEncoder().encode(payload.name);
        const phoneBytes = new TextEncoder().encode(payload.guardian_phone);
        expect(containsSubsequence(persisted, nameBytes)).toBe(false);
        expect(containsSubsequence(persisted, phoneBytes)).toBe(false);

        return true;
      }),
      { numRuns: 100 },
    );
  });
});
