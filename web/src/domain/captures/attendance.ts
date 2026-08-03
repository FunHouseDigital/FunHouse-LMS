import type { CaptureContext, CaptureResult } from './types';
import { dayOf } from './types';

export type SchoolSessionType = 'lesson' | 'kit' | 'esports';

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

export interface RosterAttendee {
  playerId: string;
  present: boolean;
}

export interface AttendanceInput {
  sessionType: SchoolSessionType;
  reference?: string;
  roster: readonly RosterAttendee[];
  schoolId?: string;
}

/** A school session requires a valid type and at least one present learner. */
export function canConfirmAttendance(input: Partial<AttendanceInput> | null | undefined): boolean {
  if (!input) return false;
  const validType =
    input.sessionType === 'lesson' ||
    input.sessionType === 'kit' ||
    input.sessionType === 'esports';
  return validType && Boolean(input.roster?.some((member) => member.present));
}

/**
 * Build one player-linked school session and one attendance row per present
 * learner. Attendance references the local session action by `client_id`; the
 * Sync_Engine defers it until the server session id is available, then rewrites
 * the reference before transmission.
 */
export function buildAttendanceActions(input: AttendanceInput, ctx: CaptureContext): CaptureResult {
  const day = dayOf(ctx.now);
  const result: CaptureResult = { records: [], actions: [] };

  for (const member of input.roster.filter((candidate) => candidate.present)) {
    const sessionClientId = ctx.newId();
    const attendanceClientId = ctx.newId();

    result.records.push({
      store: 'sessions',
      record: {
        local_id: sessionClientId,
        client_id: sessionClientId,
        player_id: member.playerId,
        day,
        session_type: input.sessionType,
        reference: input.reference,
        school_id: input.schoolId,
      },
    });
    result.actions.push({
      action: {
        client_id: sessionClientId,
        entity: 'session',
        created_at: ctx.now,
        payload: {
          player_id: member.playerId,
          session_type: input.sessionType,
          started_at: ctx.now,
          ended_at: ctx.now,
          duration_minutes: 0,
          reference: input.reference,
          school_id: input.schoolId,
        },
      },
    });

    result.records.push({
      store: 'attendance',
      record: {
        local_id: attendanceClientId,
        client_id: attendanceClientId,
        player_id: member.playerId,
        session_id: sessionClientId,
        day,
      },
    });
    result.actions.push({
      action: {
        client_id: attendanceClientId,
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
