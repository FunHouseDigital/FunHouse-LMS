/**
 * Registration + consent capture builder — `Registration_Module` (Req 10, 17.5).
 * See design.md "Registration + consent — Registration_Module".
 *
 * Pure builder: given a validated registration input it produces a `player`
 * record + action and one `consent` record + action per Consent_Type. Personal
 * fields are collected into a `personal` sub-object (restricted to the allowed
 * set — Property 16) so the screen can encrypt them at rest (Req 17.1). The
 * player action's `client_id` is used as the local player id that dependent
 * consent actions reference (Dependency D2 resolves it to the server id later).
 */
import type { ConsentType } from '../types';
import type { CaptureContext, CaptureResult } from './types';
import { dayOf } from './types';

/** The four guardian consent categories captured at registration (Req 10.1). */
export const CONSENT_TYPES: readonly ConsentType[] = [
  'media',
  'data_processing',
  'participation',
  'communications',
];

/**
 * The ONLY personal fields the client persists for a player (Req 10.5, 17.5).
 * National ID numbers and residential addresses are never collected or stored.
 */
export const ALLOWED_PLAYER_PERSONAL_FIELDS = ['name', 'guardian_phone'] as const;

/** Registration form input. `consents` maps each Consent_Type to its toggle. */
export interface RegistrationInput {
  name: string;
  guardianPhone?: string;
  consents: Record<ConsentType, boolean>;
  /** True once the on-screen guardian confirmation has been given (Req 10.3). */
  guardianConfirmed: boolean;
}

/** True iff `name` contains at least one non-whitespace character (Req 10.2). */
export function hasNonEmptyName(name: string | null | undefined): boolean {
  return typeof name === 'string' && name.trim().length > 0;
}

/**
 * Registration may be submitted iff the player name is non-empty AND the
 * on-screen guardian confirmation has been given (Req 10.2, 10.3 — Property 15).
 */
export function canSubmitRegistration(input: Partial<RegistrationInput> | null | undefined): boolean {
  if (!input) return false;
  return hasNonEmptyName(input.name) && input.guardianConfirmed === true;
}

/**
 * Collect the allowed personal fields ONLY (Req 10.5, 17.5 — Property 16). Any
 * other keys present on a loosely-typed input (e.g. an injected `id_number` or
 * `address`) are ignored and never persisted.
 */
export function collectPersonalFields(input: RegistrationInput): Record<string, string> {
  const personal: Record<string, string> = { name: input.name.trim() };
  if (typeof input.guardianPhone === 'string' && input.guardianPhone.length > 0) {
    personal.guardian_phone = input.guardianPhone;
  }
  return personal;
}

/** Split a captured full name into `first_name` (+ optional `last_name`). */
function splitName(name: string): { first_name: string; last_name?: string } {
  const parts = name.trim().split(/\s+/);
  const first_name = parts[0] ?? '';
  const last_name = parts.length > 1 ? parts.slice(1).join(' ') : undefined;
  return last_name ? { first_name, last_name } : { first_name };
}

/**
 * Build the local records + Sync_Actions for a submitted registration (Req 10.4):
 * one `player` (record + action) and one `consent` (record + action) per
 * Consent_Type. The caller must have checked {@link canSubmitRegistration}.
 */
export function buildRegistrationActions(input: RegistrationInput, ctx: CaptureContext): CaptureResult {
  const day = dayOf(ctx.now);
  const playerClientId = ctx.newId();
  const playerId = playerClientId; // local player id dependents reference (D2)
  const nameParts = splitName(input.name);
  const personal = collectPersonalFields(input);

  const result: CaptureResult = {
    records: [
      {
        store: 'players',
        record: {
          local_id: playerId,
          client_id: playerClientId,
          player_id: playerId,
          day,
        },
        // name + guardian_phone encrypted at rest (Req 17.1).
        personal,
      },
    ],
    actions: [
      {
        action: {
          client_id: playerClientId,
          entity: 'player',
          created_at: ctx.now,
          payload: { ...nameParts },
        },
      },
    ],
  };

  for (const consentType of CONSENT_TYPES) {
    const granted = input.consents[consentType] === true;
    const consentClientId = ctx.newId();
    result.records.push({
      store: 'consents',
      record: {
        local_id: consentClientId,
        client_id: consentClientId,
        player_id: playerId,
      },
      personal: { consent_type: consentType, granted },
    });
    result.actions.push({
      action: {
        client_id: consentClientId,
        entity: 'consent',
        created_at: ctx.now,
        payload: {
          player_id: playerId,
          consent_type: consentType,
          granted,
          granted_at: ctx.now,
        },
      },
    });
  }

  return result;
}
