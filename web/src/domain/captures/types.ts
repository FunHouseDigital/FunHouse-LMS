/**
 * Shared vocabulary for the capture builders (Req 4, 7, 10, 12, 14, 15).
 *
 * Every capture flow is implemented as a **pure** builder
 * `buildActions(input, ctx) → CaptureResult` (see design.md "Capture flows /
 * components"). The builder produces the local domain record(s) to persist and
 * the Sync_Action(s) to enqueue, but performs **no I/O itself** — it never
 * touches IndexedDB, the network, or `Date.now()`/`crypto.randomUUID()`
 * directly. Those effects are supplied through the injected {@link CaptureContext},
 * which makes the builders deterministic and trivially property-testable
 * (Properties 2, 4, 15, 16, 18, 19).
 *
 * The thin React screen consuming a builder is responsible for the effects:
 * writing each record (encrypting personal fields at rest via the Crypto
 * service), enqueuing each action, and nudging the Sync_Engine — see
 * {@link ../../ui/captureCommit}.
 */
import type { LocalRecordStore } from '../../store/localStore';
import type { LocalRecord } from '../../store/localStore';
import type { SyncAction, SyncStatus } from '../types';

/**
 * The injected effect surface a builder needs. Supplying `now` and `newId`
 * (rather than reading the clock / RNG inside the builder) keeps every builder
 * pure and deterministic for property tests.
 */
export interface CaptureContext {
  /** Device capture time as an ISO-8601 string (used for `created_at`, Req 4.2). */
  now: string;
  /** Unique id generator for `client_id`/`local_id` (device-unique, Req 4.3). */
  newId: () => string;
}

/**
 * A local domain record to persist, tagged with its destination store and an
 * optional `personal` sub-object holding personal-data fields that MUST be
 * encrypted at rest before the write (Req 17.1). Non-sensitive index keys
 * (`local_id`, `client_id`, `player_id`, `day`, `session_id`) live in `record`
 * in clear so IndexedDB indexing still works.
 */
export interface CaptureRecord {
  store: LocalRecordStore;
  record: LocalRecord;
  /** Personal-data fields to encrypt at rest (Req 17.1). */
  personal?: Record<string, unknown>;
}

/** A Sync_Action to enqueue, with the local queue status it should carry. */
export interface CaptureAction {
  action: SyncAction;
  /** Defaults to `unsynced`; `blocked` for forward-compatible entities (D1). */
  status?: SyncStatus;
}

/** The pure output of a capture builder: what to persist and what to enqueue. */
export interface CaptureResult {
  records: CaptureRecord[];
  actions: CaptureAction[];
}

/** Derive the local capture day (`YYYY-MM-DD`) from an ISO timestamp. */
export function dayOf(iso: string): string {
  return iso.slice(0, 10);
}
