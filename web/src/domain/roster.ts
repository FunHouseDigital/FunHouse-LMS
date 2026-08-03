/**
 * Players roster + player-detail domain logic (Req 9). See design.md "Players
 * roster + detail".
 *
 * Pure functions that the Players screen and PlayerDetail screen consume:
 *  - {@link buildRosterRows} shapes cached `GET /players` + cached balances into
 *    display rows (name, entitlement balance, last-visit date, entitlement
 *    status — Req 9.1);
 *  - {@link filterRoster} applies the name search (Req 9.4 — Property 13);
 *  - {@link mergePlayerDetail} unions server history with local unsynced records
 *    (Req 9.3 — Property 14).
 */
import type { BalanceOut, PlayerHistory, PlayerOut } from './types';

/** A single roster display row (Req 9.1). */
export interface RosterRow {
  id: string;
  name: string;
  /** Total remaining entitlement minutes, `'unlimited'`, or `null` (none cached). */
  balance: number | 'unlimited' | null;
  lastVisit: string | null;
  entitlementStatus: string;
  /** True for an encrypted local registration not yet present in hydration. */
  isLocal?: boolean;
}

/** The full display name of a player row. */
export function playerName(player: Pick<PlayerOut, 'first_name' | 'last_name'>): string {
  return [player.first_name, player.last_name].filter((p) => p && p.length > 0).join(' ').trim();
}

/** Sum a player's cached balances into a single display figure (Req 9.1). */
export function summariseBalance(balances: BalanceOut[] | undefined): number | 'unlimited' | null {
  if (!balances || balances.length === 0) return null;
  if (balances.some((b) => b.remaining_units === null)) return 'unlimited';
  return balances.reduce((total, b) => total + (b.remaining_units ?? 0), 0);
}

/** Derive a single entitlement status label for a player (Req 9.1). */
export function summariseStatus(balances: BalanceOut[] | undefined): string {
  if (!balances || balances.length === 0) return 'none';
  if (balances.some((b) => b.status === 'active')) return 'active';
  return balances[0].status;
}

export interface RosterInputs {
  players: PlayerOut[];
  /** player_id → cached balances (from the `entitlement_balances` store). */
  balancesByPlayer?: Record<string, BalanceOut[]>;
  /** player_id → last-visit ISO date (derived from cached/local sessions). */
  lastVisitByPlayer?: Record<string, string | null>;
}

/** Build the roster display rows for the cached roster (Req 9.1). */
export function buildRosterRows(inputs: RosterInputs): RosterRow[] {
  const { players, balancesByPlayer = {}, lastVisitByPlayer = {} } = inputs;
  return players.map((player) => {
    const balances = balancesByPlayer[player.id];
    return {
      id: player.id,
      name: playerName(player),
      balance: summariseBalance(balances),
      lastVisit: lastVisitByPlayer[player.id] ?? null,
      entitlementStatus: summariseStatus(balances),
    };
  });
}

/**
 * Filter roster rows by a name search (Req 9.4 — Property 13). Matching is
 * case-insensitive substring; a blank/whitespace query returns every row.
 */
export function filterRoster(rows: RosterRow[], search: string): RosterRow[] {
  const needle = (search ?? '').trim().toLowerCase();
  if (needle === '') return [...rows];
  return rows.filter((row) => row.name.toLowerCase().includes(needle));
}

/** A merged player-detail view (server + local unsynced), by record class. */
export interface MergedPlayerDetail {
  player_id: string;
  sessions: Record<string, unknown>[];
  payments: Record<string, unknown>[];
  entitlement_draws: Record<string, unknown>[];
}

/** Local unsynced records for a player to merge into the detail view (Req 9.3). */
export interface LocalPlayerRecords {
  sessions?: Record<string, unknown>[];
  payments?: Record<string, unknown>[];
  entitlement_draws?: Record<string, unknown>[];
}

/**
 * Merge server history with locally captured, not-yet-synced records for a
 * player (Req 9.3 — Property 14): the result is the union of every server record
 * and every local unsynced record, with no omission.
 */
export function mergePlayerDetail(
  serverHistory: PlayerHistory,
  local: LocalPlayerRecords = {},
): MergedPlayerDetail {
  return {
    player_id: serverHistory.player_id,
    sessions: [...serverHistory.sessions, ...(local.sessions ?? [])],
    payments: [...serverHistory.payments, ...(local.payments ?? [])],
    entitlement_draws: [...serverHistory.entitlement_draws, ...(local.entitlement_draws ?? [])],
  };
}
