/**
 * Personal-data display guard (Req 17.1, 17.2). See design.md "POPIA on-device
 * protection".
 *
 * Capture write paths store personal fields as an encrypted `enc` blob on the
 * local record (via {@link ../ui/captureCommit}); the plaintext personal values
 * never touch IndexedDB. Reading them back therefore REQUIRES the in-memory
 * session key, which exists only while a user is authenticated (Req 17.2). This
 * module is the single seam a screen must go through to surface stored personal
 * data, so the "withhold until authenticated" rule cannot be bypassed by
 * accident:
 *
 *  - {@link canDisplayPersonalData} is the boolean guard a screen checks before
 *    attempting to show any stored personal field;
 *  - {@link readPersonalData} decrypts a record's `enc` blob, returning `null`
 *    when there is no session key (display withheld) or no blob to read.
 *
 * With no session key in memory, encrypted blobs are opaque and this module
 * yields nothing decryptable — exactly the POPIA guarantee (Req 17.2).
 */
import { decryptPayload, getSessionKey } from './crypto';
import type { EncryptedField } from './types';

/** A locally stored record that may carry an encrypted personal blob. */
export interface RecordWithPersonal {
  enc?: EncryptedField;
  [key: string]: unknown;
}

/**
 * True when stored personal data may be displayed — i.e. an in-memory session
 * key exists (the user is authenticated, Req 17.2). Defaults to the Crypto
 * service's current key; a key may be passed explicitly for testing/wiring.
 */
export function canDisplayPersonalData(key: CryptoKey | null = getSessionKey()): boolean {
  return key !== null;
}

/**
 * Decrypt a record's personal `enc` blob (Req 17.1). Returns `null` when:
 *  - there is no in-memory session key → display is withheld (Req 17.2); or
 *  - the record carries no `enc` blob (nothing personal stored).
 *
 * Screens MUST route stored personal-data reads through this helper (or
 * {@link canDisplayPersonalData}) so the withhold-until-authenticated rule is
 * enforced in one place rather than re-implemented per screen.
 */
export async function readPersonalData<T = Record<string, unknown>>(
  record: RecordWithPersonal | null | undefined,
  key: CryptoKey | null = getSessionKey(),
): Promise<T | null> {
  if (!record || !record.enc) return null;
  if (!key) return null; // withhold: no session key → nothing decryptable (Req 17.2)
  return decryptPayload<T>(key, record.enc);
}
