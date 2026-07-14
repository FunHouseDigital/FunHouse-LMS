import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import {
  canDraw,
  optimisticRemaining,
  sumPendingDraws,
  refreshCachedBalances,
  getOptimisticRemaining,
  type OptimisticRemaining,
} from './entitlementCalculator';
import {
  DB_NAME,
  closeDb,
  getBalances,
} from '../store/localStore';
import type { BalanceOut, StoredSyncAction, SyncStatus } from './types';

async function resetDb(): Promise<void> {
  await closeDb();
  await new Promise<void>((resolve, reject) => {
    const req = indexedDB.deleteDatabase(DB_NAME);
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error);
    req.onblocked = () => resolve();
  });
}

const ENT = 'ent-1';
const OTHER = 'ent-2';

/** A queued entitlement-draw action for a given entitlement. */
function drawAction(
  clientId: string,
  entitlementId: string,
  amount: number,
  status: SyncStatus = 'unsynced',
): StoredSyncAction {
  return {
    client_id: clientId,
    entity: 'entitlement',
    created_at: '2024-01-01T00:00:00.000Z',
    payload: { entitlement_id: entitlementId, amount },
    status,
    attempt_count: 0,
  };
}

describe('Entitlement_Calculator — optimistic balance (Property 10)', () => {
  // Feature: revenue-pwa, Property 10: Optimistic entitlement balance equals cached
  // minus pending draws. For any cached server balance (integer minutes, or unlimited)
  // and any set of pending local entitlement-draw actions for that entitlement, the
  // optimistic remaining equals the cached remaining_units minus the sum of pending
  // draw amounts (and is treated as unlimited when the cached value is null).
  // Validates: Requirements 8.2, 8.3
  it('Property 10: optimistic remaining = cached − Σ(pending draws)', () => {
    fc.assert(
      fc.property(
        fc.oneof(fc.constant(null), fc.integer({ min: 0, max: 100_000 })),
        fc.array(fc.integer({ min: 0, max: 500 }), { maxLength: 20 }),
        // Noise: draws for another entitlement, and non-unsynced draws for this one.
        fc.array(fc.integer({ min: 0, max: 500 }), { maxLength: 10 }),
        fc.array(fc.integer({ min: 0, max: 500 }), { maxLength: 10 }),
        (cached, pending, otherEntitlement, appliedDraws) => {
          const actions: StoredSyncAction[] = [
            ...pending.map((amt, i) => drawAction(`p-${i}`, ENT, amt, 'unsynced')),
            ...otherEntitlement.map((amt, i) => drawAction(`o-${i}`, OTHER, amt, 'unsynced')),
            ...appliedDraws.map((amt, i) => drawAction(`d-${i}`, ENT, amt, 'applied')),
          ];

          const expectedSum = pending.reduce((a, b) => a + b, 0);
          expect(sumPendingDraws(actions, ENT)).toBe(expectedSum);

          const opt = optimisticRemaining(cached, sumPendingDraws(actions, ENT));
          if (cached === null) {
            expect(opt).toBe('unlimited');
          } else {
            expect(opt).toBe(cached - expectedSum);
          }
          return true;
        },
      ),
      { numRuns: 100 },
    );
  });
});

describe('Entitlement_Calculator — draw gating (Property 11)', () => {
  // Feature: revenue-pwa, Property 11: Draws never exceed cached-minus-pending and are
  // never driven negative. For any entitlement state, a draw of amount is permitted iff
  // the entitlement is unlimited, or 0 < amount ≤ optimistic_remaining and
  // optimistic_remaining ≥ 0; a negative optimistic balance blocks every draw including
  // a zero-amount draw, and no accepted draw drives the optimistic remaining below zero.
  // Validates: Requirements 8.4, 8.6
  it('Property 11: draw permitted iff unlimited or 0 < amount ≤ remaining (≥0)', () => {
    fc.assert(
      fc.property(
        fc.oneof(
          fc.constant<OptimisticRemaining>('unlimited'),
          fc.integer({ min: -1000, max: 1000 }),
        ),
        fc.integer({ min: -100, max: 1000 }),
        (remaining, amount) => {
          const allowed = canDraw(remaining, amount);
          if (remaining === 'unlimited') {
            expect(allowed).toBe(true);
          } else {
            const expected = remaining >= 0 && amount > 0 && amount <= remaining;
            expect(allowed).toBe(expected);
            // A negative optimistic balance blocks everything, incl. a zero-amount draw.
            if (remaining < 0) expect(canDraw(remaining, 0)).toBe(false);
            // No accepted finite draw drives the remaining below zero.
            if (allowed) expect(remaining - amount).toBeGreaterThanOrEqual(0);
          }
          return true;
        },
      ),
      { numRuns: 200 },
    );
  });
});

describe('Entitlement_Calculator — balance refresh (Property 12)', () => {
  // Feature: revenue-pwa, Property 12: Refreshing balances replaces the cached value for
  // that player. For any player and any newly retrieved GET /players/{id}/entitlements
  // result, the cached server balance for that player is replaced by the retrieved value
  // (subsequent optimistic computation uses the new value).
  // Validates: Requirements 8.5
  const remainingArb = fc.oneof(fc.constant(null), fc.integer({ min: 0, max: 100_000 }));
  const balanceArb: fc.Arbitrary<BalanceOut> = fc.record({
    entitlement_id: fc.constant(ENT),
    product_id: fc.constant('prod-1'),
    remaining_units: remainingArb,
    valid_from: fc.constant('2024-01-01'),
    valid_to: fc.constant('2024-12-31'),
    status: fc.constant('active'),
  });

  it('Property 12: a refresh replaces the cached balance for the player', async () => {
    await fc.assert(
      fc.asyncProperty(balanceArb, balanceArb, async (first, second) => {
        await resetDb();
        const playerId = 'player-1';

        await refreshCachedBalances(playerId, [first]);
        const afterFirst = await getBalances(playerId);
        expect(afterFirst!.balances[0].remaining_units).toBe(first.remaining_units);

        // A fresh GET result replaces the cached value entirely.
        await refreshCachedBalances(playerId, [second]);
        const afterSecond = await getBalances(playerId);
        expect(afterSecond!.balances).toHaveLength(1);
        expect(afterSecond!.balances[0].remaining_units).toBe(second.remaining_units);

        // Subsequent optimistic computation uses the new value (no pending draws → equals it).
        const opt = await getOptimisticRemaining(playerId, ENT);
        if (second.remaining_units === null) {
          expect(opt).toBe('unlimited');
        } else {
          expect(opt).toBe(second.remaining_units);
        }
        return true;
      }),
      { numRuns: 100 },
    );
  });
});
