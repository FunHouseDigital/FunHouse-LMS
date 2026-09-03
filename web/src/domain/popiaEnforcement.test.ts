/**
 * POPIA enforcement wiring tests (Task 14.1 — Req 17.1, 17.2, 17.4, 17.5).
 *
 * These are structural/wiring tests that verify the POPIA guarantees hold
 * end-to-end across the capture write paths, the display guard, the API client,
 * and the domain types — complementing the pure crypto round-trip (Property 21,
 * crypto.test.ts) and the offline-capture property (Property 4).
 *
 *  - Personal fields are encrypted at rest across every capture write path:
 *    after commitCapture with a session key the persisted record carries an
 *    `enc` blob and NO plaintext personal value; with no key the personal
 *    fields are absent (never written in the clear). (Req 17.1)
 *  - Display of stored personal data is withheld with no session key. (Req 17.2)
 *  - HTTPS-only base URLs are enforced by the API client. (Req 17.4)
 *  - No national ID / residential address fields are ever collected or
 *    persisted by the capture builders or domain types. (Req 17.5)
 */
import { afterEach, describe, expect, it } from 'vitest';
import {
  DB_NAME,
  closeDb,
  getAllLocalRecords,
  getDb,
  type LocalRecordStore,
} from '../store/localStore';
import { canDisplayPersonalData, readPersonalData } from './personalData';
import { commitCapture } from '../ui/captureCommit';
import {
  ALLOWED_PLAYER_PERSONAL_FIELDS,
  buildRegistrationActions,
  collectPersonalFields,
  type RegistrationInput,
} from './captures/registration';
import { buildSessionActions } from './captures/session';
import { buildSellActions } from './captures/sell';
import { buildAttendanceActions } from './captures/attendance';
import { buildMetricsActions } from './captures/metrics';
import type { CaptureContext, CaptureResult } from './captures/types';
import { ContainerApiClient, isAllowedBaseUrl } from '../api/client';
import type { LocalDataOwner } from './types';
import { activateTestOwner } from '../setupTests';

const ALL_RECORD_STORES: LocalRecordStore[] = [
  'players',
  'sessions',
  'payments',
  'entitlements',
  'consents',
  'attendance',
  'student_metrics',
];

/** Personal values used across the fixtures; none of these may appear at rest in clear. */
const PLAYER_NAME = 'Thandi Mokoena';
const GUARDIAN_PHONE = '0821234567';
const STUDENT_NAME = 'Sipho Dlamini';

/** Field names that must NEVER be collected or persisted anywhere (Req 17.5, 10.5). */
const FORBIDDEN_FIELDS = [
  'id_number',
  'national_id',
  'idnumber',
  'nationalid',
  'id_no',
  'address',
  'residential_address',
  'street',
  'postal_code',
  'postcode',
  'zip',
  'zip_code',
];

async function resetDb(): Promise<void> {
  await closeDb();
  await new Promise<void>((resolve, reject) => {
    const req = indexedDB.deleteDatabase(DB_NAME);
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error);
    req.onblocked = () => resolve();
  });
}

function ctx(): CaptureContext {
  let n = 0;
  return {
    now: '2024-06-15T10:00:00.000Z',
    newId: () => `id-${n++}`,
  };
}

function registrationInput(): RegistrationInput {
  return {
    name: PLAYER_NAME,
    guardianPhone: GUARDIAN_PHONE,
    guardianConfirmed: true,
    consents: {
      media: true,
      data_processing: true,
      participation: false,
      communications: false,
    },
  };
}

/** Every capture builder's output, exercised together to cover all write paths. */
function allCaptureResults(c: CaptureContext): CaptureResult[] {
  return [
    buildRegistrationActions(registrationInput(), c),
    buildSessionActions(
      { playerId: 'p1', console: 'PS5', durationMinutes: 60, payment: { method: 'cash', amountCents: 3000 } },
      c,
    ),
    buildSellActions(
      { kind: 'subscription', playerId: 'p1', priceCents: 35_000, productId: 'prod', memberIds: ['p1', 'p2'] },
      c,
    ),
    buildAttendanceActions(
      { sessionType: 'lesson', reference: 'ref-1', roster: [{ playerId: 'p1', present: true }] },
      c,
    ),
    buildMetricsActions({ studentName: STUDENT_NAME, wpm: 40, accuracy: 95 }, c),
  ];
}

async function freshOwner(): Promise<LocalDataOwner> {
  await resetDb();
  return activateTestOwner();
}

afterEach(async () => {
  await closeDb();
});

