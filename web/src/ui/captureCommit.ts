/**
 * Capture commit helper (Req 4.1, 4.2, 17.1). The single effectful step shared
 * by every capture screen: persist the builder's local record(s) — encrypting
 * personal-data fields at rest via the Crypto service — enqueue its
 * Sync_Action(s), and nudge the Sync_Engine.
 *
 * This is deliberately separate from the pure builders so the builders stay
 * effect-free (and property-testable) while all I/O lives here behind an
 * injectable seam. The capture path performs **no network call** on its own
 * (Req 4.4, 7.8, ...): `scheduler.onEnqueue()` only reaches the network when the
 * device is online, and the Sync_Engine is the sync path, not the capture path.
 */
import type { CaptureResult } from '../domain/captures/types';
import { encryptPayload, getSessionKey } from '../domain/crypto';
import {
  enqueueAction,
  writeLocalRecord,
  type LocalRecord,
} from '../store/localStore';
import {
  notifyPlayerDirectoryChanged,
  type SyncScheduler,
} from '../domain/syncEngine';

export interface CommitDeps {
  /** The Sync_Engine scheduler; `onEnqueue` registers/flushes (Req 5.1, 5.2). */
  scheduler?: Pick<SyncScheduler, 'onEnqueue'>;
  /**
   * The in-memory AES session key. Defaults to the Crypto service's current key.
   * Pass `null` explicitly to force the no-key path in tests.
   */
  sessionKey?: CryptoKey | null;
  /** Authenticated account/location/school scope owning persisted actions. */
  scope?: string | null;
}

/**
 * Persist + enqueue a capture result (write-before-confirm, Req 4.1). For each
 * record carrying `personal` data, the sensitive payload is AES-GCM encrypted
 * into an `enc` field (Req 17.1) while non-sensitive index keys stay in clear.
 * When no session key is available the personal fields are omitted rather than
 * written in plaintext (POPIA fail-safe, Req 17.1/17.2).
 */
export async function commitCapture(result: CaptureResult, deps: CommitDeps = {}): Promise<void> {
  const key = deps.sessionKey !== undefined ? deps.sessionKey : getSessionKey();

  for (const captureRecord of result.records) {
    const record: LocalRecord = {
      ...captureRecord.record,
      ...(deps.scope ? { sync_scope: deps.scope } : {}),
    };
    if (captureRecord.personal) {
      if (key) {
        record.enc = await encryptPayload(key, captureRecord.personal);
      }
      // No key → do not persist personal fields in the clear (fail-safe).
    }
    await writeLocalRecord(captureRecord.store, record);
    if (captureRecord.store === 'players') {
      notifyPlayerDirectoryChanged(deps.scope ?? null);
    }
  }

  for (const captureAction of result.actions) {
    await enqueueAction(captureAction.action, {
      ...(captureAction.status ? { status: captureAction.status } : {}),
      ...(deps.scope ? { scope: deps.scope } : {}),
    });
  }

  if (deps.scheduler) {
    await deps.scheduler.onEnqueue();
  }
}
