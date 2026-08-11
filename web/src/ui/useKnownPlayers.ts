/**
 * Shared "known players" data source for player rosters and capture screens.
 *
 * Server players come from the account-scoped `GET /players` cache. Players
 * registered on this device are read only from the same authenticated scope and
 * their names are decrypted through the central personal-data guard. Local ids
 * remain selectable until Dependency D2 resolves them during sync.
 */
import { useEffect, useState } from 'react';
import { readPersonalData } from '../domain/personalData';
import { playerName } from '../domain/roster';
import {
  playerResolutionMetaKey,
  subscribePlayerDirectoryChanged,
} from '../domain/syncEngine';
import type { PlayerOut } from '../domain/types';
import {
  getAllLocalRecords,
  getCachedRead,
  getMeta,
  type LocalRecord,
} from '../store/localStore';
import { useReferenceData } from '../state/referenceDataState';

/** A selectable player with enough provenance for pending-local UI states. */
export interface PlayerChoice {
  /** Server id for hydrated players; local player action id while awaiting hydration. */
  id: string;
  name: string;
  source: 'server' | 'local';
  /** Server id already returned by sync while the roster cache is still stale. */
  resolvedId?: string;
}

interface LocalPlayerPersonal {
  name?: unknown;
}

export interface LoadKnownPlayersOptions {
  playersCacheKey: string;
  cacheScope: string | null;
  includeLocal?: boolean;
}

async function localPlayerName(record: LocalRecord): Promise<string> {
  try {
    const personal = await readPersonalData<LocalPlayerPersonal>(record);
    const name = typeof personal?.name === 'string' ? personal.name.trim() : '';
    return name || 'Name unavailable';
  } catch {
    // A missing, expired, changed, or corrupt key must withhold rather than leak
    // or crash the authenticated screen.
    return 'Name unavailable';
  }
}

/**
 * Load the account-owned union of hydrated and locally registered players.
 * Once a local id resolves and the server roster contains that server id, the
 * local mirror is suppressed so hydration cannot create a duplicate row.
 */
export async function loadKnownPlayers({
  playersCacheKey,
  cacheScope,
  includeLocal = true,
}: LoadKnownPlayersOptions): Promise<PlayerChoice[]> {
  const cached = await getCachedRead<PlayerOut[]>(playersCacheKey);
  const roster: PlayerChoice[] = (cached?.data ?? []).map((player) => ({
    id: player.id,
    name: playerName(player),
    source: 'server',
  }));
  if (!includeLocal || !cacheScope) return roster;

  const [localRecords, resolutions] = await Promise.all([
    getAllLocalRecords('players', cacheScope),
    getMeta<Record<string, string>>(playerResolutionMetaKey(cacheScope)),
  ]);
  const resolvedByLocalId = resolutions ?? {};
  const serverIds = new Set(roster.map((player) => player.id));

  const localChoices = await Promise.all(
    localRecords.map(async (record): Promise<PlayerChoice | null> => {
      const localId = String(record.local_id);
      const resolvedId = resolvedByLocalId[localId];
      if (serverIds.has(localId) || (resolvedId && serverIds.has(resolvedId))) {
        return null;
      }
      return {
        id: localId,
        name: await localPlayerName(record),
        source: 'local',
        ...(resolvedId ? { resolvedId } : {}),
      };
    }),
  );

  return [
    ...roster,
    ...localChoices.filter((choice): choice is PlayerChoice => choice !== null),
  ];
}

export interface KnownPlayersState {
  players: PlayerChoice[];
  /** True once the current account-scoped cache/local hydration has completed. */
  loaded: boolean;
  /** True when the current account-scoped player directory could not be read. */
  error: boolean;
}

interface KnownPlayersSnapshot extends KnownPlayersState {
  key: string;
}

/**
 * Load known players while exposing whether the current scope has hydrated.
 * Existing callers that only need the array can continue using
 * {@link useKnownPlayers}.
 */
export function useKnownPlayersState(
  options: { includeLocal?: boolean } = {},
): KnownPlayersState {
  const { includeLocal = true } = options;
  const { revision, playersCacheKey, cacheScope } = useReferenceData();
  const stateKey = `${playersCacheKey}\u0000${cacheScope ?? ''}\u0000${String(includeLocal)}`;
  const [snapshot, setSnapshot] = useState<KnownPlayersSnapshot>({
    key: stateKey,
    players: [],
    loaded: false,
    error: false,
  });

  useEffect(() => {
    let alive = true;
    let sequence = 0;
    const reload = () => {
      const request = ++sequence;
      void loadKnownPlayers({ playersCacheKey, cacheScope, includeLocal })
        .then((loaded) => {
          if (alive && request === sequence) {
            setSnapshot({
              key: stateKey,
              players: loaded,
              loaded: true,
              error: false,
            });
          }
        })
        .catch(() => {
          if (alive && request === sequence) {
            setSnapshot((current) =>
              current.key === stateKey
                ? { ...current, loaded: true, error: true }
                : {
                    key: stateKey,
                    players: [],
                    loaded: true,
                    error: true,
                  },
            );
          }
        });
    };
    reload();
    const unsubscribe = subscribePlayerDirectoryChanged(cacheScope, reload);
    return () => {
      alive = false;
      unsubscribe();
    };
  }, [cacheScope, includeLocal, playersCacheKey, revision, stateKey]);

  if (snapshot.key !== stateKey) {
    return { players: [], loaded: false, error: false };
  }
  return {
    players: snapshot.players,
    loaded: snapshot.loaded,
    error: snapshot.error,
  };
}

export function useKnownPlayers(options: { includeLocal?: boolean } = {}): PlayerChoice[] {
  return useKnownPlayersState(options).players;
}
