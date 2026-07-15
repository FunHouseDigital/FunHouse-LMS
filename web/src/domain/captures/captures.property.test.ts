import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import { buildSessionActions, type SessionInput } from './session';
import { buildRegistrationActions, type RegistrationInput } from './registration';
import { buildSellActions, type SellInput } from './sell';
import { buildAttendanceActions, type AttendanceInput } from './attendance';
import { buildMetricsActions, type MetricsRowInput } from './metrics';
import type { CaptureAction, CaptureContext, CaptureResult } from './types';

const NOW = '2024-06-15T10:00:00.000Z';

/** A device-like context: one monotonic id source shared across the capture sequence. */
function deviceCtx(): CaptureContext {
  let n = 0;
  return { now: NOW, newId: () => `client-${n++}` };
}

/** Assert an action's payload carries the required fields for its entity. */
function assertRequiredPayload(entity: string, payload: Record<string, unknown>): void {
  switch (entity) {
    case 'session':
      expect(payload.session_type).toBeDefined();
      expect(payload.started_at).toBeDefined();
      expect(payload.ended_at).toBeDefined();
      expect(payload.duration_minutes).toBeDefined();
      break;
    case 'payment':
      expect(payload.player_id).toBeDefined();
      expect(payload.amount_cents).toBeDefined();
      expect(payload.method).toBeDefined();
      expect(payload.paid_at).toBeDefined();
      break;
    case 'entitlement':
      // A draw carries entitlement_id+amount; a create carries player_id+product_id.
      if ('amount' in payload) {
        expect(payload.entitlement_id).toBeDefined();
        expect(typeof payload.amount).toBe('number');
      } else {
        expect(payload.player_id).toBeDefined();
        expect(payload.product_id).toBeDefined();
      }
      break;
    case 'player':
      expect(payload.first_name).toBeDefined();
      break;
    case 'consent':
      expect(payload.player_id).toBeDefined();
      expect(payload.consent_type).toBeDefined();
      expect(typeof payload.granted).toBe('boolean');
      expect(payload.granted_at).toBeDefined();
      break;
    case 'attendance':
      expect(payload.session_id).toBeDefined();
      expect(payload.player_id).toBeDefined();
      expect(payload.attendance_date).toBeDefined();
      expect(payload.present).toBe(true);
      break;
    case 'student_metrics':
      // D1 resolved: metrics sync live keyed on the natural key
      // (player_id / metric_type / measured_at).
      expect(payload.player_id).toBeDefined();
      expect(payload.metric_type).toBeDefined();
      expect(typeof payload.value).toBe('number');
      expect(payload.measured_at).toBeDefined();
      break;
    default:
      throw new Error(`unexpected entity ${entity}`);
  }
}

// ---- Capture-descriptor generators (each yields a valid, non-empty capture) ----

const sessionDesc = fc
  .record({
    playerId: fc.uuid(),
    console: fc.constantFrom<'PS5' | 'PS4'>('PS5', 'PS4'),
    durationMinutes: fc.integer({ min: 1, max: 240 }),
    payKind: fc.constantFrom('cash', 'entitlement'),
    amountCents: fc.integer({ min: 0, max: 100_000 }),
    entitlementId: fc.uuid(),
  })
  .map((d) => ({
    kind: 'session' as const,
    build: (ctx: CaptureContext): CaptureResult => {
      const input: SessionInput = {
        playerId: d.playerId,
        console: d.console,
        durationMinutes: d.durationMinutes,
        payment:
          d.payKind === 'cash'
            ? { method: 'cash', amountCents: d.amountCents }
            : { method: 'entitlement', entitlementId: d.entitlementId },
      };
      return buildSessionActions(input, ctx);
    },
  }));

const registrationDesc = fc
  .record({
    name: fc.string({ minLength: 1, maxLength: 10 }).filter((s) => s.trim().length > 0),
    guardianPhone: fc.option(fc.string({ maxLength: 10 }), { nil: undefined }),
    consents: fc.record({
      media: fc.boolean(),
      data_processing: fc.boolean(),
      participation: fc.boolean(),
      communications: fc.boolean(),
    }),
  })
  .map((d) => ({
    kind: 'registration' as const,
    build: (ctx: CaptureContext): CaptureResult => {
      const input: RegistrationInput = { ...d, guardianConfirmed: true };
      return buildRegistrationActions(input, ctx);
    },
  }));

