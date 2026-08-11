/**
 * PlayerPicker — the reusable player search/select control, factored out of the
 * Log Session pattern (search box + recent-players list) so the Metrics grid can
 * tie each row to a registered `player_id` using the exact same interaction
 * (design.md "Log Session — Session_Logger"). It intentionally reuses the shared
 * {@link useKnownPlayers} data source rather than introducing a new picker.
 *
 * A blank query shows the most-recent players; a non-blank query filters the
 * full known-players list by a case-insensitive name substring.
 */
import { useMemo, useState } from 'react';
import { useKnownPlayers, type PlayerChoice } from './useKnownPlayers';

export interface PlayerPickerProps {
  /** Currently-selected player id, or `null` when none is chosen. */
  selectedId: string | null;
  /** Invoked with the chosen player when the user selects one. */
  onSelect: (player: PlayerChoice) => void;
  /** Accessible label suffix so multiple pickers on one screen stay distinct. */
  idSuffix?: string;
  /** Max recent players shown for a blank query (default 5). */
  recentLimit?: number;
  /** Whether locally registered, not-yet-synced players may be selected. */
  includeLocal?: boolean;
}

export function PlayerPicker({
  selectedId,
  onSelect,
  idSuffix,
  recentLimit = 5,
  includeLocal = true,
}: PlayerPickerProps) {
  const players = useKnownPlayers({ includeLocal });
  const [search, setSearch] = useState('');

  const recent = useMemo(() => players.slice(0, recentLimit), [players, recentLimit]);
  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (needle === '') return recent;
    return players.filter((p) => p.name.toLowerCase().includes(needle));
  }, [players, recent, search]);

  const searchLabel = idSuffix ? `Search players ${idSuffix}` : 'Search players';
  const listLabel = idSuffix ? `Players ${idSuffix}` : 'Players';

  return (
    <div className="player-picker">
      <input
        type="search"
        aria-label={searchLabel}
        placeholder="Search players"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />
      <ul aria-label={listLabel}>
        {filtered.map((p) => (
          <li key={p.id}>
            <button
              type="button"
              aria-pressed={selectedId === p.id}
              onClick={() => onSelect(p)}
            >
              {p.name}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default PlayerPicker;
