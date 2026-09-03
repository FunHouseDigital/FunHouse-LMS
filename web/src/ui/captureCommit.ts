import type { CaptureResult } from '../domain/captures/types';
import type { LocalDataOwner } from '../domain/types';
import {
  commitPreparedCapture,
  prepareLocalRecord,
  prepareQueueAction,
  type LocalRecord,
  type PreparedLocalWrite,
} from '../store/localStore';
import { notifyPlayerDirectoryChanged, type SyncScheduler } from '../domain/syncEngine';

export interface CommitDeps {
  scheduler?: Pick<SyncScheduler, 'onEnqueue'>;
  owner: LocalDataOwner;
}

/**
 * Pre-encrypt the complete queue and every personal record body outside an IDB
 * transaction, revalidate the immutable auth lifecycle, then atomically commit
 * all touched local-record and queue rows. Any missing/stale key fails before
 * mutation; scheduling and notifications happen only after `tx.done`.
 */
export async function commitCapture(result: CaptureResult, deps: CommitDeps): Promise<void> {
  const owner = deps.owner;
  if (!owner || !owner.isCurrent()) {
    throw new Error('Authenticated local-data owner key is unavailable');
  }

  const records: PreparedLocalWrite[] = await Promise.all(
    result.records.map(async (captureRecord) => {
      const record: LocalRecord = {
        ...captureRecord.record,
        sync_scope: owner.scope,
      };
      return prepareLocalRecord(
        captureRecord.store,
        record,
        captureRecord.personal,
        owner,
      );
    }),
  );
  const actions = await Promise.all(
    result.actions.map((captureAction) =>
      prepareQueueAction(captureAction.action, captureAction.status, owner),
    ),
  );

  if (!owner.isCurrent()) {
    throw new Error('Authenticated local-data owner changed before capture commit');
  }
  await commitPreparedCapture(records, actions, owner);

  if (records.some((item) => item.store === 'players')) {
    notifyPlayerDirectoryChanged(owner.scope);
  }
  if (deps.scheduler) await deps.scheduler.onEnqueue();
}
