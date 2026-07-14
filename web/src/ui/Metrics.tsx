/**
 * Metrics entry screen — `Metrics_Module` (Req 15, Dependency D1). See design.md
 * "Metrics — Metrics_Module" and "Dependency D1".
 *
 * A grid with columns for student name, words-per-minute, and accuracy. Numeric
 * fields accept only non-negative numeric input (Req 15.2); invalid entries mark
 * the field and disable the row's save. Saving builds a forward-compatible
 * `student_metrics` action per provided metric, enqueued in the `blocked`
 * sub-status because `/sync` has no `student_metrics` entity yet (D1) — no
 * backend change is made here.
 */
import { useCallback, useState } from 'react';
import { useServices } from '../state/servicesState';
import {
  buildMetricsActions,
  canSaveMetricsRow,
  isNonNegativeNumeric,
  type MetricsRowInput,
} from '../domain/captures/metrics';

interface GridRow {
  key: string;
  studentName: string;
  wpm: string;
  accuracy: string;
  saved: boolean;
}

let rowSeq = 0;
function newRow(): GridRow {
  rowSeq += 1;
  return { key: `row-${rowSeq}`, studentName: '', wpm: '', accuracy: '', saved: false };
}

export function Metrics() {
  const { commit } = useServices();
  const [rows, setRows] = useState<GridRow[]>(() => [newRow(), newRow(), newRow()]);

  const update = useCallback((key: string, patch: Partial<GridRow>) => {
    setRows((prev) => prev.map((r) => (r.key === key ? { ...r, ...patch, saved: false } : r)));
  }, []);

  const saveRow = useCallback(
    async (row: GridRow) => {
      const input: MetricsRowInput = {
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
            const canSave = canSaveMetricsRow({ wpm: row.wpm, accuracy: row.accuracy }) && wpmValid && accValid;
            return (
              <tr key={row.key} data-row={row.key}>
                <td>
                  <input
                    type="text"
                    aria-label={`Student name ${row.key}`}
                    value={row.studentName}
                    onChange={(e) => update(row.key, { studentName: e.target.value })}
                  />
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
