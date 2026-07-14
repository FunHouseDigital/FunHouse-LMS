/**
 * Sell capture builder — `Sell_Module` (Req 12). See design.md "Sell —
 * Sell_Module".
 *
 * Pure builder: given a sale input it produces a `payment` record + action and,
 * for a subscription or Holiday Special, an `entitlement` **create** action
 * (`{player_id, product_id}`) per included member. The four-member subscription
 * cap is a pure reducer (`addSubscriptionMember`) enforced by the screen and
 * property-tested directly (Property 18).
 */
import type { CaptureContext, CaptureResult } from './types';
import { dayOf } from './types';

/** Product kinds offered by the Sell screen (Req 12.1). */
export type SellKind = 'pay_per_use' | 'subscription' | 'holiday_special';

/** The R350 subscription price in cents (Req 12.2). */
export const SUBSCRIPTION_PRICE_CENTS = 35_000;

/** A new subscription may include at most four members (Req 12.2, 12.4). */
export const MAX_SUBSCRIPTION_MEMBERS = 4;

/**
 * Add a member to a subscription, preventing a fifth (Req 12.4 — Property 18).
 * Duplicates are ignored; once four members are present, further adds are no-ops.
 */
export function addSubscriptionMember(members: readonly string[], id: string): string[] {
  if (members.includes(id)) return [...members];
  if (members.length >= MAX_SUBSCRIPTION_MEMBERS) return [...members];
  return [...members, id];
}

/** True when another member may still be added to the subscription (Req 12.4). */
export function canAddSubscriptionMember(members: readonly string[]): boolean {
  return members.length < MAX_SUBSCRIPTION_MEMBERS;
}

/** Validated Sell input. */
export interface SellInput {
  kind: SellKind;
  /** The paying player. */
  playerId: string;
  /** Sale price in integer cents (from the cached product catalog). */
  priceCents: number;
  /** The purchased product id (drives the server-side entitlement create). */
  productId?: string;
  /** Subscription members to grant the entitlement to (≤ 4). Ignored otherwise. */
  memberIds?: readonly string[];
}

/** Members who receive an entitlement for a sale of the given kind. */
function entitlementRecipients(input: SellInput): string[] {
  if (input.kind === 'subscription') {
    const members = (input.memberIds ?? []).slice(0, MAX_SUBSCRIPTION_MEMBERS);
    return members.length > 0 ? members : [input.playerId];
  }
  if (input.kind === 'holiday_special') {
    return [input.playerId];
  }
  return []; // pay_per_use → cash only, no entitlement created
}

/**
 * Build the local records + Sync_Actions for a completed sale (Req 12.3): a
 * `payment` leg always, plus one `entitlement` create per recipient for a
 * subscription / Holiday Special. Every action carries a device-unique
 * `client_id` and `created_at == capture time` (Property 2).
 */
export function buildSellActions(input: SellInput, ctx: CaptureContext): CaptureResult {
  const day = dayOf(ctx.now);
  const payClientId = ctx.newId();

  const result: CaptureResult = {
    records: [
      {
        store: 'payments',
        record: {
          local_id: payClientId,
          client_id: payClientId,
          player_id: input.playerId,
          day,
          amount_cents: input.priceCents,
          method: 'cash',
          product_id: input.productId,
          sell_kind: input.kind,
        },
      },
    ],
    actions: [
      {
        action: {
          client_id: payClientId,
          entity: 'payment',
          created_at: ctx.now,
          payload: {
            player_id: input.playerId,
            product_id: input.productId,
            amount_cents: input.priceCents,
            method: 'cash',
            paid_at: ctx.now,
          },
        },
      },
    ],
  };

  if (input.productId) {
    for (const memberId of entitlementRecipients(input)) {
      const entClientId = ctx.newId();
      result.records.push({
        store: 'entitlements',
        record: {
          local_id: entClientId,
          client_id: entClientId,
          player_id: memberId,
          day,
          product_id: input.productId,
          kind: 'create',
        },
      });
      result.actions.push({
        action: {
          client_id: entClientId,
          entity: 'entitlement',
          created_at: ctx.now,
          payload: {
            player_id: memberId,
            product_id: input.productId,
          },
        },
      });
    }
  }

  return result;
}
