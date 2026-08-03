/**
 * Alerts view domain logic (Req 16). See design.md "Alerts".
 *
 * The single pure function {@link buildAlertRows} is the render mapping the
 * {@link Alerts} screen uses: it turns the `GET /alerts` list into display rows.
 * The mapping is deliberately **one-to-one and order-preserving** and performs
 * **no filtering, sorting, or rule recomputation** — alerts are shown exactly as
 * the Container_API returned them (Req 16.4, Property 20). Alert rules are
 * computed server-side; the client never re-derives them.
 * The cache key helper requires an authenticated account scope so offline alerts
 * cannot be replayed across accounts on a shared device.
 */
import type { Alert } from './types';

/** Account-owned `cached_reads` key for the server-computed alerts list. */
export function alertsCacheKey(scope: string): string {
  return `alerts:${scope}`;
}

/** A single alert display row (Req 16.1). */
export interface AlertRow {
  /** The alert type as returned by the server (e.g. `no-session-in-7-days`). */
  type: string;
  /** The subject identifier the alert concerns. */
  subjectId: string;
  /** The human-readable detail for the alert. */
  detail: string;
}

/**
 * Map received alerts to display rows (Req 16.1, 16.2 — Property 20).
 *
 * This is a straight, index-preserving projection: the i-th output row reflects
 * the i-th input alert's `type`, `subject_id`, and `detail`. No alert is added,
 * dropped, reordered, or recomputed.
 */
export function buildAlertRows(alerts: Alert[]): AlertRow[] {
  return alerts.map((alert) => ({
    type: alert.type,
    subjectId: alert.subject_id,
    detail: alert.detail,
  }));
}
