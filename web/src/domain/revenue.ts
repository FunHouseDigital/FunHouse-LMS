/**
 * Revenue Dashboard domain logic (Req 13). See design.md "Revenue Dashboard"
 * and "Dependency D3 (revenue filters)".
 *
 * Pure functions consumed by {@link RevenueDashboard}:
 *  - {@link buildRevenueRows} shapes a `GET /revenue/summary` response into the
 *    three display streams (pay-per-use, subscription, school-contract),
 *    converting integer cents → Rand. The school-contract stream is ALWAYS
 *    present, even at R0 (Req 13.2).
 *  - {@link revenueCacheKey} names the per-`(period, location)` `cached_reads`
 *    entry so each selection is cached under its own key (Req 13.3, 13.4, 13.5).
 *  - {@link summariesEqual} underpins the D3 "endpoint appears to ignore params"
 *    probe: if two requests with *distinct* params return identical totals, the
 *    server is treated as ignoring the params and the dashboard falls back to
 *    the default scoped summary with the filters disabled (Dependency D3).
 *
 * No client-side re-aggregation of revenue is performed here or anywhere in the
 * dashboard — the three streams are rendered exactly as returned by the API.
 */
import type { RevenueSummary } from './types';

/** The revenue reporting periods offered by the period selector (Req 13.3). */
export type RevenuePeriod = 'daily' | 'weekly' | 'monthly';

/** Periods in selector order. */
export const REVENUE_PERIODS: readonly RevenuePeriod[] = ['daily', 'weekly', 'monthly'];

/** Stable key for the "all locations" selection (blank location input). */
export const ALL_LOCATIONS = 'all';

/** Build the account-owned key for the default summary used by the D3 fallback. */
export function defaultRevenueCacheKey(scope: string): string {
  return `revenue:${scope}:summary:default:default`;
}

/** A single revenue stream display row. */
export interface RevenueStreamRow {
  key: 'pay_per_use' | 'subscription' | 'school_contract';
  label: string;
  cents: number;
  rand: string;
}

/**
 * The account-owned `cached_reads` key for a `(period, location)` selection
 * (Req 13.5). A blank location maps to {@link ALL_LOCATIONS} so the key is
 * always stable.
 */
export function revenueCacheKey(
  scope: string,
  period: RevenuePeriod,
  location: string,
): string {
  const loc = location.trim() === '' ? ALL_LOCATIONS : location.trim();
  return `revenue:${scope}:summary:${period}:${loc}`;
}

/** Convert integer cents to a Rand display string (e.g. `3050` → `"R30.50"`). */
export function centsToRand(cents: number): string {
  const value = typeof cents === 'number' && Number.isFinite(cents) ? cents : 0;
  return `R${(value / 100).toFixed(2)}`;
}

/**
 * Build the three revenue stream rows from a summary (Req 13.1). The
 * school-contract stream is always included, even when its value is R0
 * (Req 13.2). No re-aggregation: each stream reflects the API value verbatim.
 */
export function buildRevenueRows(summary: RevenueSummary): RevenueStreamRow[] {
  const payPerUse = summary?.pay_per_use_cents ?? 0;
  const subscription = summary?.subscription_cents ?? 0;
  const schoolContract = summary?.school_contracts_cents ?? 0;
  return [
    { key: 'pay_per_use', label: 'Pay-per-use', cents: payPerUse, rand: centsToRand(payPerUse) },
    {
      key: 'subscription',
      label: 'Subscription',
      cents: subscription,
      rand: centsToRand(subscription),
    },
    // Always present even at R0 (Req 13.2).
    {
      key: 'school_contract',
      label: 'School contract',
      cents: schoolContract,
      rand: centsToRand(schoolContract),
    },
  ];
}

/** True iff two summaries carry identical stream totals. */
export function summariesEqual(a: RevenueSummary, b: RevenueSummary): boolean {
  return (
    (a?.pay_per_use_cents ?? 0) === (b?.pay_per_use_cents ?? 0) &&
    (a?.subscription_cents ?? 0) === (b?.subscription_cents ?? 0) &&
    (a?.school_contracts_cents ?? 0) === (b?.school_contracts_cents ?? 0)
  );
}
