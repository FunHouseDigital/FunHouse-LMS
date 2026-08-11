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
import { useReferenceData } from '../state/referenceDataState';
import { getCachedRead, writeCachedRead } from '../store/localStore';
import {
  REVENUE_PERIODS,
  buildRevenueRows,
  defaultRevenueCacheKey,
  revenueCacheKey,
  summariesEqual,
  type RevenuePeriod,
} from '../domain/revenue';
import type { RevenueSummary } from '../domain/types';

type LoadState = 'loading' | 'ready' | 'empty';

interface RevenueView {
  scope: string | null;
  summary: RevenueSummary | null;
  cached: boolean;
  state: LoadState;
}

function isOnline(): boolean {
  // Default to online when the flag is unavailable (e.g. non-browser env).
  return typeof navigator === 'undefined' || navigator.onLine !== false;
}

export function RevenueDashboard() {
  const { client } = useAuth();
  const { cacheScope } = useReferenceData();
  const activeScopeRef = useRef(cacheScope);
  // Render updates this before passive-effect cleanup, closing the account-switch
  // window between sequential D3 requests.
  activeScopeRef.current = cacheScope;

  const [period, setPeriod] = useState<RevenuePeriod>('monthly');
  const [location, setLocation] = useState('');
  const [view, setView] = useState<RevenueView>({
    scope: null,
    summary: null,
    cached: false,
    state: 'loading',
  });
  const [paramSupportByScope, setParamSupportByScope] = useState(
    () => new Map<string, boolean>(),
  );
  const storedParamSupport = cacheScope ? paramSupportByScope.get(cacheScope) : undefined;
  const paramsIgnored = storedParamSupport ?? false;
  const paramSupportKnown = storedParamSupport !== undefined;

  useEffect(() => {
    if (!cacheScope) {
      setView({ scope: null, summary: null, cached: false, state: 'empty' });
      return undefined;
    }

    let alive = true;
    const requestScope = cacheScope;
    const defaultKey = defaultRevenueCacheKey(requestScope);
    const selectedKey = revenueCacheKey(requestScope, period, location);
    const isCurrent = () => alive && activeScopeRef.current === requestScope;
    const rememberParamSupport = (ignored: boolean) => {
      setParamSupportByScope((current) => {
        const next = new Map(current);
        next.set(requestScope, ignored);
        return next;
      });
    };

    function show(data: RevenueSummary, fromCache: boolean): void {
      if (!isCurrent()) return;
      setView({
        scope: requestScope,
        summary: data,
        cached: fromCache,
        state: 'ready',
      });
    }

    async function readFromCache(preferDefault: boolean): Promise<boolean> {
      const primaryKey = preferDefault ? defaultKey : selectedKey;
      const fallbackKey = preferDefault ? selectedKey : defaultKey;
      const hit =
        (await getCachedRead<RevenueSummary>(primaryKey)) ??
        (await getCachedRead<RevenueSummary>(fallbackKey));
      if (!isCurrent()) return true;
      if (hit) {
        show(hit.data, true);
        return true;
      }
      return false;
    }

    async function probeParamSupport(): Promise<boolean | null> {
      // Check account ownership between every request because the shared client
      // resolves its bearer token when each individual request begins.
      if (!isCurrent()) return null;
      const daily = await client.getRevenueSummary({ period: 'daily' });
      if (!isCurrent()) return null;
      const monthly = await client.getRevenueSummary({ period: 'monthly' });
      if (!isCurrent()) return null;
      return summariesEqual(daily, monthly);
    }

    void (async () => {
      // Offline: render only this account's cached summary (Req 13.5).
      if (!isOnline()) {
        const found = await readFromCache(paramsIgnored);
        if (isCurrent() && !found) {
          setView({ scope: requestScope, summary: null, cached: false, state: 'empty' });
        }
        return;
      }

      try {
        // Determine param support once per account scope (Dependency D3).
        let ignored = paramsIgnored;
        if (!paramSupportKnown) {
          const probeResult = await probeParamSupport();
          if (probeResult === null) return;
          ignored = probeResult;
          rememberParamSupport(ignored);
        }

        if (!isCurrent()) return;
        const data = ignored
          ? await client.getRevenueSummary({})
          : await client.getRevenueSummary({
              period,
              location: location.trim() === '' ? undefined : location.trim(),
            });
        if (!isCurrent()) return;

        const key = ignored ? defaultKey : selectedKey;
        await writeCachedRead(key, data);
        show(data, false);
      } catch {
        // Fetch failed → D3 fallback within this account's namespace only.
        if (isCurrent()) rememberParamSupport(true);
        const found = await readFromCache(true);
        if (isCurrent() && !found) {
          setView({ scope: requestScope, summary: null, cached: false, state: 'empty' });
        }
      }
    })();

    return () => {
      alive = false;
    };
  }, [cacheScope, client, location, paramSupportKnown, paramsIgnored, period]);

  // Do not render the prior account's financial state during the
  // render/effect boundary after an account change.
  const visible: RevenueView =
    cacheScope && view.scope === cacheScope
      ? view
      : {
          scope: cacheScope,
          summary: null,
          cached: false,
          state: cacheScope ? 'loading' : 'empty',
        };
  const { summary, cached, state } = visible;
  const rows = summary ? buildRevenueRows(summary) : [];
  const filtersDisabled = paramsIgnored;

  return (
    <section aria-label="Revenue Dashboard" data-screen-body="revenue">
      <h1>Revenue Dashboard</h1>
      <p className="screen-intro">Track revenue across lounge sales, subscriptions, and school contracts.</p>

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

      <div className="filter-bar">
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
