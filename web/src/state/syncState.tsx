/**
 * Sync status state — the unsynced-items badge, the synced indicator, the
 * stale-device warning, and the surfaced rejected actions (Req 6, 11.4).
 *
 * See design.md "Sync status & stale device". The pure derivations
 * (`isStale`, `deriveSyncStatus`) are exported so they can be property-tested
 * directly (Properties 8 and 9); the React context is a thin binding that reads
 * the Local_Store and re-derives on demand.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import type { StoredSyncAction } from '../domain/types';
import { useAuth } from './authState';
import { sessionScopeKey } from './referenceDataState';
import {
  countUnsynced,
  getActionsByStatus,
  getLastSuccessfulSync,
  getLegacyUnscopedActions,
} from '../store/localStore';

/**
 * Five full days in milliseconds. The stale-device warning fires when the
 * elapsed time since the last successful sync is **strictly greater** than
 * this (Req 6.4).
 */
export const STALE_AFTER_MS = 5 * 24 * 60 * 60 * 1000;

/**
 * True iff `lastSuccessfulSync` is strictly more than 5 full days before `now`
 * (Req 6.4). A `null`/unparseable timestamp is treated as not-yet-stale (there
 * is no "most recent successful sync" to measure against).
 */
export function isStale(lastSuccessfulSync: string | null, now: number = Date.now()): boolean {
  if (!lastSuccessfulSync) return false;
  const last = Date.parse(lastSuccessfulSync);
  if (Number.isNaN(last)) return false;
  return now - last > STALE_AFTER_MS;
}

/** A rejected action surfaced to the user with its stored reason (Req 6.5). */
export interface RejectedItem {
  client_id: string;
  entity: string;
  reason: string | null;
}

export interface SyncStatusView {
  /** Current count of Unsynced_Items (Req 6.1). */
  unsyncedCount: number;
  /** Pre-account-scoping actions quarantined because their owner is unknown. */
  quarantinedCount: number;
  /** Synced indicator: true when nothing is pending or quarantined (Req 6.3). */
  synced: boolean;
  /** Stale-device warning; may coexist with `synced` (Req 6.4). */
  stale: boolean;
  /** Rejected actions with their reasons (Req 6.5). */
  rejected: RejectedItem[];
}

/**
 * Pure derivation of the sync-status view. `synced` and `stale` are independent
 * so they may both be true simultaneously (Req 6.4).
 */
export function deriveSyncStatus(input: {
  unsyncedCount: number;
  lastSuccessfulSync: string | null;
  now?: number;
  rejected?: RejectedItem[];
  quarantinedCount?: number;
}): SyncStatusView {
  const quarantinedCount = input.quarantinedCount ?? 0;
  return {
    unsyncedCount: input.unsyncedCount,
    quarantinedCount,
    synced: input.unsyncedCount === 0 && quarantinedCount === 0,
    stale: isStale(input.lastSuccessfulSync, input.now),
    rejected: input.rejected ?? [],
  };
}

function toRejectedItem(action: StoredSyncAction): RejectedItem {
  return {
    client_id: action.client_id,
    entity: action.entity,
    reason: action.reason ?? null,
  };
}

/** Read the Local_Store and compute the current sync-status view (Req 6). */
export async function readSyncStatus(
  now: number = Date.now(),
  scope?: string | null,
): Promise<SyncStatusView> {
  const [unsyncedCount, lastSync, rejectedActions, quarantinedActions] = await Promise.all([
    countUnsynced(scope),
    getLastSuccessfulSync(scope),
    getActionsByStatus('rejected', scope),
    scope ? getLegacyUnscopedActions() : Promise.resolve([]),
  ]);
  return deriveSyncStatus({
    unsyncedCount,
    lastSuccessfulSync: lastSync,
    now,
    rejected: rejectedActions.map(toRejectedItem),
    quarantinedCount: quarantinedActions.length,
  });
}

export interface SyncStatusContextValue extends SyncStatusView {
  /** Re-read the Local_Store and update the view (call after enqueue/reconcile). */
  refresh: () => Promise<void>;
}

const SyncStatusContext = createContext<SyncStatusContextValue | null>(null);

const EMPTY_VIEW: SyncStatusView = {
  unsyncedCount: 0,
  quarantinedCount: 0,
  synced: true,
  stale: false,
  rejected: [],
};

export interface SyncStatusProviderProps {
  children: ReactNode;
  /** Optional fixed clock for deterministic rendering/tests. */
  now?: () => number;
}

export function SyncStatusProvider({ children, now }: SyncStatusProviderProps) {
  const { session } = useAuth();
  const syncScope = sessionScopeKey(session);
  const [view, setView] = useState<SyncStatusView>(EMPTY_VIEW);

  const refresh = useCallback(async () => {
    const next = await readSyncStatus(now ? now() : Date.now(), syncScope);
    setView(next);
  }, [now, syncScope]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const value = useMemo<SyncStatusContextValue>(() => ({ ...view, refresh }), [view, refresh]);

  return <SyncStatusContext.Provider value={value}>{children}</SyncStatusContext.Provider>;
}

/** Access the sync-status context; throws if used outside its provider. */
export function useSyncStatus(): SyncStatusContextValue {
  const ctx = useContext(SyncStatusContext);
  if (!ctx) {
    throw new Error('useSyncStatus must be used within a SyncStatusProvider');
  }
  return ctx;
}
