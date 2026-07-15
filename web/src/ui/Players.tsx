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
import {
  getAllLocalRecords,
  getBalances,
  getCachedRead,
} from '../store/localStore';
import {
  buildRosterRows,
  filterRoster,
  type RosterRow,
} from '../domain/roster';
import type { BalanceOut, PlayerOut } from '../domain/types';

async function loadRosterRows(): Promise<RosterRow[]> {
  const cached = await getCachedRead<PlayerOut[]>('players');
  const players = cached?.data ?? [];

  const balancesByPlayer: Record<string, BalanceOut[]> = {};
  for (const player of players) {
    const bal = await getBalances(player.id);
    if (bal) balancesByPlayer[player.id] = bal.balances;
  }

  // Derive last-visit from locally captured sessions (offline-safe).
  const localSessions = await getAllLocalRecords('sessions');
  const lastVisitByPlayer: Record<string, string | null> = {};
  for (const session of localSessions) {
    const pid = session.player_id ? String(session.player_id) : null;
    if (!pid) continue;
    const day = typeof session.day === 'string' ? session.day : null;
    if (day && (!lastVisitByPlayer[pid] || day > lastVisitByPlayer[pid]!)) {
      lastVisitByPlayer[pid] = day;
    }
  }

  return buildRosterRows({ players, balancesByPlayer, lastVisitByPlayer });
}

export function Players() {
  const [rows, setRows] = useState<RosterRow[]>([]);
  const [search, setSearch] = useState('');
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let alive = true;
    void (async () => {
      const next = await loadRosterRows();
      if (alive) {
        setRows(next);
        setLoaded(true);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const visible = useMemo(() => filterRoster(rows, search), [rows, search]);

  return (
    <section aria-label="Players" data-screen-body="players">
      <h1>Players</h1>

      <Link to="/register">Add player</Link>

      <input
        type="search"
        aria-label="Search players by name"
        placeholder="Search by name"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />

      {loaded && rows.length === 0 && <p role="status">No players yet.</p>}

      <ul aria-label="Player roster">
        {visible.map((row) => (
          <li key={row.id} data-player-row={row.id}>
            <Link to={`/players/${encodeURIComponent(row.id)}`}>{row.name}</Link>
            <span data-field="balance">
              {row.balance === null
                ? 'no entitlement'
                : row.balance === 'unlimited'
                  ? 'unlimited'
                  : `${row.balance} min`}
            </span>
            <span data-field="last-visit">{row.lastVisit ?? 'never'}</span>
            <span data-field="status">{row.entitlementStatus}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

export default Players;
