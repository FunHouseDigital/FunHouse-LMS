/**
 * Attendance & school-sessions screen — `Attendance_Module` (Req 14). See
 * design.md "Attendance & school sessions — Attendance_Module".
 *
 * Requires a session type (lesson | kit | esports), shows a class roster with a
 * tap-to-toggle attendance control per member, and presents the type-specific
 * quick field (kit→kit-module, esports→match-particulars, lesson→lesson-
 * reference) carried in the session payload `reference`. On confirm the pure
 * builder produces a `session` record + action and one `attendance` record +
 * action per present member; the commit step persists/enqueues them offline.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useServices } from '../state/servicesState';
import { getAllLocalRecords, getCachedRead } from '../store/localStore';
import {
  buildAttendanceActions,
  canConfirmAttendance,
  referenceLabelFor,
  type AttendanceInput,
  type RosterAttendee,
  type SchoolSessionType,
} from '../domain/captures/attendance';
import { playerName } from '../domain/roster';
import type { PlayerOut } from '../domain/types';

interface RosterEntry {
  id: string;
  name: string;
  present: boolean;
}

const TYPE_LABELS: Record<SchoolSessionType, string> = {
  lesson: 'Lesson',
  kit: 'Kit',
  esports: 'Esports',
};

export function Attendance() {
  const { commit } = useServices();
  const [sessionType, setSessionType] = useState<SchoolSessionType>('lesson');
  const [reference, setReference] = useState('');
  const [roster, setRoster] = useState<RosterEntry[]>([]);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let alive = true;
    void (async () => {
      const cached = await getCachedRead<PlayerOut[]>('players');
      const server = (cached?.data ?? []).map((p) => ({ id: p.id, name: playerName(p), present: false }));
      const local = (await getAllLocalRecords('players')).map((r) => ({
        id: String(r.local_id),
        name: String(r.name ?? 'New player'),
        present: false,
      }));
      if (alive) setRoster([...server, ...local.filter((l) => !server.some((s) => s.id === l.id))]);
    })();
    return () => {
      alive = false;
    };
  }, []);

  const toggle = useCallback((id: string) => {
    setRoster((prev) => prev.map((m) => (m.id === id ? { ...m, present: !m.present } : m)));
  }, []);

  const canConfirm = useMemo(() => canConfirmAttendance({ sessionType }), [sessionType]);

  const onConfirm = useCallback(async () => {
    const attendees: RosterAttendee[] = roster.map((m) => ({ playerId: m.id, present: m.present }));
    const input: AttendanceInput = { sessionType, reference: reference || undefined, roster: attendees };
    const result = buildAttendanceActions(input, {
      now: new Date().toISOString(),
      newId: () => globalThis.crypto.randomUUID(),
    });
    await commit(result);
    setSaved(true);
    setReference('');
    setRoster((prev) => prev.map((m) => ({ ...m, present: false })));
  }, [roster, sessionType, reference, commit]);

  return (
    <section aria-label="Attendance" data-screen-body="attendance">
      <h1>Attendance & Sessions</h1>

      <fieldset>
        <legend>Session type</legend>
        {(Object.keys(TYPE_LABELS) as SchoolSessionType[]).map((t) => (
          <label key={t}>
            <input
              type="radio"
              name="session-type"
              value={t}
              checked={sessionType === t}
              onChange={() => setSessionType(t)}
            />
            {TYPE_LABELS[t]}
          </label>
        ))}
      </fieldset>

      <label>
        {referenceLabelFor(sessionType)}
        <input
          type="text"
          aria-label={referenceLabelFor(sessionType)}
          value={reference}
          onChange={(e) => setReference(e.target.value)}
        />
      </label>

      <fieldset>
        <legend>Roster</legend>
        <ul aria-label="Class roster">
          {roster.map((m) => (
            <li key={m.id}>
              <button type="button" aria-pressed={m.present} onClick={() => toggle(m.id)}>
                {m.name} {m.present ? '✓ present' : ''}
              </button>
            </li>
          ))}
        </ul>
      </fieldset>

      <button type="button" disabled={!canConfirm} onClick={onConfirm}>
        Confirm session
      </button>

      {saved && <p role="status">School session logged</p>}
    </section>
  );
}

export default Attendance;