const sellDesc = fc
  .record({
    kind: fc.constantFrom<'pay_per_use' | 'subscription' | 'holiday_special'>(
      'pay_per_use',
      'subscription',
      'holiday_special',
    ),
    playerId: fc.uuid(),
    priceCents: fc.integer({ min: 0, max: 100_000 }),
    memberIds: fc.array(fc.uuid(), { minLength: 1, maxLength: 6 }),
  })
  .map((d) => ({
    kind: 'sell' as const,
    build: (ctx: CaptureContext): CaptureResult => {
      const input: SellInput = {
        kind: d.kind,
        playerId: d.playerId,
        priceCents: d.priceCents,
        productId: d.kind === 'pay_per_use' ? undefined : 'prod-1',
        memberIds: d.memberIds,
      };
      return buildSellActions(input, ctx);
    },
  }));

const attendanceDesc = fc
  .record({
    sessionType: fc.constantFrom<'lesson' | 'kit' | 'esports'>('lesson', 'kit', 'esports'),
    roster: fc.array(fc.record({ playerId: fc.uuid(), present: fc.boolean() }), { maxLength: 8 }),
    reference: fc.option(fc.string({ maxLength: 8 }), { nil: undefined }),
  })
  .map((d) => ({
    kind: 'attendance' as const,
    build: (ctx: CaptureContext): CaptureResult => {
      const input: AttendanceInput = {
        sessionType: d.sessionType,
        reference: d.reference,
        roster: d.roster,
      };
      return buildAttendanceActions(input, ctx);
    },
  }));

const metricsDesc = fc
  .record({
    playerId: fc.uuid(),
    studentName: fc.string({ maxLength: 8 }),
    wpm: fc.integer({ min: 0, max: 200 }),
    accuracy: fc.option(fc.integer({ min: 0, max: 100 }), { nil: undefined }),
  })
  .map((d) => ({
    kind: 'metrics' as const,
    build: (ctx: CaptureContext): CaptureResult => {
      const input: MetricsRowInput = {
        playerId: d.playerId,
        studentName: d.studentName,
        wpm: d.wpm,
        accuracy: d.accuracy,
      };
      return buildMetricsActions(input, ctx);
    },
  }));

const anyDesc = fc.oneof(sessionDesc, registrationDesc, sellDesc, attendanceDesc, metricsDesc);

describe('Capture action identity across all builders (Property 2)', () => {
  // Feature: revenue-pwa, Property 2: Every completed capture creates exactly one action
  // per resulting entity with a unique, device-origin identity. For any sequence of
  // completed captures (session, registration, sell, attendance, metrics), each resulting
  // entity produces exactly one Sync_Action, every action's client_id is unique across all
  // actions created on the device, and every action's created_at equals the device capture
  // time and its payload carries the required fields for its entity.
  // Validates: Requirements 4.1, 4.2, 4.3, 7.7, 10.4, 12.3, 14.4, 15.3
  it('Property 2: one action per entity, unique client_ids, created_at == capture time', () => {
    fc.assert(
      fc.property(fc.array(anyDesc, { minLength: 1, maxLength: 12 }), (descriptors) => {
        const ctx = deviceCtx();
        const allActions: CaptureAction[] = [];
        const allRecordClientIds: string[] = [];

        for (const descriptor of descriptors) {
          const result = descriptor.build(ctx);
          allActions.push(...result.actions);
          for (const r of result.records) {
            allRecordClientIds.push(String(r.record.client_id));
          }
        }

        const clientIds = allActions.map((a) => a.action.client_id);

        // Device-unique client_id across every action created on the device (Req 4.3).
        expect(new Set(clientIds).size).toBe(clientIds.length);

        // Exactly one action per resulting entity == one record per action (1:1 by client_id).
        expect(allRecordClientIds.slice().sort()).toEqual(clientIds.slice().sort());

        for (const { action } of allActions) {
          // created_at is the device capture time (Req 4.2).
          expect(action.created_at).toBe(NOW);
          // Required payload fields present for the entity (Req 7.7, 10.4, 12.3, 14.4, 15.3).
          assertRequiredPayload(action.entity, action.payload as Record<string, unknown>);
        }
        return true;
      }),
      { numRuns: 100 },
    );
  });
});
