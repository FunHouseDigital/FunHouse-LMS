import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import { SyncEngine } from './syncEngine';
import {
  DB_NAME,
  closeDb,
  enqueueAction,
  getAction,
  getActionsByStatus,
  countUnsynced,
} from '../store/localStore';
import type { ActionResult, EntityType, SyncAction, SyncResult } from './types';

/** Drop the database so each property run starts from a clean slate. */
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
  .integer({ min: 0, max: 4_102_444_800_000 })
  .map((t) => new Date(t).toISOString());

const entityArb: fc.Arbitrary<EntityType> = fc.constantFrom(
  'player',
  'consent',
  'session',
  'attendance',
  'payment',
  'entitlement',
);

/** A recording mock of the Container_API `sync` method. */
function makeMock(handler: (actions: SyncAction[]) => SyncResult | Promise<SyncResult>) {
  const batches: SyncAction[][] = [];
  const client = {
    async sync(actions: SyncAction[]): Promise<SyncResult> {
      batches.push(actions.map((a) => ({ ...a })));
      return handler(actions);
    },
  };
  return { batches, client };
}

interface RawAction {
  created_at: string;
  entity: EntityType;
}

/** Enqueue index-keyed actions (unique client_ids, no player refs → no D2 deferral). */
async function enqueueAll(raws: RawAction[]): Promise<SyncAction[]> {
  const actions: SyncAction[] = raws.map((r, i) => ({
    client_id: `a-${i}`,
    entity: r.entity,
    created_at: r.created_at,
    payload: { v: i },
  }));
  for (const action of actions) {
    await enqueueAction(action);
  }
  return actions;
}