describe('POPIA: personal fields are encrypted at rest across capture write paths (Req 17.1)', () => {
  it('registration persists name + guardian phone only inside an encrypted blob', async () => {
    const owner = await freshOwner();
    const result = buildRegistrationActions(registrationInput(), ctx());
    await commitCapture(result, { owner });

    const players = await getAllLocalRecords('players', owner);
    expect(players).toHaveLength(1);
    const player = players[0];

    // The sensitive fields live only in the `enc` blob; no plaintext columns.
    expect(player.enc).toBeDefined();
    expect(player.name).toBeUndefined();
    expect(player.guardian_phone).toBeUndefined();

    // Non-sensitive index keys remain in clear so IndexedDB indexing works.
    expect(player.local_id).toBeTruthy();
    expect(player.player_id).toBeTruthy();
    expect(player.day).toBe('2024-06-15');

    // The serialized record must not leak the plaintext personal values.
    const serialized = JSON.stringify(player);
    expect(serialized).not.toContain(PLAYER_NAME);
    expect(serialized).not.toContain(GUARDIAN_PHONE);

    // The blob decrypts back to exactly the personal payload.
    const decrypted = await readPersonalData<Record<string, string>>(player, owner, 'players');
    expect(decrypted).toEqual({ name: PLAYER_NAME, guardian_phone: GUARDIAN_PHONE });
  });

  it('consent records encrypt their consent metadata at rest', async () => {
    const owner = await freshOwner();
    const result = buildRegistrationActions(registrationInput(), ctx());
    await commitCapture(result, { owner });

    const consents = await getAllLocalRecords('consents', owner);
    expect(consents.length).toBe(4);
    for (const consent of consents) {
      expect(consent.enc).toBeDefined();
      expect(consent.consent_type).toBeUndefined();
      expect(consent.granted).toBeUndefined();
      const decrypted = await readPersonalData<Record<string, unknown>>(consent, owner, 'consents');
      expect(Object.keys(decrypted!).sort()).toEqual(['consent_type', 'granted']);
    }
  });

  it('metrics persists the student name only inside an encrypted blob', async () => {
    const owner = await freshOwner();
    const result = buildMetricsActions({ studentName: STUDENT_NAME, wpm: 40, accuracy: 95 }, ctx());
    await commitCapture(result, { owner });

    const metrics = await getAllLocalRecords('student_metrics', owner);
    expect(metrics.length).toBeGreaterThan(0);
    for (const row of metrics) {
      expect(row.enc).toBeDefined();
      expect(row.player_name).toBeUndefined();
      expect(JSON.stringify(row)).not.toContain(STUDENT_NAME);
      const decrypted = await readPersonalData<Record<string, unknown>>(row, owner, 'student_metrics');
      expect(decrypted).toEqual({ player_name: STUDENT_NAME });
    }
  });

  it('no raw record across any write path contains plaintext personal, activity, or financial fields', async () => {
    const owner = await freshOwner();
    const c = ctx();
    for (const result of allCaptureResults(c)) {
      await commitCapture(result, { owner });
    }
    const db = await getDb();
    const raw: unknown[] = [];
    for (const store of ALL_RECORD_STORES) raw.push(...await db.getAll(store));
    const serialized = JSON.stringify(raw);
    for (const value of [PLAYER_NAME, GUARDIAN_PHONE, STUDENT_NAME, 'PS5', 'ref-1', 'prod', 'subscription']) {
      expect(serialized).not.toContain(value);
    }
    for (const row of raw as Array<Record<string, unknown>>) {
      expect(Object.keys(row).every((key) => ['local_id', 'client_id', 'sync_scope', 'envelope'].includes(key))).toBe(true);
    }
  });

  it('with NO owner capability, capture is rejected before any local mutation — fail-safe', async () => {
    await resetDb();
    const c = ctx();
    for (const result of allCaptureResults(c)) {
      await expect(commitCapture(result, { owner: null as unknown as LocalDataOwner })).rejects.toThrow(
        'Authenticated local-data owner key is unavailable',
      );
    }

    const db = await getDb();
    for (const store of ALL_RECORD_STORES) {
      expect(await db.count(store)).toBe(0);
    }
    expect(await db.count('sync_queue')).toBe(0);
  });
});

