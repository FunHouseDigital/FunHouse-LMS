/**
 * Today summary domain logic (Req 11). See design.md "Today".
 *
 * Pure functions computed entirely from Local_Store records so the Today screen
 * renders offline (Req 11.5):
 *  - running cash total for the current day (Req 11.1),
 *  - count of sessions logged today (Req 11.2),
 *  - progress against the R550 monthly pace target (Req 11.3).
 * The unsynced count (Req 11.4) comes from the sync state, not from here.
 */
import type { LocalRecord } from '../store/localStore';

/** The R550 monthly pace target, in Rand (Req 11.3). */
export const MONTHLY_PACE_TARGET_RAND = 550;

/** The computed Today figures (Req 11.1, 11.2). */
export interface TodayTotals {
  /** Running cash total captured today, in integer cents (Req 11.1). */
  cashTotalCents: number;
  /** Count of sessions logged today (Req 11.2). */
  sessionCount: number;
}

function amountCentsOf(record: LocalRecord): number {
  const value = record.amount_cents;
  return typeof value === 'number' && Number.isFinite(value) ? value : 0;
}

/** True when a payment record was captured on `day` and is a cash payment. */
function isCashPaymentOnDay(record: LocalRecord, day: string): boolean {
  return record.day === day && (record.method === undefined || record.method === 'cash');
}

/**
 * Compute the Today totals from local payment + session records (Req 11.1, 11.2,
 * 11.5 — Property 17). The cash total sums the current day's cash payments; the
 * session count counts the current day's sessions.
 */
export function computeTodayTotals(
  payments: LocalRecord[],
  sessions: LocalRecord[],
  day: string,
): TodayTotals {
  const cashTotalCents = payments
    .filter((p) => isCashPaymentOnDay(p, day))
    .reduce((total, p) => total + amountCentsOf(p), 0);
  const sessionCount = sessions.filter((s) => s.day === day).length;
  return { cashTotalCents, sessionCount };
}

/** Format integer cents as a Rand string (e.g. `3050` → `"R30.50"`). */
export function formatRand(cents: number): string {
  return `R${(cents / 100).toFixed(2)}`;
}

/** The current day's cash total as a fraction of the monthly pace target (Req 11.3). */
export function paceFraction(cashTotalCents: number): number {
  const targetCents = MONTHLY_PACE_TARGET_RAND * 100;
  if (targetCents <= 0) return 0;
  return cashTotalCents / targetCents;
}
