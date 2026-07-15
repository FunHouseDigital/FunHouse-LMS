import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import { computeTodayTotals } from './today';
import type { LocalRecord } from '../store/localStore';

const DAY = '2024-06-15';
const OTHER_DAYS = ['2024-06-14', '2024-06-16', '2025-01-01'];

describe('Today totals (Property 17)', () => {
  // Feature: revenue-pwa, Property 17: Today totals equal sums over the current day's local
  // records. For any set of local payment and session records, the Today cash total equals
  // the sum of the current day's cash payments and the Today session count equals the number
  // of the current day's sessions, computed solely from Local_Store data.
  // Validates: Requirements 11.1, 11.2, 11.5
  it('Property 17: cash total + session count sum only the current day', () => {
    fc.assert(
      fc.property(
        fc.array(
          fc.record({
            day: fc.constantFrom(DAY, ...OTHER_DAYS),
            amount_cents: fc.integer({ min: 0, max: 500_000 }),
            method: fc.constantFrom('cash', 'entitlement', undefined),
          }),
          { maxLength: 40 },
        ),
        fc.array(fc.record({ day: fc.constantFrom(DAY, ...OTHER_DAYS) }), { maxLength: 40 }),
        (rawPayments, rawSessions) => {
          const payments: LocalRecord[] = rawPayments.map((p, i) => ({
            local_id: `pay-${i}`,
            day: p.day,
            amount_cents: p.amount_cents,
            ...(p.method !== undefined ? { method: p.method } : {}),
          }));
          const sessions: LocalRecord[] = rawSessions.map((s, i) => ({ local_id: `ses-${i}`, day: s.day }));

          const totals = computeTodayTotals(payments, sessions, DAY);

          // Expected cash total: today's payments that are cash (method 'cash' or absent).
          const expectedCash = rawPayments
            .filter((p) => p.day === DAY && (p.method === undefined || p.method === 'cash'))
            .reduce((sum, p) => sum + p.amount_cents, 0);
          const expectedSessions = rawSessions.filter((s) => s.day === DAY).length;

          expect(totals.cashTotalCents).toBe(expectedCash);
          expect(totals.sessionCount).toBe(expectedSessions);
          return true;
        },
      ),
      { numRuns: 100 },
    );
  });
});
