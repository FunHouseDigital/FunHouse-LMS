import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import {
  MAX_SUBSCRIPTION_MEMBERS,
  addSubscriptionMember,
  buildSellActions,
  type SellInput,
} from './sell';
import type { CaptureContext } from './types';

function ctx(): CaptureContext {
  let n = 0;
  return { now: '2024-06-15T10:00:00.000Z', newId: () => `id-${n++}` };
}

describe('Subscription membership cap (Property 18)', () => {
  // Feature: revenue-pwa, Property 18: Subscription membership never exceeds four. For any
  // sequence of add-member actions on a new subscription, the resulting member set contains
  // at most four members, and any attempt to add a fifth is rejected.
  // Validates: Requirements 12.4
  it('Property 18: any sequence of adds yields at most four members', () => {
    fc.assert(
      fc.property(fc.array(fc.string({ maxLength: 8 }), { maxLength: 40 }), (ids) => {
        let members: string[] = [];
        for (const id of ids) {
          const before = members.length;
          members = addSubscriptionMember(members, id);
          // Never exceeds the cap.
          expect(members.length).toBeLessThanOrEqual(MAX_SUBSCRIPTION_MEMBERS);
          // A fifth distinct add is rejected (no growth once full).
          if (before >= MAX_SUBSCRIPTION_MEMBERS) {
            expect(members.length).toBe(MAX_SUBSCRIPTION_MEMBERS);
          }
        }
        // No duplicates.
        expect(new Set(members).size).toBe(members.length);
        return true;
      }),
      { numRuns: 100 },
    );
  });

  it('Property 18: the built sale never creates more than four entitlements', () => {
    fc.assert(
      fc.property(fc.array(fc.uuid(), { minLength: 1, maxLength: 10 }), (memberIds) => {
        const input: SellInput = {
          kind: 'subscription',
          playerId: memberIds[0],
          priceCents: 35_000,
          productId: 'prod-sub',
          memberIds,
        };
        const { actions } = buildSellActions(input, ctx());
        const entitlementActions = actions.filter((a) => a.action.entity === 'entitlement');
        expect(entitlementActions.length).toBeLessThanOrEqual(MAX_SUBSCRIPTION_MEMBERS);
        return true;
      }),
      { numRuns: 100 },
    );
  });
});
