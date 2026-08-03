/**
 * Shared "known players" hook — the single player-selection data source reused
 * by the capture screens (Log Session, Metrics). See design.md "Log Session —
 * Session_Logger" and "Players roster + detail".
 *
 * It unions the cached `GET /players` roster with locally-registered players not
 * yet synced (whose `id` is their local id — the same value the Sync_Engine's
 * Dependency-D2 resolution later rewrites to the server id). Selecting a local
 * player therefore threads its local id through the capture payload exactly like
 * session/payment do, so metrics keyed on `player_id` resolve identically.
 */
import { useEffect, useState } from 'react';
import { getAllLocalRecords, getCachedRead, type LocalRecord } from '../store/localStore';
import { useReferenceData } from '../state/referenceDataState';
import { playerName } from '../domain/roster';
import type { PlayerOut } from '../domain/types';

/** A selectable player: a stable `id` (server id or local id) and a display name. */
export interface PlayerChoice {
  id: string;
  name: string;
}

/**
 * Load the union of cached-roster players and locally-registered players (Req
 * 7.2, 9.1). Local players are excluded when they already appear in the cached
 * roster (same id).
 */
export function useKnownPlayers(): PlayerChoice[] {
  const { revision, playersCacheKey } = useReferenceData();
  const [players, setPlayers] = useState<PlayerChoice[]>([]);
  useEffect(() => {
    let alive = true;
    void (async () => {
      const cached = await getCachedRead<PlayerOut[]>(playersCacheKey);
      const roster: PlayerChoice[] = (cached?.data ?? []).map((p) => ({
        id: p.id,
        name: playerName(p),
      }));
      const local = await getAllLocalRecords('players');
      const localChoices: PlayerChoice[] = local
        .map((r: LocalRecord) => ({ id: String(r.local_id), name: String(r.name ?? 'New player') }))
        .filter((c) => !roster.some((r) => r.id === c.id));
      if (alive) setPlayers([...roster, ...localChoices]);
    })();
    return () => {
      alive = false;
    };
  }, [playersCacheKey, revision]);
  return players;
}
