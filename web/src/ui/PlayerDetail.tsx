/**
 * Player detail screen (Req 9.3). See design.md "Players roster + detail".
 *
 * Displays a player's session, payment, and entitlement history, merging the
 * server history (`GET /players/{id}/history`) with locally captured records
 * that have not yet synced (Req 9.3). Failed server reads remain explicitly
 * non-authoritative while saved device activity is still rendered (Property 14).
 */
import { useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useAuth } from '../state/authState';
import { useReferenceData } from '../state/referenceDataState';
import { getActionsByStatus } from '../store/localStore';
import { mergePlayerDetail, type LocalPlayerRecords, type MergedPlayerDetail } from '../domain/roster';
import type { PlayerHistory, StoredSyncAction } from '../domain/types';
import { centsToRand } from '../domain/revenue';

const UNKNOWN_VALUE = 'Unknown';

const EMPTY_HISTORY = (playerId: string): PlayerHistory => ({
  player_id: playerId,
  sessions: [],
  payments: [],
  entitlement_draws: [],
});

/** Gather this player's locally captured, not-yet-synced records from the queue. */
async function localUnsyncedFor(
  playerId: string,
  scope: string | null,
): Promise<LocalPlayerRecords> {
  if (!scope) return {};
  const unsynced: StoredSyncAction[] = await getActionsByStatus('unsynced', scope);
  const forPlayer = unsynced.filter((a) => a.player_id === playerId || (a.payload as Record<string, unknown>)?.player_id === playerId);
  return {
    sessions: forPlayer.filter((a) => a.entity === 'session').map((a) => ({ ...a.payload, __local: true, client_id: a.client_id, created_at: a.created_at })),
    payments: forPlayer.filter((a) => a.entity === 'payment').map((a) => ({ ...a.payload, __local: true, client_id: a.client_id, created_at: a.created_at })),
    entitlement_draws: forPlayer
      .filter((a) => a.entity === 'entitlement' && typeof (a.payload as Record<string, unknown>).amount === 'number')
      .map((a) => ({ ...a.payload, __local: true, client_id: a.client_id, created_at: a.created_at })),
  };
}

type HistoryRecord = Record<string, unknown>;

function valueFrom(record: HistoryRecord, ...keys: string[]): unknown {
  for (const key of keys) {
    const value = record[key];
    if (value !== undefined && value !== null && value !== '') return value;
  }
  return undefined;
}

function formatUnknown(value: unknown): string {
  if (typeof value === 'string') return value.trim() || UNKNOWN_VALUE;
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : UNKNOWN_VALUE;
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  return UNKNOWN_VALUE;
}

