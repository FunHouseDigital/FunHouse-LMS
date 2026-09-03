/**
 * Account-scoped sync health derived from the durable Local_Store (Req 6, 11.4).
 * Queue payloads never enter this context: only counts, safe rejection summaries,
 * and the last successful server contact are exposed to the shared app shell.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { localDataLifecycleIdentity } from '../domain/personalData';
import type { LocalDataOwner, StoredSyncAction } from '../domain/types';
import { useAuth } from './authState';
import { sessionScopeKey } from './referenceDataState';
import {
  countActionsByStatus,
  countUnsynced,
  getActionsByStatus,
  getLastSuccessfulSync,
  countQuarantinedLegacyData,
} from '../store/localStore';

/** Five full days in milliseconds (Req 6.4). */
export const STALE_AFTER_MS = 5 * 24 * 60 * 60 * 1000;

/** True only when a parseable successful-sync timestamp is over five days old. */
export function isStale(lastSuccessfulSync: string | null, now: number = Date.now()): boolean {
  if (!lastSuccessfulSync) return false;
  const last = Date.parse(lastSuccessfulSync);
  if (Number.isNaN(last)) return false;
  return now - last > STALE_AFTER_MS;
}

/** Payload-free rejection detail safe for the status surface. */
export interface RejectedItem {
  entity: string;
  reason: string | null;
}

export interface SyncStatusView {
  /** Current account actions still eligible for retry. */
  unsyncedCount: number;
  /** Current account actions held by this app version. */
  blockedCount: number;
  /** Unknown-owner legacy actions; count-only and never attributed or retried. */
  quarantinedCount: number;
  /** True when the current account has no retryable pending actions. */
  synced: boolean;
  /** Independent warning based on the current account's last server contact. */
  stale: boolean;
  /** Current account terminal rejections, without payloads or client ids. */
  rejected: RejectedItem[];
  /** Current account's last successful server contact. */
  lastSuccessfulSync: string | null;
}

/** Pure sync-status derivation retained for property testing. */
export function deriveSyncStatus(input: {
  unsyncedCount: number;
  lastSuccessfulSync: string | null;
  now?: number;
  rejected?: RejectedItem[];
  blockedCount?: number;
  quarantinedCount?: number;
}): SyncStatusView {
  return {
    unsyncedCount: input.unsyncedCount,
    blockedCount: input.blockedCount ?? 0,
    quarantinedCount: input.quarantinedCount ?? 0,
    synced: input.unsyncedCount === 0 && (input.quarantinedCount ?? 0) === 0,
    stale: isStale(input.lastSuccessfulSync, input.now),
    rejected: input.rejected ?? [],
    lastSuccessfulSync: input.lastSuccessfulSync,
  };
}

function toRejectedItem(action: StoredSyncAction): RejectedItem {
  return {
    entity: action.entity,
    reason: action.reason ?? null,
  };
}

/**
 * Read durable sync health. `undefined` retains the all-queue behavior used by
 * low-level tests; explicit `null` fails closed and reads no account data.
 */
export async function readSyncStatus(
  now: number = Date.now(),
  owner: LocalDataOwner | null = null,
): Promise<SyncStatusView> {
  if (!owner || !owner.isCurrent()) return deriveSyncStatus({ unsyncedCount: 0, lastSuccessfulSync: null, now });

  const [unsyncedCount, lastSync, rejectedCount, blockedCount, quarantinedCount] =
    await Promise.all([
      countUnsynced(owner),
      getLastSuccessfulSync(owner),
      countActionsByStatus(owner, 'rejected'),
      countActionsByStatus(owner, 'blocked'),
      countQuarantinedLegacyData(),
    ]);
  if (!owner.isCurrent()) return deriveSyncStatus({ unsyncedCount: 0, lastSuccessfulSync: null, now });
  const rejectedActions = await getActionsByStatus('rejected', owner);
  if (!owner.isCurrent()) return deriveSyncStatus({ unsyncedCount: 0, lastSuccessfulSync: null, now });

  return deriveSyncStatus({
    unsyncedCount,
    lastSuccessfulSync: lastSync,
    now,
    rejected: rejectedActions.slice(0, rejectedCount).map(toRejectedItem),
    blockedCount,
    quarantinedCount,
  });
}

export interface SyncStatusContextValue extends SyncStatusView {
  /** True until the active account's first durable status read completes. */
  loading: boolean;
  /** Re-read durable state after enqueue or any reconcile attempt. */
  refresh: () => Promise<void>;
}

const SyncStatusContext = createContext<SyncStatusContextValue | null>(null);

const EMPTY_VIEW: SyncStatusView = {
  unsyncedCount: 0,
  blockedCount: 0,
  quarantinedCount: 0,
  synced: true,
  stale: false,
  rejected: [],
  lastSuccessfulSync: null,
};

interface ScopedView {
  identity: string | null;
  view: SyncStatusView;
}

export interface SyncStatusProviderProps {
  children: ReactNode;
  /** Optional fixed clock for deterministic rendering/tests. */
  now?: () => number;
}

export function SyncStatusProvider({ children, now }: SyncStatusProviderProps) {
  const { session, localDataOwner } = useAuth();
  const syncScope = sessionScopeKey(session);
  const lifecycleIdentity = localDataLifecycleIdentity(localDataOwner);
  const [stored, setStored] = useState<ScopedView>({ identity: null, view: EMPTY_VIEW });
  const activeIdentity = useRef<string | null>(lifecycleIdentity);
  const requestGeneration = useRef(0);

  // Invalidate old-account reads during render, before passive-effect cleanup.
  if (activeIdentity.current !== lifecycleIdentity) {
    activeIdentity.current = lifecycleIdentity;
    requestGeneration.current += 1;
  }

  const refresh = useCallback(async () => {
    const requestIdentity = lifecycleIdentity;
    const generation = ++requestGeneration.current;
    if (!requestIdentity || !localDataOwner) {
      setStored({ identity: null, view: EMPTY_VIEW });
      return;
    }

    const next = await readSyncStatus(now ? now() : Date.now(), localDataOwner);
    if (
      generation !== requestGeneration.current ||
      activeIdentity.current !== requestIdentity ||
      !localDataOwner.isCurrent()
    ) {
      return;
    }
    setStored({ identity: requestIdentity, view: next });
  }, [lifecycleIdentity, localDataOwner, now]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Re-evaluate the five-day threshold while the shell remains mounted.
  useEffect(() => {
    if (!syncScope || now) return undefined;
    const timer = setInterval(() => {
      void refresh();
    }, 60_000);
    return () => clearInterval(timer);
  }, [now, refresh, syncScope]);

  const loading = lifecycleIdentity !== null && stored.identity !== lifecycleIdentity;
  const visible = !loading && lifecycleIdentity && localDataOwner?.isCurrent() ? stored.view : EMPTY_VIEW;
  const value = useMemo<SyncStatusContextValue>(
    () => ({ ...visible, loading, refresh }),
    [loading, refresh, visible],
  );

  return <SyncStatusContext.Provider value={value}>{children}</SyncStatusContext.Provider>;
}

/** Access the sync-status context; throws outside its provider. */
export function useSyncStatus(): SyncStatusContextValue {
  const ctx = useContext(SyncStatusContext);
  if (!ctx) throw new Error('useSyncStatus must be used within a SyncStatusProvider');
  return ctx;
}
