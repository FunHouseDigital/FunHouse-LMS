/**
 * Log Session capture builder — `Session_Logger` (Req 7, 8.1). See design.md
 * "Log Session — Session_Logger".
 *
 * Pure builder: given a validated session input and a {@link CaptureContext},
 * it produces a `session` record + action (`session_type: "lounge"`) plus
 * exactly one payment leg — either a `payment` (cash) or an `entitlement` draw
 * (`{entitlement_id, amount: duration_minutes}`). The entitlement-draw oversell
 * gate lives in the Entitlement_Calculator and is enforced by the screen before
 * this builder is invoked (Req 8.4, 8.6); this builder assumes a permitted draw.
 */
import type { CaptureContext, CaptureResult } from './types';
import { dayOf } from './types';

/** Console options offered by the Log Session screen (Req 7.3). */
export type ConsoleOption = 'PS5' | 'PS4';

/** Duration presets offered by the Log Session screen (Req 7.4). */
export const DURATION_PRESETS: readonly number[] = [20, 60, 120];

/** Cash payment leg for a session (Req 7.5). */
export interface CashPayment {
  method: 'cash';
  /** Integer cents captured for the session. */
  amountCents: number;
}

/** Entitlement-draw payment leg for a session (Req 7.5, 8). */
export interface EntitlementPayment {
  method: 'entitlement';
  entitlementId: string;
}

export type SessionPayment = CashPayment | EntitlementPayment;

/** Validated Log Session input (the screen enforces the confirm gate, Req 7.6). */
export interface SessionInput {
  playerId: string;
  console: ConsoleOption;
  /** Duration in minutes (a preset or a custom positive value, Req 7.4). */
  durationMinutes: number;
  payment: SessionPayment;
}

/**
 * True when the confirm control may be enabled (Req 7.6): a player, a positive
 * duration, and a payment method have all been chosen.
 */
export function canConfirmSession(input: Partial<SessionInput> | null | undefined): boolean {
  if (!input) return false;
  const hasPlayer = typeof input.playerId === 'string' && input.playerId.length > 0;
  const hasDuration = typeof input.durationMinutes === 'number' && input.durationMinutes > 0;
  const hasPayment = !!input.payment && (input.payment.method === 'cash' || input.payment.method === 'entitlement');
  return hasPlayer && hasDuration && hasPayment;
}

/**
 * Build the local records + Sync_Actions for a confirmed lounge session (Req
 * 7.7). Always emits a `session` leg; emits exactly one payment leg matching the
 * chosen method. Every action gets a device-unique `client_id` and `created_at`
 * equal to the capture time (Property 2).
 */
export function buildSessionActions(input: SessionInput, ctx: CaptureContext): CaptureResult {
  const startedAt = ctx.now;
  const endedAt = new Date(new Date(ctx.now).getTime() + input.durationMinutes * 60_000).toISOString();
  const day = dayOf(ctx.now);

  const sessionClientId = ctx.newId();
  const sessionLocalId = sessionClientId;

  const result: CaptureResult = {
    records: [
      {
        store: 'sessions',
        record: {
          local_id: sessionLocalId,
          client_id: sessionClientId,
          player_id: input.playerId,
          day,
          session_type: 'lounge',
          console: input.console,
          duration_minutes: input.durationMinutes,
          started_at: startedAt,
          ended_at: endedAt,
        },
      },
    ],
    actions: [
      {
        action: {
          client_id: sessionClientId,
          entity: 'session',
          created_at: ctx.now,
          payload: {
            player_id: input.playerId,
            session_type: 'lounge',
            started_at: startedAt,
            ended_at: endedAt,
            duration_minutes: input.durationMinutes,
            reference: input.console,
          },
        },
      },
    ],
  };

  if (input.payment.method === 'cash') {
    const payClientId = ctx.newId();
    result.records.push({
      store: 'payments',
      record: {
        local_id: payClientId,
        client_id: payClientId,
        player_id: input.playerId,
        day,
        amount_cents: input.payment.amountCents,
        method: 'cash',
      },
    });
    result.actions.push({
      action: {
        client_id: payClientId,
        entity: 'payment',
        created_at: ctx.now,
        payload: {
          player_id: input.playerId,
          amount_cents: input.payment.amountCents,
          method: 'cash',
          paid_at: ctx.now,
        },
      },
    });
  } else {
    const drawClientId = ctx.newId();
    result.records.push({
      store: 'entitlements',
      record: {
        local_id: drawClientId,
        client_id: drawClientId,
        player_id: input.playerId,
        day,
        entitlement_id: input.payment.entitlementId,
        amount: input.durationMinutes,
        kind: 'draw',
      },
    });
    result.actions.push({
      action: {
        client_id: drawClientId,
        entity: 'entitlement',
        created_at: ctx.now,
        payload: {
          entitlement_id: input.payment.entitlementId,
          amount: input.durationMinutes,
        },
      },
    });
  }

  return result;
}
