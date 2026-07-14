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
import { getCachedRead, writeCachedRead } from '../store/localStore';
import { buildAlertRows, type AlertRow } from '../domain/alerts';
import type { Alert } from '../domain/types';

/** The `cached_reads` key for the alerts list (design.md Local_Store schema). */
const ALERTS_CACHE_KEY = 'alerts';

type LoadState = 'loading' | 'ready' | 'empty';

function isOnline(): boolean {
  return typeof navigator === 'undefined' || navigator.onLine !== false;
}

export function Alerts() {
  const { client } = useAuth();
  const [rows, setRows] = useState<AlertRow[]>([]);
  const [cached, setCached] = useState(false);
  const [state, setState] = useState<LoadState>('loading');

  useEffect(() => {
    let alive = true;

    async function readFromCache(): Promise<boolean> {
      const hit = await getCachedRead<Alert[]>(ALERTS_CACHE_KEY);
      if (!alive) return true;
      if (hit) {
        setRows(buildAlertRows(hit.data));
        setCached(true);
        setState(hit.data.length === 0 ? 'empty' : 'ready');
        return true;
      }
      return false;
    }

    void (async () => {
      // Offline: render the last cached alerts with the cached indicator (Req 16.3).
      if (!isOnline()) {
        const found = await readFromCache();
        if (alive && !found) setState('empty');
        return;
      }

      try {
        const alerts = await client.getAlerts();
        await writeCachedRead(ALERTS_CACHE_KEY, alerts);
        if (!alive) return;
        // Display exactly as received — no recomputation/filtering (Req 16.4).
        setRows(buildAlertRows(alerts));
        setCached(false);
        setState(alerts.length === 0 ? 'empty' : 'ready');
      } catch {
        // Network failure → fall back to the last cached alerts (Req 16.3).
        const found = await readFromCache();
        if (alive && !found) setState('empty');
      }
    })();

    return () => {
      alive = false;
    };
  }, [client]);

  return (
    <section aria-label="Alerts" data-screen-body="alerts">
      <h1>Alerts</h1>

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
