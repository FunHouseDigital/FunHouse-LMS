/**
 * Entitlement_Calculator — the optimistic, offline-safe entitlement balance
 * (Req 8). See design.md "Entitlement_Calculator" and "Draw vs create mapping".
 *
 * All arithmetic is in **integer minutes**, mirroring the server's convention
 * exactly (`entitlements/engine.py`). The optimistic remaining for an
 * entitlement is the last cached server balance minus the sum of pending
 * (unsynced) local draw amounts for that entitlement:
 *
 *   optimistic_remaining = cached_server_remaining_units − Σ(pending draws)
 *
 * A `null` cached value means the server entitlement is **unlimited**: draws are
 * always permitted and no displayed number is ever reduced.
 *
 * The pure functions (`sumPendingDraws`, `optimisticRemaining`, `canDraw`) hold
 * the rules and are property-tested directly (Properties 10 and 11); the async
 * helpers wire them to the Local_Store queue + cached balances.
 */
import type { BalanceOut, StoredSyncAction } from './types';
import {
  getActionsByStatus,
  getBalances,
  writeBalances,
} from '../store/localStore';

/** An entitlement's remaining minutes, or `unlimited`. */
export type OptimisticRemaining = number | 'unlimited';

/**
 * True when a queued action is a pending (unsynced) entitlement **draw** for
 * `entitlementId`: `entity === 'entitlement'`, a numeric `payload.amount`, and a
 * matching `payload.entitlement_id`.
 */
export function isPendingDrawFor(
  action: StoredSyncAction,
  entitlementId: string,
): boolean {
  if (action.entity !== 'entitlement' || action.status !== 'unsynced') return false;
  const payload = action.payload as Record<string, unknown>;
  return payload.entitlement_id === entitlementId && typeof payload.amount === 'number';
}

/** Sum the amounts of all pending draws for `entitlementId` in `actions`. */
export function sumPendingDraws(
  actions: StoredSyncAction[],
  entitlementId: string,
): number {
  let total = 0;
  for (const action of actions) {
    if (isPendingDrawFor(action, entitlementId)) {
      total += (action.payload as { amount: number }).amount;
    }
  }
  return total;
}

/**
 * Optimistic remaining minutes for an entitlement given its cached server value
 * and the sum of pending draws. `null` cached → `'unlimited'` (Req 8.2).
 */
export function optimisticRemaining(
  cachedRemaining: number | null,
  pendingDrawSum: number,
): OptimisticRemaining {
  if (cachedRemaining === null) return 'unlimited';
  return cachedRemaining - pendingDrawSum;
}

/**
 * Whether an entitlement draw of `amount` minutes is permitted (Req 8.4, 8.6):
 *  - unlimited entitlement → always permitted;
 *  - otherwise permitted iff `optimistic_remaining ≥ 0` and
 *    `0 < amount ≤ optimistic_remaining`.
 *
 * A negative optimistic balance blocks **every** draw, including a zero-amount
 * draw; an accepted draw never drives the optimistic remaining below zero.
 */
export function canDraw(remaining: OptimisticRemaining, amount: number): boolean {
  if (remaining === 'unlimited') return true;
  if (remaining < 0) return false;
  return amount > 0 && amount <= remaining;
}

/** Pre-confirm display model for a player's entitlement (Req 8.1). */
export interface EntitlementDisplay {
  entitlement_id: string;
  product_id: string;
  /** Optimistic remaining minutes, or `'unlimited'`. */
  remaining: OptimisticRemaining;
  /** Last cached server remaining units (integer minutes) or `null` (unlimited). */
  unitTotal: number | null;
  valid_from: string | null;
  valid_to: string | null;
  status: string;
}

/** Build the pre-confirm display model for one balance + pending draws (Req 8.1, 8.2). */
export function describeEntitlement(
  balance: BalanceOut,
  pendingDrawSum: number,
): EntitlementDisplay {
  return {
    entitlement_id: balance.entitlement_id,
    product_id: balance.product_id,
    remaining: optimisticRemaining(balance.remaining_units, pendingDrawSum),
    unitTotal: balance.remaining_units,
    valid_from: balance.valid_from,
    valid_to: balance.valid_to,
    status: balance.status,
  };
}

// ---- Local_Store-wired helpers ----

/**
 * Compute the optimistic remaining for a player's entitlement from the cached
 * server balance and the current unsynced draw queue (Req 8.2, 8.3).
 * Returns `null` if the entitlement is not present in the cached balances.
 */
export async function getOptimisticRemaining(
  playerId: string,
  entitlementId: string,
  cacheScope?: string | null,
): Promise<OptimisticRemaining | null> {
  const cached = await getBalances(playerId, cacheScope);
  const balance = cached?.balances.find((b) => b.entitlement_id === entitlementId);
  if (!balance) return null;
  const pending = await getActionsByStatus('unsynced');
  return optimisticRemaining(balance.remaining_units, sumPendingDraws(pending, entitlementId));
}

/**
 * The pre-confirm display models for every cached entitlement of a player, each
 * with pending draws applied (Req 8.1). Empty when nothing is cached.
 */
export async function getEntitlementDisplays(
  playerId: string,
  cacheScope?: string | null,
): Promise<EntitlementDisplay[]> {
  const cached = await getBalances(playerId, cacheScope);
  if (!cached) return [];
  const pending = await getActionsByStatus('unsynced');
  return cached.balances.map((b) =>
    describeEntitlement(b, sumPendingDraws(pending, b.entitlement_id)),
  );
}

/**
 * Decide whether a player may draw `amount` minutes from an entitlement, using
 * the optimistic balance (Req 8.4, 8.6). Unknown entitlements are blocked.
 */
export async function canPlayerDraw(
  playerId: string,
  entitlementId: string,
  amount: number,
  cacheScope?: string | null,
): Promise<boolean> {
  const remaining = await getOptimisticRemaining(playerId, entitlementId, cacheScope);
  if (remaining === null) return false;
  return canDraw(remaining, amount);
}

/**
 * Replace the cached server balances for a player with a freshly retrieved
 * `GET /players/{id}/entitlements` result (Req 8.5).
 */
export async function refreshCachedBalances(
  playerId: string,
  balances: BalanceOut[],
  cachedAt?: string,
  cacheScope?: string | null,
): Promise<void> {
  await writeBalances(playerId, balances, cachedAt, cacheScope);
}
