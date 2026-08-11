/**
 * Alerts screen (Req 16). See design.md "Alerts".
 *
 * When online, retrieves alerts from `GET /alerts` and displays each alert's
 * type and subject (`subject_id` + `detail`, Req 16.1). The four server alert
 * types (no-session-in-7-days, entitlement-expiring, subscription-payment-due,
 * unsynced-device-older-than-5-days) are rendered exactly as returned
 * (Req 16.2). While offline, the last cached alerts are rendered with a "cached"
 * indicator (Req 16.3). Alerts are displayed exactly as received — the client
 * performs NO rule recomputation or filtering (Req 16.4); the render mapping is
 * the one-to-one, order-preserving {@link buildAlertRows}.
 */
import { useEffect, useState } from 'react';
import { useAuth } from '../state/authState';
import { useReferenceData } from '../state/referenceDataState';
import { getCachedRead, writeCachedRead } from '../store/localStore';
import { alertsCacheKey, buildAlertRows, type AlertRow } from '../domain/alerts';
import type { Alert } from '../domain/types';

type LoadState = 'loading' | 'ready' | 'empty';

interface AlertsView {
  scope: string | null;
  rows: AlertRow[];
  cached: boolean;
  state: LoadState;
}

function isOnline(): boolean {
  return typeof navigator === 'undefined' || navigator.onLine !== false;
}

export function Alerts() {
  const { client } = useAuth();
  const { cacheScope } = useReferenceData();
  const [view, setView] = useState<AlertsView>({
    scope: null,
    rows: [],
    cached: false,
    state: 'loading',
  });

  useEffect(() => {
    if (!cacheScope) {
      setView({ scope: null, rows: [], cached: false, state: 'empty' });
      return undefined;
    }

    let alive = true;
    const requestScope = cacheScope;
    const cacheKey = alertsCacheKey(requestScope);

    function show(alerts: Alert[], fromCache: boolean): void {
      if (!alive) return;
      setView({
        scope: requestScope,
        rows: buildAlertRows(alerts),
        cached: fromCache,
        state: alerts.length === 0 ? 'empty' : 'ready',
      });
    }

    async function readFromCache(): Promise<boolean> {
      const hit = await getCachedRead<Alert[]>(cacheKey);
      if (!alive) return true;
      if (hit) {
        show(hit.data, true);
        return true;
      }
      return false;
    }

    void (async () => {
      // Offline: render only this account's cached alerts (Req 16.3).
      if (!isOnline()) {
        const found = await readFromCache();
        if (alive && !found) {
          setView({ scope: requestScope, rows: [], cached: false, state: 'empty' });
        }
        return;
      }

      try {
        const alerts = await client.getAlerts();
        // A replaced account may finish this write, but only under the scope
        // captured when its request began. It can never populate the new scope.
        await writeCachedRead(cacheKey, alerts);
        show(alerts, false);
      } catch {
        // Network failure → fall back only to this account's cached alerts.
        const found = await readFromCache();
        if (alive && !found) {
          setView({ scope: requestScope, rows: [], cached: false, state: 'empty' });
        }
      }
    })();

    return () => {
      alive = false;
    };
  }, [cacheScope, client]);

  // Do not render the prior account's state during the render/effect boundary.
  const visible: AlertsView =
    cacheScope && view.scope === cacheScope
      ? view
      : { scope: cacheScope, rows: [], cached: false, state: cacheScope ? 'loading' : 'empty' };
  const { rows, cached, state } = visible;

  return (
    <section aria-label="Alerts" data-screen-body="alerts">
      <h1>Alerts</h1>
      <p className="screen-intro">Operational signals that may need follow-up across your account.</p>

      {cached && (
        <p role="status" data-field="cached-indicator">
          Showing cached data
        </p>
      )}

      {state === 'loading' && <p role="status">Loading…</p>}
      {state === 'empty' && (
        <p role="status" data-field="empty">
          No alerts.
        </p>
      )}

      {state === 'ready' && (
        <ul aria-label="Operational alerts">
          {rows.map((row, index) => (
            <li key={`${row.type}:${row.subjectId}:${index}`} data-alert-type={row.type}>
              <span data-field="type">{row.type}</span>
              <span data-field="subject-id">{row.subjectId}</span>
              <span data-field="detail">{row.detail}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export default Alerts;
