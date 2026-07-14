import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import { buildAlertRows } from './alerts';
import type { Alert } from './types';

/** The four server alert types (Req 16.2) plus arbitrary strings to prove no filtering. */
const KNOWN_TYPES = [
  'no-session-in-7-days',
  'entitlement-expiring',
  'subscription-payment-due',
  'unsynced-device-older-than-5-days',
] as const;

const alertArb: fc.Arbitrary<Alert> = fc.record({
  type: fc.oneof(fc.constantFrom(...KNOWN_TYPES), fc.string({ maxLength: 20 })),
  subject_id: fc.string({ maxLength: 24 }),
  detail: fc.string({ maxLength: 40 }),
});

describe('Alerts render fidelity (Property 20)', () => {
  // Feature: revenue-pwa, Property 20: Alerts render exactly as received without recomputation.
  // For any alert list returned by GET /alerts, the rendered alerts are a one-to-one,
  // order-preserving reflection of the received alerts (each with its type and subject),
  // with no client-side rule recomputation or filtering.
  // Validates: Requirements 16.2, 16.4
  it('Property 20: render mapping is one-to-one and order-preserving', () => {
    fc.assert(
      fc.property(fc.array(alertArb, { maxLength: 50 }), (alerts) => {
        const rows = buildAlertRows(alerts);

        // One-to-one: exactly as many rows as received alerts (nothing added/dropped).
        expect(rows).toHaveLength(alerts.length);

        // Order-preserving + faithful projection at every index (no recompute/reorder).
        for (let i = 0; i < alerts.length; i += 1) {
          expect(rows[i].type).toBe(alerts[i].type);
          expect(rows[i].subjectId).toBe(alerts[i].subject_id);
          expect(rows[i].detail).toBe(alerts[i].detail);
        }
        return true;
      }),
      { numRuns: 100 },
    );
  });

  it('preserves duplicate alerts without de-duplication (no filtering)', () => {
    const dup: Alert = { type: 'entitlement-expiring', subject_id: 'p1', detail: 'expires soon' };
    const rows = buildAlertRows([dup, dup, dup]);
    expect(rows).toHaveLength(3);
  });

  it('maps an empty list to an empty render (no injected rows)', () => {
    expect(buildAlertRows([])).toEqual([]);
  });
});
