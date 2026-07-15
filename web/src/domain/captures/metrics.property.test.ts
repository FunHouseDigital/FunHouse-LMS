import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import { buildMetricsActions, isNonNegativeNumeric, parseNonNegative } from './metrics';
import type { CaptureContext } from './types';

function ctx(): CaptureContext {
  let n = 0;
  return { now: '2024-06-15T10:00:00.000Z', newId: () => `id-${n++}` };
}

describe('Metrics numeric input (Property 19)', () => {
  // Feature: revenue-pwa, Property 19: Metrics entry accepts only non-negative numeric
  // input. For any input value for words-per-minute or accuracy, the value is accepted if
  // and only if it parses to a non-negative number.
  // Validates: Requirements 15.2
  it('Property 19: value accepted iff it parses to a non-negative number', () => {
    fc.assert(
      fc.property(
        fc.oneof(
          fc.integer({ min: -1000, max: 1000 }),
          fc.double({ min: -1000, max: 1000, noNaN: false }),
          fc.string({ maxLength: 8 }),
          fc.constant(''),
          fc.constant('  '),
          fc.constant('42'),
          fc.constant('-3'),
          fc.constant('3.5'),
          fc.constant('abc'),
          fc.constant(Number.NaN),
          fc.constant(Number.POSITIVE_INFINITY),
        ),
        (value) => {
          const accepted = isNonNegativeNumeric(value);
          // Independent oracle: a finite non-negative number, or a string that
          // trims to a finite non-negative number.
          let expected: boolean;
          if (typeof value === 'number') {
            expected = Number.isFinite(value) && value >= 0;
          } else if (typeof value === 'string') {
            const t = value.trim();
            if (t === '') expected = false;
            else {
              const n = Number(t);
              expected = Number.isFinite(n) && n >= 0;
            }
          } else {
            expected = false;
          }
          expect(accepted).toBe(expected);
          if (accepted) expect(parseNonNegative(value)).toBeGreaterThanOrEqual(0);
          return true;
        },
      ),
      { numRuns: 200 },
    );
  });

  it('Property 19: builder emits a metric action only for accepted values', () => {
    fc.assert(
      fc.property(
        fc.oneof(fc.integer({ min: -50, max: 200 }), fc.constant(''), fc.string({ maxLength: 4 })),
        fc.oneof(fc.integer({ min: -50, max: 200 }), fc.constant(''), fc.string({ maxLength: 4 })),
        (wpm, accuracy) => {
          const { actions } = buildMetricsActions(
            { playerId: 'player-1', studentName: 'S', wpm, accuracy },
            ctx(),
          );
          const expectedCount =
            (isNonNegativeNumeric(wpm) ? 1 : 0) + (isNonNegativeNumeric(accuracy) ? 1 : 0);
          expect(actions).toHaveLength(expectedCount);
          // D1 resolved: metrics now enqueue as normal live synced actions
          // (no `blocked` sub-status) keyed on player_id, so the Sync_Engine
          // includes them in the next flush batch.
          for (const a of actions) {
            expect(a.action.entity).toBe('student_metrics');
            expect(a.status).toBeUndefined(); // defaults to `unsynced` at enqueue
            const payload = a.action.payload as Record<string, unknown>;
            expect(payload.player_id).toBe('player-1');
            expect(payload.measured_at).toBeDefined();
            // The personal display name is never sent on the wire.
            expect(payload.player_name).toBeUndefined();
          }
          return true;
        },
      ),
      { numRuns: 100 },
    );
  });
});
