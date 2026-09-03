/**
 * Players roster screen (Req 9.1, 9.2, 9.4, 9.5). See design.md "Players roster
 * + detail".
 *
 * Renders each in-scope player with name, entitlement balance, last-visit date,
 * and entitlement status. The roster renders offline from the last cached
 * `GET /players` in the Local_Store (Req 9.2). A name search filters the rows
 * (Req 9.4); an empty roster shows an empty state (Req 9.5). Selecting a row
 * navigates to the player detail.
 */
import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useReferenceData } from '../state/referenceDataState';
import { useKnownPlayers, type PlayerChoice } from './useKnownPlayers';
import {
  getAllLocalRecords,
  getBalances,
  getOwnerMetadata,
} from '../store/localStore';
import {
  filterRoster,
  summariseBalance,
  summariseStatus,
  type RosterRow,
} from '../domain/roster';
import { localDataLifecycleIdentity } from '../domain/personalData';
import { playerResolutionMetaKey } from '../domain/syncEngine';
import type { BalanceOut, LocalDataOwner } from '../domain/types';

async function loadRosterRows(
  players: PlayerChoice[],
  cacheScope: string | null,
  owner: LocalDataOwner | null,
): Promise<RosterRow[]> {
  if (!owner || owner.scope !== cacheScope || !owner.isCurrent()) return [];
  const balancesByPlayer: Record<string, BalanceOut[]> = {};
  for (const player of players) {
    const balanceId = player.resolvedId ?? player.id;
    const bal = await getBalances(balanceId, owner);
    if (!owner.isCurrent()) return [];
    if (bal) balancesByPlayer[player.id] = bal.balances;
  }

  // Derive last-visit from locally captured sessions (offline-safe). Resolve
  // local player ids so hydrated server rows retain their pre-sync activity.
  const [localSessions, resolutions] = cacheScope
    ? await Promise.all([
        getAllLocalRecords('sessions', owner),
        getOwnerMetadata<Record<string, string>>(playerResolutionMetaKey(cacheScope), owner),
      ])
    : [[], undefined];
  if (!owner.isCurrent()) return [];
  const resolvedByLocalId = resolutions ?? {};
  const lastVisitByPlayer: Record<string, string | null> = {};
  for (const session of localSessions) {
    const rawPlayerId = session.player_id ? String(session.player_id) : null;
    if (!rawPlayerId) continue;
    const playerId = resolvedByLocalId[rawPlayerId] ?? rawPlayerId;
    const day = typeof session.day === 'string' ? session.day : null;
    if (day && (!lastVisitByPlayer[playerId] || day > lastVisitByPlayer[playerId]!)) {
      lastVisitByPlayer[playerId] = day;
    }
  }

  return players.map((player) => {
    const balances = balancesByPlayer[player.id];
    const activityId = player.resolvedId ?? player.id;
    const awaitsServerId = player.source === 'local' && !player.resolvedId;
    return {
      // Once sync has resolved a local registration, use the server id so the
      // stale local roster mirror can still open the server-backed history.
      id: activityId,
      name: player.name,
      balance: summariseBalance(balances),
      lastVisit: lastVisitByPlayer[activityId] ?? null,
      entitlementStatus: awaitsServerId ? 'pending sync' : summariseStatus(balances),
      ...(awaitsServerId ? { isLocal: true } : {}),
    };
  });
}

export function Players() {
  const { cacheScope, owner } = useReferenceData();
  const identity = localDataLifecycleIdentity(owner);
  const players = useKnownPlayers();
  const [snapshot, setSnapshot] = useState<{ identity: string | null; rows: RosterRow[]; loaded: boolean }>({ identity: null, rows: [], loaded: false });

  useEffect(() => {
    let alive = true;
    void (async () => {
      const next = await loadRosterRows(players, cacheScope, owner);
      if (alive && owner?.isCurrent()) {
        setSnapshot({ identity, rows: next, loaded: true });
      }
    })();
    return () => {
      alive = false;
    };
  }, [cacheScope, identity, owner, players]);

  const current = snapshot.identity === identity && owner?.isCurrent()
    ? snapshot
    : { identity, rows: [], loaded: false };
  const [searchState, setSearchState] = useState({ identity: null as string | null, value: '' });
  const search = searchState.identity === identity ? searchState.value : '';
  const visible = useMemo(() => filterRoster(current.rows, search), [current.rows, search]);

  return (
    <section aria-label="Players" data-screen-body="players">
      <h1>Players</h1>
      <p className="screen-intro">Find players, review balances and activity, or add someone new.</p>

      <Link className="button-link" to="/register">Add player</Link>

      <input
        type="search"
        aria-label="Search players by name"
        placeholder="Search by name"
        value={search}
        onChange={(e) => setSearchState({ identity, value: e.target.value })}
      />

      {current.loaded && current.rows.length === 0 && <p role="status">No players yet.</p>}
      {!current.loaded && <p role="status">Loading players…</p>}
      {current.loaded && current.rows.length > 0 && visible.length === 0 && (
        <p role="status">No players match “{search.trim()}”.</p>
      )}

      <ul aria-label="Player roster">
        {visible.map((row) => {
          const content = (
            <>
              <span>{row.name}</span>
              <span data-field="balance">
                {row.balance === null
                  ? 'no entitlement'
                  : row.balance === 'unlimited'
                    ? 'unlimited'
                    : `${row.balance} min`}
              </span>
              <span data-field="last-visit">{row.lastVisit ?? 'never'}</span>
              <span data-field="status">{row.entitlementStatus}</span>
            </>
          );
          return (
            <li key={row.id} data-player-row={row.id}>
              <Link to={`/players/${encodeURIComponent(row.id)}`}>{content}</Link>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

export default Players;