describe('POPIA: display of stored personal data is withheld until authenticated (Req 17.2)', () => {
  it('canDisplayPersonalData requires a LocalDataOwner capability', async () => {
    const owner = await freshOwner();
    expect(canDisplayPersonalData()).toBe(false);
    expect(canDisplayPersonalData(null)).toBe(false);
    expect(canDisplayPersonalData(owner)).toBe(true);
  });

  it('readPersonalData yields nothing without an owner and decrypts with the owner capability', async () => {
    const owner = await freshOwner();
    const result = buildRegistrationActions(registrationInput(), ctx());
    await commitCapture(result, { owner });
    const player = (await getAllLocalRecords('players', owner))[0];

    expect(await readPersonalData(player)).toBeNull();
    expect(await readPersonalData(player, owner, 'players')).toEqual({
      name: PLAYER_NAME,
      guardian_phone: GUARDIAN_PHONE,
    });
  });

  it('readPersonalData fails closed when a retained same-scope owner becomes stale', async () => {
    await resetDb();
    let current = true;
    const owner = await activateTestOwner('display-subject', undefined, 1, () => current);
    const result = buildRegistrationActions(registrationInput(), ctx());
    await commitCapture(result, { owner });
    const player = (await getAllLocalRecords('players', owner))[0];

    current = false;
    expect(canDisplayPersonalData(owner)).toBe(false);
    expect(await readPersonalData(player, owner, 'players')).toBeNull();
  });

  it('readPersonalData returns null for a record with no encrypted blob', async () => {
    const owner = await freshOwner();
    expect(await readPersonalData({ local_id: 'x' }, owner)).toBeNull();
    expect(await readPersonalData(null, owner)).toBeNull();
  });
});

describe('POPIA: HTTPS-only transport for Container_API and sync (Req 17.4)', () => {
  it('rejects non-HTTPS (non-localhost) base URLs at construction', () => {
    expect(() => new ContainerApiClient({ baseUrl: 'http://api.funhouse.example' })).toThrow();
    expect(isAllowedBaseUrl('http://api.funhouse.example')).toBe(false);
    expect(isAllowedBaseUrl('ftp://api.funhouse.example')).toBe(false);
    expect(isAllowedBaseUrl('not a url')).toBe(false);
  });

  it('allows HTTPS anywhere and plain HTTP only for localhost/loopback (documented dev exception)', () => {
    expect(isAllowedBaseUrl('https://api.funhouse.example')).toBe(true);
    expect(isAllowedBaseUrl('http://localhost:8000')).toBe(true);
    expect(isAllowedBaseUrl('http://127.0.0.1:8000')).toBe(true);
    expect(() => new ContainerApiClient({ baseUrl: 'https://api.funhouse.example' })).not.toThrow();
    expect(() => new ContainerApiClient({ baseUrl: 'http://localhost:8000' })).not.toThrow();
  });
});

describe('POPIA: no national ID / residential address fields (Req 17.5, 10.5)', () => {
  it('collectPersonalFields yields only the allowed set even when extra fields are injected', () => {
    const injected = {
      ...registrationInput(),
      // These must be ignored — never collected or persisted.
      id_number: '0001010000088',
      address: '12 Long Street',
      residential_address: '12 Long Street',
    } as unknown as RegistrationInput;

    const personal = collectPersonalFields(injected);
    const keys = Object.keys(personal).sort();
    expect(keys).toEqual(['guardian_phone', 'name']);
    // Every produced key is within the documented allow-list.
    for (const k of keys) {
      expect(ALLOWED_PLAYER_PERSONAL_FIELDS as readonly string[]).toContain(k);
    }
  });

  it('collectPersonalFields omits the guardian phone when not provided (minimal fields)', () => {
    const personal = collectPersonalFields({
      name: PLAYER_NAME,
      guardianConfirmed: true,
      consents: { media: false, data_processing: false, participation: false, communications: false },
    });
    expect(Object.keys(personal)).toEqual(['name']);
  });

  it('no forbidden field name is produced by any capture builder (record keys, personal keys, or action payloads)', () => {
    const c = ctx();
    const producedKeys = new Set<string>();

    for (const result of allCaptureResults(c)) {
      for (const captureRecord of result.records) {
        Object.keys(captureRecord.record).forEach((k) => producedKeys.add(k.toLowerCase()));
        if (captureRecord.personal) {
          Object.keys(captureRecord.personal).forEach((k) => producedKeys.add(k.toLowerCase()));
        }
      }
      for (const captureAction of result.actions) {
        Object.keys(captureAction.action.payload).forEach((k) => producedKeys.add(k.toLowerCase()));
      }
    }

    for (const forbidden of FORBIDDEN_FIELDS) {
      expect(producedKeys.has(forbidden)).toBe(false);
    }
  });
});
