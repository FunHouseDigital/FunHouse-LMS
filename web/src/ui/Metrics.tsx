/**
 * Metrics entry screen — `Metrics_Module` (Req 15, Dependency D1 resolved). See
 * design.md "Metrics — Metrics_Module".
 *
 * A grid with columns for student, words-per-minute, and accuracy. Each row's
 * student is a **selected registered player** (chosen with the shared
 * {@link PlayerPicker}, the same search/select control used by Log Session) so
 * the saved metric can be keyed on `player_id` — the natural key the Container
 * API's `student_metrics` entity expects (D1, PR #3). Numeric fields accept only
 * non-negative numeric input (Req 15.2); invalid entries mark the field and
 * disable the row's save. Saving builds a live `student_metrics` action per
 * provided metric, enqueued with the normal `unsynced` status so the Sync_Engine
 * includes it in the next batch and reconciles it like the other capture
 * entities. The student's display name stays personal data — encrypted at rest
 * locally (Req 17.1) and never sent on the wire.
 */
import { useCallback, useState } from 'react';
import { useServices } from '../state/servicesState';
import {
  buildMetricsActions,
  canSaveMetricsRow,
  isNonNegativeNumeric,
  type MetricsRowInput,
} from '../domain/captures/metrics';
import { PlayerPicker } from './PlayerPicker';
import type { PlayerChoice } from './useKnownPlayers';

interface GridRow {
  key: string;
  playerId: string | null;
  studentName: string;
  wpm: string;
  accuracy: string;
  saved: boolean;
}

let rowSeq = 0;
function newRow(): GridRow {
  rowSeq += 1;
  return { key: `row-${rowSeq}`, playerId: null, studentName: '', wpm: '', accuracy: '', saved: false };
}

export function Metrics() {
  const { commit } = useServices();
  const [rows, setRows] = useState<GridRow[]>(() => [newRow(), newRow(), newRow()]);

  const update = useCallback((key: string, patch: Partial<GridRow>) => {
    setRows((prev) => prev.map((r) => (r.key === key ? { ...r, ...patch, saved: false } : r)));
  }, []);

  const selectPlayer = useCallback(
    (key: string, player: PlayerChoice) => {
      update(key, { playerId: player.id, studentName: player.name });
    },
    [update],
  );

  const saveRow = useCallback(
    async (row: GridRow) => {
      // A registered player must be selected so the metric carries a player_id
      // (the API's natural key for student_metrics — D1).
      if (!row.playerId) return;
      const input: MetricsRowInput = {
        playerId: row.playerId,
        studentName: row.studentName || undefined,
        wpm: row.wpm,
        accuracy: row.accuracy,
      };
      if (!canSaveMetricsRow(input)) return;
      const result = buildMetricsActions(input, {
        now: new Date().toISOString(),
        newId: () => globalThis.crypto.randomUUID(),
      });
      await commit(result);
      setRows((prev) => prev.map((r) => (r.key === row.key ? { ...r, saved: true } : r)));
    },
    [commit],
  );

  return (
    <section aria-label="Metrics" data-screen-body="metrics">
      <h1>Metrics Entry</h1>

      <table>
        <thead>
          <tr>
            <th scope="col">Student</th>
            <th scope="col">WPM</th>
            <th scope="col">Accuracy</th>
            <th scope="col" aria-label="Actions" />
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const wpmValid = row.wpm === '' || isNonNegativeNumeric(row.wpm);
            const accValid = row.accuracy === '' || isNonNegativeNumeric(row.accuracy);
            const canSave =
              !!row.playerId &&
              canSaveMetricsRow({ wpm: row.wpm, accuracy: row.accuracy }) &&
              wpmValid &&
              accValid;
            return (
              <tr key={row.key} data-row={row.key}>
                <td>
                  <PlayerPicker
                    idSuffix={row.key}
                    selectedId={row.playerId}
                    onSelect={(player) => selectPlayer(row.key, player)}
                  />
                  {row.studentName && (
                    <span data-selected-student={row.key}>{row.studentName}</span>
                  )}
                </td>
                <td>
                  <input
                    type="text"
                    inputMode="numeric"
                    aria-label={`WPM ${row.key}`}
                    aria-invalid={!wpmValid}
                    value={row.wpm}
                    onChange={(e) => update(row.key, { wpm: e.target.value })}
                  />
                </td>
                <td>
                  <input
                    type="text"
                    inputMode="numeric"
                    aria-label={`Accuracy ${row.key}`}
                    aria-invalid={!accValid}
                    value={row.accuracy}
                    onChange={(e) => update(row.key, { accuracy: e.target.value })}
                  />
                </td>
                <td>
                  <button type="button" disabled={!canSave} onClick={() => void saveRow(row)}>
                    Save
                  </button>
                  {row.saved && <span role="status">saved</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <button type="button" onClick={() => setRows((prev) => [...prev, newRow()])}>
        Add row
      </button>
    </section>
  );
}

export default Metrics;
