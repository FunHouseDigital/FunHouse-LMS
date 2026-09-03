import type { LocalDataOwner } from './types';

/** Stable render key for one exact authenticated local-data lifecycle. */
export function localDataLifecycleIdentity(owner: LocalDataOwner | null | undefined): string | null {
  return owner ? `${owner.scope}\u0000${owner.generation}\u0000${owner.keyId}` : null;
}
import type { LocalRecord, LocalRecordStore } from '../store/localStore';
import { decryptLocalPersonal } from '../store/localStore';

export function canDisplayPersonalData(owner: LocalDataOwner | null = null): boolean {
  return Boolean(owner?.isCurrent());
}

/** Decrypt a record's owner-bound personal body only for the exact active lifecycle. */
export async function readPersonalData<T = Record<string, unknown>>(
  record: LocalRecord | null | undefined,
  owner: LocalDataOwner | null = null,
  store: LocalRecordStore = 'players',
): Promise<T | null> {
  if (!record?.enc || !owner || !owner.isCurrent()) return null;
  try {
    const personal = await decryptLocalPersonal<T>(store, record, owner);
    return owner.isCurrent() ? personal : null;
  } catch {
    return null;
  }
}
