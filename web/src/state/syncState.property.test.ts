import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import {
  STALE_AFTER_MS,
  deriveSyncStatus,
  isStale,
  readSyncStatus,
} from './syncState';
import {
  DB_NAME,
  closeDb,
  enqueueAction,
  updateActionStatus,
  countUnsynced,
} from '../store/localStore';
import type { EntityType, SyncStatus } from '../domain/types';

async function resetDb(): Promise<void> {
  await closeDb();
  await new Promise<void>((resolve, reject) => {
    const req = indexedDB.deleteDatabase(DB_NAME);
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error);
    req.onblocked = () => resolve();
  });
}

const entityArb: fc.Arbitrary<EntityType> = fc.constantFrom(
  'player',
  'consent',
  'session',
  'payment',
);

const statusArb: fc.Arbitrary<SyncStatus> = fc.constantFrom(
  'unsynced',
  'applied',
  'skipped',
  'rejected',
  'blocked',
);

describe('Sync status — unsynced badge (Property 8)', () => {
  // Feature: revenue-pwa, Property 8: Unsynced badge equals the count of unsynced
  // actions. For any Sync_Queue, the displayed unsynced-items count equals the number
  // of actions with status unsynced, and it re-derives to the correct value after any
  // enqueue or reconcile (zero when none remain).
  // Validates: Requirements 6.1, 6.2, 6.3, 11.4
  it('Property 8: badge equals count of unsynced and re-derives after reconcile', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.array(fc.record({ entity: entityArb, status: statusArb }), { maxLength: 20 }),
        async (raws) => {
          await resetDb();
          for (let i = 0; i < raws.length; i++) {
            await enqueueAction(
              {
                client_id: `a-${i}`,
                entity: raws[i].entity,
                created_at: new Date(1_700_000_000_000 + i).toISOString(),
                payload: { v: i },
              },
              { status: raws[i].status },
            );
          }

          const expectedUnsynced = raws.filter((r) => r.status === 'unsynced').length;
          expect(await countUnsynced()).toBe(expectedUnsynced);

          const view = await readSyncStatus(Date.now());
          expect(view.unsyncedCount).toBe(expectedUnsynced);
          expect(view.synced).toBe(expectedUnsynced === 0);
          // Rejected actions are surfaced.
          expect(view.rejected.length).toBe(raws.filter((r) => r.status === 'rejected').length);

          // Reconcile: mark every remaining unsynced action applied → badge goes to 0.
          for (let i = 0; i < raws.length; i++) {
            if (raws[i].status === 'unsynced') {
              await updateActionStatus(`a-${i}`, 'applied');
            }
          }
          const after = await readSyncStatus(Date.now());
          expect(after.unsyncedCount).toBe(0);
          expect(after.synced).toBe(true);
          return true;
        },
      ),
      { numRuns: 100 },
    );
  });
});

describe('Sync status — stale-device threshold (Property 9)', () => {
  // Feature: revenue-pwa, Property 9: Stale-device warning triggers strictly after 5
  // full days. For any last_successful_sync timestamp and current device time, the
  // stale-device warning is shown if and only if the elapsed time is strictly greater
  // than 5 full days; the warning may coexist with the synced state.
  // Validates: Requirements 6.4
  it('Property 9: stale iff elapsed strictly greater than 5 full days', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 0, max: 4_102_444_800_000 }),
        // Deltas spanning well before and after the 5-day boundary, including exact.
        fc.oneof(
          fc.integer({ min: -10 * 24 * 60 * 60 * 1000, max: 10 * 24 * 60 * 60 * 1000 }),
          fc.constantFrom(
            STALE_AFTER_MS - 1,
            STALE_AFTER_MS,
            STALE_AFTER_MS + 1,
            0,
          ),
        ),
        (lastMs, delta) => {
          const lastIso = new Date(lastMs).toISOString();
          const now = lastMs + delta;
          const expected = delta > STALE_AFTER_MS;
          expect(isStale(lastIso, now)).toBe(expected);

          // Stale may coexist with the synced state (zero unsynced).
          const view = deriveSyncStatus({ unsyncedCount: 0, lastSuccessfulSync: lastIso, now });
          expect(view.synced).toBe(true);
          expect(view.stale).toBe(expected);
          return true;
        },
      ),
      { numRuns: 200 },
    );
  });

  it('treats a null last-sync as not-yet-stale', () => {
    expect(isStale(null, Date.now())).toBe(false);
  });
});
