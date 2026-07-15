/**
 * Metrics capture builder — `Metrics_Module` (Req 15, Dependency D1 resolved).
 * See design.md "Metrics — Metrics_Module" and "Dependency D1".
 *
 * Pure builder: given a metrics row (a selected `playerId` + display name +
 * optional wpm/accuracy), it validates that each numeric field is a non-negative
 * number (Property 19) and produces a `student_metrics` record + action per
 * provided metric. `student_metrics` is now a live `/sync` entity (Container API
 * PR #3): actions enqueue with the normal `unsynced` status and are included in
 * the next flush batch, keyed on `player_id` and reconciled like the other
 * natural-key entities. The display name stays personal data — encrypted at
 * rest locally and never sent on the wire; only `player_id` identifies the
 * student in the payload.
 */
import type { StudentMetricsPayload } from '../types';
import type { CaptureAction, CaptureContext, CaptureResult } from './types';
import { dayOf } from './types';

/** The metric types the pipeline understands (Req 15, D1). */
export type MetricType = StudentMetricsPayload['metric_type'];

/** A single metrics-grid row. Numeric fields may arrive as raw strings. */
export interface MetricsRowInput {
  studentName?: string;
  playerId?: string;
  wpm?: number | string;
  accuracy?: number | string;
}

/**
 * Parse a value to a non-negative number, or `null` when it is not non-negative
 * numeric (Req 15.2 — Property 19). Empty/blank strings, non-numeric text,
 * negatives, `NaN`, and infinities are all rejected.
 */
export function parseNonNegative(value: unknown): number | null {
  if (typeof value === 'number') {
    return Number.isFinite(value) && value >= 0 ? value : null;
  }
  if (typeof value === 'string') {
    const trimmed = value.trim();
    if (trimmed === '') return null;
    const n = Number(trimmed);
    return Number.isFinite(n) && n >= 0 ? n : null;
  }
  return null;
}

/** True iff `value` parses to a non-negative number (Req 15.2 — Property 19). */
export function isNonNegativeNumeric(value: unknown): boolean {
  return parseNonNegative(value) !== null;
}

/** True when a metrics row can be saved: at least one valid metric present. */
export function canSaveMetricsRow(row: MetricsRowInput | null | undefined): boolean {
  if (!row) return false;
  return isNonNegativeNumeric(row.wpm) || isNonNegativeNumeric(row.accuracy);
}

function metricAction(
  ctx: CaptureContext,
  row: MetricsRowInput,
  metricType: MetricType,
  value: number,
): { record: CaptureResult['records'][number]; action: CaptureAction } {
  const clientId = ctx.newId();
  const payload: StudentMetricsPayload = {
    metric_type: metricType,
    // `measured_at` (device capture time) stabilises the server natural key
    // (`player_id`/`metric_type`/`measured_at`) so retries stay idempotent.
    measured_at: ctx.now,
    value,
    ...(row.playerId ? { player_id: row.playerId } : {}),
  };
  return {
    record: {
      store: 'student_metrics',
      record: {
        local_id: clientId,
        client_id: clientId,
        ...(row.playerId ? { player_id: row.playerId } : {}),
        day: dayOf(ctx.now),
        metric_type: metricType,
        value,
      },
      // The student name is personal data → encrypted at rest (Req 17.1); it is
      // never placed in the sync payload.
      ...(row.studentName ? { personal: { player_name: row.studentName } } : {}),
    },
    // D1 resolved: a live synced action. Defaults to the normal `unsynced`
    // status so the Sync_Engine includes it in the next batch and reconciles it
    // (applied/skipped/rejected) like session/attendance/payment. When the
    // referenced player was registered offline, its local id is rewritten to the
    // server id via the same D2 local-id resolution used by those entities.
    action: {
      action: {
        client_id: clientId,
        entity: 'student_metrics',
        created_at: ctx.now,
        payload: payload as unknown as Record<string, unknown>,
      },
    },
  };
}

/**
 * Build the local records + Sync_Actions for a saved metrics row (Req 15.3):
 * one `student_metrics` leg per valid metric (`typing_wpm` and/or
 * `typing_accuracy`). Invalid/absent numeric fields are skipped.
 */
export function buildMetricsActions(row: MetricsRowInput, ctx: CaptureContext): CaptureResult {
  const result: CaptureResult = { records: [], actions: [] };

  const wpm = parseNonNegative(row.wpm);
  if (wpm !== null) {
    const built = metricAction(ctx, row, 'typing_wpm', wpm);
    result.records.push(built.record);
    result.actions.push(built.action);
  }

  const accuracy = parseNonNegative(row.accuracy);
  if (accuracy !== null) {
    const built = metricAction(ctx, row, 'typing_accuracy', accuracy);
    result.records.push(built.record);
    result.actions.push(built.action);
  }

  return result;
}
