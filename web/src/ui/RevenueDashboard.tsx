/**
 * Revenue Dashboard screen (Req 13). See design.md "Revenue Dashboard" and
 * "Dependency D3 (revenue filters)".
 *
 * Renders the three revenue streams (pay-per-use, subscription, school-contract)
 * from `GET /revenue/summary`, converting integer cents → Rand. The
 * school-contract stream is always shown, even at R0 (Req 13.2). A period
 * selector (daily/weekly/monthly, Req 13.3) and a location selector (Req 13.4)
 * drive the query params, and each `(period, location)` result is cached under
 * its own `cached_reads` key (Req 13.5). While offline, the last cached summary
 * is rendered with a "cached" indicator (Req 13.5).
 *
 * Dependency D3: `GET /revenue/summary` is assumed to accept period/location
 * query params. To detect a deployed endpoint that ignores them, the dashboard
 * probes once by requesting two *distinct* periods; if the totals come back
 * identical the endpoint is treated as param-ignoring and the dashboard falls
 * back to the default scoped summary with the filters disabled. The same
 * fallback applies when a param'd request errors. No client-side re-aggregation
 * of revenue is ever performed.
 */
import { useEffect, useRef, useState } from 'react';
import { useAuth } from '../state/authState';
import { getCachedRead, writeCachedRead } from '../store/localStore';
import {
  DEFAULT_REVENUE_CACHE_KEY,
  REVENUE_PERIODS,
  buildRevenueRows,
  revenueCacheKey,
  summariesEqual,
  type RevenuePeriod,
} from '../domain/revenue';
import type { RevenueSummary } from '../domain/types';

type LoadState = 'loading' | 'ready' | 'empty';

function isOnline(): boolean {
  // Default to online when the flag is unavailable (e.g. non-browser env).
  return typeof navigator === 'undefined' || navigator.onLine !== false;
}

export function RevenueDashboard() {
  const { client } = useAuth();

  const [period, setPeriod] = useState<RevenuePeriod>('monthly');
  const [location, setLocation] = useState('');
  const [summary, setSummary] = useState<RevenueSummary | null>(null);
  const [cached, setCached] = useState(false);
  const [paramsIgnored, setParamsIgnored] = useState(false);
  const [state, setState] = useState<LoadState>('loading');

  // The D3 probe runs at most once per mount.
  const probedRef = useRef(false);

  useEffect(() => {
    let alive = true;

    async function readFromCache(preferDefault: boolean): Promise<boolean> {
      const primaryKey = preferDefault
        ? DEFAULT_REVENUE_CACHE_KEY
        : revenueCacheKey(period, location);
      const fallbackKey = preferDefault
        ? revenueCacheKey(period, location)
        : DEFAULT_REVENUE_CACHE_KEY;
      const hit =
        (await getCachedRead<RevenueSummary>(primaryKey)) ??
        (await getCachedRead<RevenueSummary>(fallbackKey));
      if (!alive) return true;
      if (hit) {
        setSummary(hit.data);
        setCached(true);
        setState('ready');
        return true;
      }
      return false;
    }

    async function probeParamSupport(): Promise<boolean> {
      // Two distinct periods; identical totals ⇒ the endpoint ignores params.
      const daily = await client.getRevenueSummary({ period: 'daily' });
      const monthly = await client.getRevenueSummary({ period: 'monthly' });
      return summariesEqual(daily, monthly);
    }

    void (async () => {
      // Offline: render the last cached summary and flag it as cached (Req 13.5).
      if (!isOnline()) {
        const found = await readFromCache(paramsIgnored);
        if (alive && !found) setState('empty');
        return;
      }

      try {
        // Determine param support once (Dependency D3).
        let ignored = paramsIgnored;
        if (!probedRef.current) {
          ignored = await probeParamSupport();
          probedRef.current = true;
          if (alive) setParamsIgnored(ignored);
        }

        const data = ignored
          ? await client.getRevenueSummary({})
          : await client.getRevenueSummary({
              period,
              location: location.trim() === '' ? undefined : location.trim(),
            });

        const key = ignored ? DEFAULT_REVENUE_CACHE_KEY : revenueCacheKey(period, location);
        await writeCachedRead(key, data);

        if (!alive) return;
        setSummary(data);
        setCached(false);
        setState('ready');
      } catch {
        // Fetch failed → D3 fallback to the default scoped summary + disable
        // filters, showing the last cached data with the cached indicator.
        if (alive) setParamsIgnored(true);
        const found = await readFromCache(true);
        if (alive && !found) setState('empty');
      }
    })();

    return () => {
      alive = false;
    };
    // paramsIgnored is intentionally in the deps: flipping it re-runs the load.
  }, [client, period, location, paramsIgnored]);

  const rows = summary ? buildRevenueRows(summary) : [];
  const filtersDisabled = paramsIgnored;

  return (
    <section aria-label="Revenue Dashboard" data-screen-body="revenue">
      <h1>Revenue Dashboard</h1>

      {cached && (
        <p role="status" data-field="cached-indicator">
          Showing cached data
        </p>
      )}

      {filtersDisabled && (
        <p role="status" data-field="filters-disabled">
          Filters unavailable — showing the default summary
        </p>
      )}

      <div>
        <label>
          Period
          <select
            aria-label="Period"
            value={period}
            disabled={filtersDisabled}
            onChange={(e) => setPeriod(e.target.value as RevenuePeriod)}
          >
            {REVENUE_PERIODS.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </label>

        <label>
          Location
          <input
            type="text"
            aria-label="Location"
            placeholder="All locations"
            value={location}
            disabled={filtersDisabled}
            onChange={(e) => setLocation(e.target.value)}
          />
        </label>
      </div>

      {state === 'loading' && <p role="status">Loading…</p>}
      {state === 'empty' && (
        <p role="status" data-field="empty">
          No revenue data available.
        </p>
      )}

      {state === 'ready' && (
        <ul aria-label="Revenue streams">
          {rows.map((row) => (
            <li key={row.key} data-stream={row.key}>
              <span data-field="label">{row.label}</span>
              <span data-field="amount">{row.rand}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export default RevenueDashboard;
