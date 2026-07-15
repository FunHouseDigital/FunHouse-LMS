/**
 * Attendance + school-session capture builder — `Attendance_Module` (Req 14).
 * See design.md "Attendance & school sessions — Attendance_Module".
 *
 * Pure builder: given a session type, a type-specific reference quick field, and
 * a class roster with per-member attendance toggles, it produces a `session`
 * record + action and one `attendance` record + action per **present** member.
 * The attendance actions reference the session action's `client_id` as their
 * local `session_id` (the same local-id-resolution shape the Sync_Engine handles
 * for players, D2-style).
 */
import type { CaptureContext, CaptureResult } from './types';
import { dayOf } from './types';

/** School session types (all valid server `session_type`s, Req 14.1). */
export type SchoolSessionType = 'lesson' | 'kit' | 'esports';

/** The label of the type-specific quick field (Req 14.3). */
export function referenceLabelFor(sessionType: SchoolSessionType): string {
  switch (sessionType) {
    case 'kit':
      return 'Kit module';
    case 'esports':
      return 'Match particulars';
    case 'lesson':
    default:
      return 'Lesson reference';
  }
}

/** A single roster member with their attendance toggle. */
export interface RosterAttendee {
  playerId: string;
  present: boolean;
}

/** Validated attendance input. */
export interface AttendanceInput {
  sessionType: SchoolSessionType;
  /** kit-module / match-particulars / lesson-reference quick field (Req 14.3). */
  reference?: string;
  roster: readonly RosterAttendee[];
  schoolId?: string;
}

/** True when a school session may be confirmed: a type is chosen (Req 14.1). */
export function canConfirmAttendance(input: Partial<AttendanceInput> | null | undefined): boolean {
  if (!input) return false;
  return input.sessionType === 'lesson' || input.sessionType === 'kit' || input.sessionType === 'esports';
}

/**
 * Build the local records + Sync_Actions for a confirmed school session (Req
 * 14.4): one `session` leg, plus one `attendance` leg per present roster member.
 * Absent members produce nothing.
 */
export function buildAttendanceActions(input: AttendanceInput, ctx: CaptureContext): CaptureResult {
  const day = dayOf(ctx.now);
  const sessionClientId = ctx.newId();
  const present = input.roster.filter((m) => m.present);

  const result: CaptureResult = {
    records: [
      {
        store: 'sessions',
        record: {
          local_id: sessionClientId,
          client_id: sessionClientId,
          day,
          session_type: input.sessionType,
          reference: input.reference,
          school_id: input.schoolId,
        },
      },
    ],
    actions: [
      {
        action: {
          client_id: sessionClientId,
          entity: 'session',
          created_at: ctx.now,
          payload: {
            session_type: input.sessionType,
            started_at: ctx.now,
            ended_at: ctx.now,
            duration_minutes: 0,
            reference: input.reference,
            school_id: input.schoolId,
          },
        },
      },
    ],
  };

  for (const member of present) {
    const attClientId = ctx.newId();
    result.records.push({
      store: 'attendance',
      record: {
        local_id: attClientId,
        client_id: attClientId,
        player_id: member.playerId,
        session_id: sessionClientId,
        day,
      },
    });
    result.actions.push({
      action: {
        client_id: attClientId,
        entity: 'attendance',
        created_at: ctx.now,
        payload: {
          session_id: sessionClientId,
          player_id: member.playerId,
          attendance_date: day,
          present: true,
          school_id: input.schoolId,
        },
      },
    });
  }

  return result;
}