describe('Sync_Engine flush() — reconcile properties', () => {
  // Feature: revenue-pwa, Property 1: Sync-queue idempotency on re-flush. For any
  // Sync_Queue and any POST /sync response marking a subset of actions
  // applied/skipped, running flush() again produces a batch that contains none of
  // those terminal actions and issues no duplicate action for them; and applying
  // the same reconcile twice yields the same queue state as applying it once.
  // Validates: Requirements 5.3, 5.4
  it('Property 1: re-flush never re-sends terminal actions (idempotent)', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.array(
          fc.record({
            created_at: isoArb,
            entity: entityArb,
            terminal: fc.boolean(),
            skipped: fc.boolean(),
          }),
          { maxLength: 15 },
        ),
        async (raws) => {
          await resetDb();
          await enqueueAll(raws);
          const terminal = new Map<string, 'applied' | 'skipped'>();
          raws.forEach((r, i) => {
            if (r.terminal) terminal.set(`a-${i}`, r.skipped ? 'skipped' : 'applied');
          });

          const mock = makeMock((batch) => ({
            results: batch
              .filter((a) => terminal.has(a.client_id))
              .map<ActionResult>((a) => ({
                client_id: a.client_id,
                entity: a.entity,
                status: terminal.get(a.client_id)!,
                record_id: null,
                reason: null,
              })),
          }));
          const engine = new SyncEngine({ client: mock.client });

          await engine.flush();
          const terminalAfter1 = new Set(
            [...(await getActionsByStatus('applied')), ...(await getActionsByStatus('skipped'))].map(
              (a) => a.client_id,
            ),
          );
          // Exactly the marked subset became terminal.
          expect(terminalAfter1).toEqual(new Set(terminal.keys()));

          await engine.flush();

          // The second flush's batch (if any) re-sends none of the terminal actions.
          if (mock.batches.length > 1) {
            const secondBatchIds = mock.batches[1].map((a) => a.client_id);
            for (const id of secondBatchIds) {
              expect(terminal.has(id)).toBe(false);
            }
          }

          // Reconcile is idempotent: terminal set is unchanged after a second flush.
          const terminalAfter2 = new Set(
            [...(await getActionsByStatus('applied')), ...(await getActionsByStatus('skipped'))].map(
              (a) => a.client_id,
            ),
          );
          expect(terminalAfter2).toEqual(terminalAfter1);
          return true;
        },
      ),
      { numRuns: 100 },
    );
  });

  // Feature: revenue-pwa, Property 5: created_at and client_id are preserved across
  // retries. For any Sync_Action and any number of successive flush() attempts
  // (including intervening network errors), the action's created_at and client_id
  // transmitted to POST /sync remain byte-for-byte identical to the values assigned
  // at capture.
  // Validates: Requirements 5.7
  it('Property 5: created_at/client_id are byte-identical across retries', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.array(fc.record({ created_at: isoArb, entity: entityArb }), {
          minLength: 1,
          maxLength: 12,
        }),
        async (raws) => {
          await resetDb();
          const actions = await enqueueAll(raws);
          const original = new Map(actions.map((a) => [a.client_id, a.created_at]));

          // First attempt fails at the transport (network error) → retain queue.
          let failNext = true;
          const mock = makeMock((batch) => {
            if (failNext) {
              failNext = false;
              throw new Error('network down');
            }
            return {
              results: batch.map<ActionResult>((a) => ({
                client_id: a.client_id,
                entity: a.entity,
                status: 'applied',
                record_id: null,
                reason: null,
              })),
            };
          });
          const engine = new SyncEngine({ client: mock.client });

          const r1 = await engine.flush(); // network error, queue retained
          expect(r1.outcome).toBe('network-error');
          const r2 = await engine.flush(); // success
          expect(r2.outcome).toBe('ok');

          // Every transmitted action carried the original identity, every time.
          for (const batch of mock.batches) {
            for (const sent of batch) {
              expect(original.has(sent.client_id)).toBe(true);
              expect(sent.created_at).toBe(original.get(sent.client_id));
            }
          }
          return true;
        },
      ),
      { numRuns: 100 },
    );
  });

  // Feature: revenue-pwa, Property 6: Reconcile clears terminal actions and retains
  // rejections with a reason. For any batch and any POST /sync result set (in any
  // order), each action matched by client_id with applied or skipped is removed from
  // the unsynced set, and each rejected action is retained locally with its returned
  // reason recorded and surfaced.
  // Validates: Requirements 5.3, 5.4, 5.6, 6.5
  it('Property 6: applied/skipped cleared; rejected retained with reason (order-independent)', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.array(
          fc.record({
            created_at: isoArb,
            entity: entityArb,
            status: fc.constantFrom<'applied' | 'skipped' | 'rejected'>(
              'applied',
              'skipped',
              'rejected',
            ),
            reason: fc.string(),
            sortKey: fc.integer(),
          }),
          { maxLength: 15 },
        ),
        async (raws) => {
          await resetDb();
          await enqueueAll(raws);

          const results: ActionResult[] = raws
            .map((r, i) => ({
              client_id: `a-${i}`,
              entity: r.entity,
              status: r.status,
              record_id: r.status === 'applied' ? `srv-${i}` : null,
              reason: r.status === 'rejected' ? r.reason : null,
              sortKey: r.sortKey,
            }))
            // Shuffle: results arrive in an arbitrary order (matching is by client_id).
            .sort((a, b) => a.sortKey - b.sortKey)
            .map(({ sortKey: _sortKey, ...rest }) => rest);

          const mock = makeMock(() => ({ results }));
          const engine = new SyncEngine({ client: mock.client });
          await engine.flush();

          for (let i = 0; i < raws.length; i++) {
            const stored = await getAction(`a-${i}`);
            expect(stored).toBeDefined();
            if (raws[i].status === 'rejected') {
              expect(stored!.status).toBe('rejected');
              expect(stored!.reason).toBe(raws[i].reason);
            } else {
              expect(stored!.status).toBe(raws[i].status);
            }
          }

          // Terminal actions are gone from the unsynced set; rejected are retained.
          const unsynced = new Set((await getActionsByStatus('unsynced')).map((a) => a.client_id));
          raws.forEach((_r, i) => {
            expect(unsynced.has(`a-${i}`)).toBe(false);
          });
          const rejected = await getActionsByStatus('rejected');
          expect(rejected.length).toBe(raws.filter((r) => r.status === 'rejected').length);
          return true;
        },
      ),
      { numRuns: 100 },
    );
  });

  // Feature: revenue-pwa, Property 7: Network error retains the entire queue
  // unchanged. For any Sync_Queue, when POST /sync fails with a network error, the
  // set of Unsynced_Items after the attempt equals the set before the attempt (only
  // attempt_count may change).
  // Validates: Requirements 5.5
  it('Property 7: a network error leaves the unsynced set unchanged', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.array(fc.record({ created_at: isoArb, entity: entityArb }), { maxLength: 15 }),
        async (raws) => {
          await resetDb();
          await enqueueAll(raws);

          const before = (await getActionsByStatus('unsynced')).map((a) => a.client_id).sort();
          const countBefore = await countUnsynced();

          const mock = makeMock(() => {
            throw new Error('offline');
          });
          const engine = new SyncEngine({ client: mock.client });
          const result = await engine.flush();

          if (raws.length === 0) {
            expect(result.outcome).toBe('empty');
          } else {
            expect(result.outcome).toBe('network-error');
          }

          const after = (await getActionsByStatus('unsynced')).map((a) => a.client_id).sort();
          expect(after).toEqual(before);
          expect(await countUnsynced()).toBe(countBefore);
          return true;
        },
      ),
      { numRuns: 100 },
    );
  });
});
