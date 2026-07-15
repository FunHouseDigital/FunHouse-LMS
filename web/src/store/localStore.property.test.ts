import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import {
  DB_NAME,
  closeDb,
  compareByCreatedAt,
  enqueueAction,
  getAllLocalRecords,
  getUnsyncedActions,
  writeLocalRecord,
  type LocalRecord,
} from './localStore';
import type { EntityType, SyncAction } from '../domain/types';

/** Drop the database entirely so each property run starts from a clean slate. */
async function resetDb(): Promise<void> {
  await closeDb();
  await new Promise<void>((resolve, reject) => {
    const req = indexedDB.deleteDatabase(DB_NAME);
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error);
    req.onblocked = () => resolve();
  });
}

const isoArb = fc
  .integer({ min: 0, max: 4_102_444_800_000 }) // 1970 .. 2100, fixed-width ISO strings
  .map((t) => new Date(t).toISOString());

const entityArb: fc.Arbitrary<EntityType> = fc.constantFrom(
  'player',
  'consent',
  'session',
  'attendance',
  'payment',
  'entitlement',
);

const payloadArb: fc.Arbitrary<Record<string, unknown>> = fc.oneof(
  fc.record({ player_id: fc.uuid() }),
  fc.record({ player_id: fc.uuid(), duration_minutes: fc.integer({ min: 0, max: 240 }) }),
  fc.constant({} as Record<string, unknown>),
);

describe('Local_Store persistence', () => {
  // Feature: revenue-pwa, Property 3: For any set of local records and queued
  // actions written to the Local_Store, closing and reopening the store yields
  // exactly the same records and pending queue entries (no loss, no reordering
  // of the unsynced set by created_at).
  // Validates: Requirements 4.5
  it('Property 3: round-trips records and the pending queue across relaunch', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.array(fc.record({ created_at: isoArb, entity: entityArb, payload: payloadArb }), {
          maxLength: 25,
        }),
        fc.array(
          fc.record({
            day: fc.constantFrom('2024-01-01', '2024-06-15', '2025-03-03'),
            player_id: fc.uuid(),
          }),
          { maxLength: 25 },
        ),
        async (rawActions, rawRecords) => {
          await resetDb();

          const actions: SyncAction[] = rawActions.map((a, i) => ({
            client_id: `act-${i}`,
            entity: a.entity,
            created_at: a.created_at,
            payload: a.payload,
          }));
          for (const action of actions) {
            await enqueueAction(action);
          }

          const records: LocalRecord[] = rawRecords.map((r, i) => ({
            local_id: `rec-${i}`,
            day: r.day,
            player_id: r.player_id,
          }));
          for (const record of records) {
            await writeLocalRecord('sessions', record);
          }

          // Simulate an app relaunch: fully close, then reopen on next access.
          await closeDb();

          const readActions = await getUnsyncedActions();
          const readRecords = await getAllLocalRecords('sessions');

          // No loss.
          expect(readActions).toHaveLength(actions.length);
          expect(readRecords).toHaveLength(records.length);

          // Unsynced set ordered by created_at then client_id (no reordering loss).
          const expectedOrder = [...actions].sort(compareByCreatedAt).map((a) => a.client_id);
          expect(readActions.map((a) => a.client_id)).toEqual(expectedOrder);

          // Every action round-trips faithfully.
          const byId = new Map(readActions.map((a) => [a.client_id, a]));
          for (const action of actions) {
            const got = byId.get(action.client_id);
            expect(got).toBeDefined();
            expect(got!.created_at).toBe(action.created_at);
            expect(got!.entity).toBe(action.entity);
            expect(got!.payload).toEqual(action.payload);
            expect(got!.status).toBe('unsynced');
          }

          // Every local record round-trips faithfully.
          const recById = new Map(readRecords.map((r) => [r.local_id, r]));
          for (const record of records) {
            expect(recById.get(record.local_id)).toEqual(record);
          }

          return true;
        },
      ),
      { numRuns: 100 },
    );
  });
});