function formatLabel(value: unknown): string {
  const text = formatUnknown(value);
  if (text === UNKNOWN_VALUE) return text;
  return text
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatDate(value: unknown): string {
  if (typeof value !== 'string' && typeof value !== 'number') return UNKNOWN_VALUE;
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return UNKNOWN_VALUE;
  return new Intl.DateTimeFormat('en-ZA', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date);
}

function formatDuration(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
    ? `${value} min`
    : UNKNOWN_VALUE;
}

function formatAmount(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
    ? centsToRand(value)
    : UNKNOWN_VALUE;
}

function formatDrawAmount(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
    ? `${value} min`
    : UNKNOWN_VALUE;
}

function historyKey(prefix: string, record: HistoryRecord, index: number): string {
  const stableId = valueFrom(record, 'client_id', 'id');
  if (typeof stableId === 'string' && stableId !== '') return `${prefix}-${stableId}`;

  const entitlementId = valueFrom(record, 'entitlement_id');
  const timestamp = valueFrom(record, 'server_timestamp', 'client_timestamp', 'created_at');
  if (typeof entitlementId === 'string' && entitlementId !== '' && timestamp !== undefined) {
    return `${prefix}-${entitlementId}-${String(timestamp)}`;
  }
  return `${prefix}-${index}`;
}

function HistoryField({ term, value }: { term: string; value: string }) {
  return (
    <div>
      <dt>{term}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function PendingBadge({ local }: { local: boolean }) {
  return local ? <span className="local-badge">Pending sync</span> : null;
}

type HistoryLoadStatus = 'loading' | 'success' | 'error';

interface HistoryViewState {
  status: HistoryLoadStatus;
  merged: MergedPlayerDetail | null;
}

export function PlayerDetail() {
  const { id = '' } = useParams();
  const { client } = useAuth();
  const { cacheScope } = useReferenceData();
  const [history, setHistory] = useState<HistoryViewState>({
    status: 'loading',
    merged: null,
  });

  useEffect(() => {
    let alive = true;
    setHistory({ status: 'loading', merged: null });
    void (async () => {
      let server = EMPTY_HISTORY(id);
      let status: HistoryLoadStatus = 'success';
      try {
        server = await client.getPlayerHistory(id);
      } catch {
        status = 'error';
      }
      let local: LocalPlayerRecords = {};
      try {
        local = await localUnsyncedFor(id, cacheScope);
      } catch {
        // A local read failure must not hide history already returned by the server.
      }
      if (alive) {
        setHistory({ status, merged: mergePlayerDetail(server, local) });
      }
    })();
    return () => {
      alive = false;
    };
  }, [cacheScope, id, client]);

  const { merged, status } = history;

  const totals = useMemo(() => {
    if (!merged) return { sessions: 0, payments: 0, draws: 0 };
    return {
      sessions: merged.sessions.length,
      payments: merged.payments.length,
      draws: merged.entitlement_draws.length,
    };
  }, [merged]);

  return (
    <section aria-label="Player detail" data-screen-body="player-detail">
      <h1>Player</h1>
      <p className="player-id" data-player-id={id}>{id}</p>

      {status === 'loading' && (
        <p className="history-state" role="status">Loading player history…</p>
      )}
      {status === 'error' && (
        <p className="history-state" role="status">
          Server history unavailable. Showing saved activity from this device.
        </p>
      )}

      <h2>Sessions ({totals.sessions})</h2>
      {status === 'success' && merged && merged.sessions.length === 0 && (
        <p className="history-empty">No sessions recorded.</p>
      )}
      <ul className="history-list" aria-label="Sessions">
        {merged?.sessions.map((session, index) => {
          const record = session as HistoryRecord;
          const local = record.__local === true;
          return (
            <li key={historyKey('session', record, index)} data-local={String(local)}>
              <PendingBadge local={local} />
              <dl className="history-grid">
                <HistoryField term="Session type" value={formatLabel(valueFrom(record, 'session_type', 'type'))} />
                <HistoryField term="Reference" value={formatUnknown(valueFrom(record, 'reference'))} />
                <HistoryField term="Duration" value={formatDuration(valueFrom(record, 'duration_minutes', 'duration'))} />
                <HistoryField term="Started" value={formatDate(valueFrom(record, 'started_at'))} />
                <HistoryField term="Ended" value={formatDate(valueFrom(record, 'ended_at'))} />
                <HistoryField term="Recorded by" value={formatUnknown(valueFrom(record, 'logged_by'))} />
                <HistoryField term="Location" value={formatUnknown(valueFrom(record, 'location_id', 'location'))} />
              </dl>
            </li>
          );
        })}
      </ul>

      <h2>Payments ({totals.payments})</h2>
      {status === 'success' && merged && merged.payments.length === 0 && (
        <p className="history-empty">No payments recorded.</p>
      )}
      <ul className="history-list" aria-label="Payments">
        {merged?.payments.map((payment, index) => {
          const record = payment as HistoryRecord;
          const local = record.__local === true;
          return (
            <li key={historyKey('payment', record, index)} data-local={String(local)}>
              <PendingBadge local={local} />
              <dl className="history-grid">
                <HistoryField term="Amount" value={formatAmount(valueFrom(record, 'amount_cents'))} />
                <HistoryField term="Payment method" value={formatLabel(valueFrom(record, 'method', 'payment_method'))} />
                <HistoryField term="Paid at" value={formatDate(valueFrom(record, 'paid_at'))} />
                <HistoryField term="Product" value={formatUnknown(valueFrom(record, 'product_id', 'product'))} />
                <HistoryField term="Recorded by" value={formatUnknown(valueFrom(record, 'logged_by'))} />
                <HistoryField term="Location" value={formatUnknown(valueFrom(record, 'location_id', 'location'))} />
              </dl>
            </li>
          );
        })}
      </ul>

      <h2>Entitlement draws ({totals.draws})</h2>
      {status === 'success' && merged && merged.entitlement_draws.length === 0 && (
        <p className="history-empty">No entitlement draws recorded.</p>
      )}
      <ul className="history-list" aria-label="Entitlement draws">
        {merged?.entitlement_draws.map((draw, index) => {
          const record = draw as HistoryRecord;
          const local = record.__local === true;
          const localAmount = valueFrom(record, 'amount');
          const recordedBy = valueFrom(record, 'logged_by');
          const serverTimestamp = valueFrom(record, 'server_timestamp');
          const clientTimestamp = valueFrom(record, 'client_timestamp');
          return (
            <li key={historyKey('draw', record, index)} data-local={String(local)}>
              <PendingBadge local={local} />
              <dl className="history-grid">
                <HistoryField term="Entitlement" value={formatUnknown(valueFrom(record, 'entitlement_id'))} />
                <HistoryField term="Product" value={formatUnknown(valueFrom(record, 'product_id', 'product'))} />
                {local && localAmount !== undefined && (
                  <HistoryField term="Amount drawn" value={formatDrawAmount(localAmount)} />
                )}
                {(!local || recordedBy !== undefined) && (
                  <HistoryField term="Recorded by" value={formatUnknown(recordedBy)} />
                )}
                {(!local || serverTimestamp !== undefined) && (
                  <HistoryField term="Server recorded at" value={formatDate(serverTimestamp)} />
                )}
                {(!local || clientTimestamp !== undefined) && (
                  <HistoryField term="Client captured at" value={formatDate(clientTimestamp)} />
                )}
                {local && (
                  <HistoryField term="Saved on this device at" value={formatDate(valueFrom(record, 'created_at'))} />
                )}
              </dl>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

export default PlayerDetail;
