/**
 * Player detail screen (Req 9.3). See design.md "Players roster + detail".
 *
 * Displays a player's session, payment, and entitlement history, merging the
 * server history (`GET /players/{id}/history`) with locally captured records
 * that have not yet synced (Req 9.3). When offline (or the fetch fails) the view
 * falls back to an empty server history and shows only the local unsynced
 * records — the merge never omits either side (Property 14).
 */
import { useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useAuth } from '../state/authState';
import { useReferenceData } from '../state/referenceDataState';
import { getActionsByStatus } from '../store/localStore';
import { mergePlayerDetail, type LocalPlayerRecords, type MergedPlayerDetail } from '../domain/roster';
import type { PlayerHistory, StoredSyncAction } from '../domain/types';

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
    sessions: forPlayer.filter((a) => a.entity === 'session').map((a) => ({ ...a.payload, __local: true, client_id: a.client_id })),
    payments: forPlayer.filter((a) => a.entity === 'payment').map((a) => ({ ...a.payload, __local: true, client_id: a.client_id })),
    entitlement_draws: forPlayer
      .filter((a) => a.entity === 'entitlement' && typeof (a.payload as Record<string, unknown>).amount === 'number')
      .map((a) => ({ ...a.payload, __local: true, client_id: a.client_id })),
  };
}

export function PlayerDetail() {
  const { id = '' } = useParams();
  const { client } = useAuth();
  const { cacheScope } = useReferenceData();
  const [merged, setMerged] = useState<MergedPlayerDetail | null>(null);

  useEffect(() => {
    let alive = true;
    void (async () => {
      let server: PlayerHistory = EMPTY_HISTORY(id);
      try {
        server = await client.getPlayerHistory(id);
      } catch {
        // Offline / error → local-only detail (Req 9.3 fallback).
      }
      const local = await localUnsyncedFor(id, cacheScope);
      if (alive) setMerged(mergePlayerDetail(server, local));
    })();
    return () => {
      alive = false;
    };
  }, [cacheScope, id, client]);

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
      <p data-player-id={id}>{id}</p>

      <h2>Sessions ({totals.sessions})</h2>
      <ul aria-label="Sessions">
        {merged?.sessions.map((s, i) => (
          <li key={`s-${i}`} data-local={String(Boolean((s as Record<string, unknown>).__local))}>
            {JSON.stringify(s)}
          </li>
        ))}
      </ul>

      <h2>Payments ({totals.payments})</h2>
      <ul aria-label="Payments">
        {merged?.payments.map((p, i) => (
          <li key={`p-${i}`} data-local={String(Boolean((p as Record<string, unknown>).__local))}>
            {JSON.stringify(p)}
          </li>
        ))}
      </ul>

      <h2>Entitlement draws ({totals.draws})</h2>
      <ul aria-label="Entitlement draws">
        {merged?.entitlement_draws.map((d, i) => (
          <li key={`d-${i}`} data-local={String(Boolean((d as Record<string, unknown>).__local))}>
            {JSON.stringify(d)}
          </li>
        ))}
      </ul>
    </section>
  );
}

export default PlayerDetail;
