import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import {
  ALLOWED_PLAYER_PERSONAL_FIELDS,
  buildRegistrationActions,
  canSubmitRegistration,
  type RegistrationInput,
} from './registration';
import type { ConsentType } from '../types';
import type { CaptureContext } from './types';

const CONSENT_KEYS: ConsentType[] = ['media', 'data_processing', 'participation', 'communications'];

function ctx(): CaptureContext {
  let n = 0;
  return { now: '2024-06-15T10:00:00.000Z', newId: () => `id-${n++}` };
}

const consentsArb = fc.record({
  media: fc.boolean(),
  data_processing: fc.boolean(),
  participation: fc.boolean(),
  communications: fc.boolean(),
});

describe('Registration validation (Property 15)', () => {
  // Feature: revenue-pwa, Property 15: Registration validation requires a non-empty name
  // and guardian confirmation. For any registration input, submission is permitted if and
  // only if the player name contains a non-whitespace character and the on-screen guardian
  // confirmation has been given.
  // Validates: Requirements 10.2, 10.3
  it('Property 15: submit permitted iff non-empty name AND guardian confirmed', () => {
    fc.assert(
      fc.property(
        // A mix of blank, whitespace-only, and real names.
        fc.oneof(fc.constant(''), fc.constant('   '), fc.string({ maxLength: 10 }), fc.constant(' Ada ')),
        fc.boolean(),
        consentsArb,
        (name, guardianConfirmed, consents) => {
          const input: RegistrationInput = { name, guardianConfirmed, consents };
          const expected = name.trim().length > 0 && guardianConfirmed === true;
          expect(canSubmitRegistration(input)).toBe(expected);
          return true;
        },
      ),
      { numRuns: 100 },
    );
  });
});

describe('Registration restricted personal fields (Property 16)', () => {
  // Feature: revenue-pwa, Property 16: Stored personal fields are restricted to the
  // allowed set. For any captured record written to the Local_Store, the persisted personal
  // fields are a subset of {player name, guardian phone, the four consent values} and never
  // include a national ID number or residential address.
  // Validates: Requirements 10.5, 17.5
  it('Property 16: player personal fields ⊆ allowed set; never id/address anywhere', () => {
    fc.assert(
      fc.property(
        fc.string({ minLength: 1, maxLength: 12 }).filter((s) => s.trim().length > 0),
        fc.option(fc.string({ maxLength: 12 }), { nil: undefined }),
        consentsArb,
        // Adversarial extra keys the UI must never persist.
        fc.record({
          id_number: fc.string(),
          address: fc.string(),
          national_id: fc.string(),
        }),
        (name, guardianPhone, consents, junk) => {
          // Simulate a loosely-typed input carrying disallowed keys.
          const input = { name, guardianPhone, consents, guardianConfirmed: true, ...junk } as unknown as RegistrationInput;
          const { records } = buildRegistrationActions(input, ctx());

          const playerRecord = records.find((r) => r.store === 'players')!;
          const personalKeys = Object.keys(playerRecord.personal ?? {});
          for (const key of personalKeys) {
            expect(ALLOWED_PLAYER_PERSONAL_FIELDS).toContain(key as (typeof ALLOWED_PLAYER_PERSONAL_FIELDS)[number]);
          }

          // No record (clear or personal) ever contains id/address fields.
          const serialised = JSON.stringify(records);
          expect(serialised).not.toContain('id_number');
          expect(serialised).not.toContain('national_id');
          expect(serialised).not.toContain('address');

          // One consent record per type (Req 10.4).
          const consentRecords = records.filter((r) => r.store === 'consents');
          expect(consentRecords).toHaveLength(CONSENT_KEYS.length);
          return true;
        },
      ),
      { numRuns: 100 },
    );
  });
});
