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
import type { StoredSyncAction } from '../domain/types';
import { useAuth } from './authState';
import { sessionScopeKey } from './referenceDataState';
import {
  countUnsynced,
  getActionsByStatus,
  getLastSuccessfulSync,
  getLegacyUnscopedActions,
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
    synced: input.unsyncedCount === 0,
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
  scope?: string | null,
): Promise<SyncStatusView> {
  if (scope === null) return deriveSyncStatus({ unsyncedCount: 0, lastSuccessfulSync: null, now });

  const [unsyncedCount, lastSync, rejectedActions, blockedActions, quarantinedGroups] =
    await Promise.all([
      countUnsynced(scope),
      getLastSuccessfulSync(scope),
      getActionsByStatus('rejected', scope),
      getActionsByStatus('blocked', scope),
      scope
        ? Promise.all([
            getLegacyUnscopedActions('unsynced'),
            getLegacyUnscopedActions('rejected'),
            getLegacyUnscopedActions('blocked'),
          ])
        : Promise.resolve([] as StoredSyncAction[][]),
    ]);

  return deriveSyncStatus({
    unsyncedCount,
    lastSuccessfulSync: lastSync,
    now,
    rejected: rejectedActions.map(toRejectedItem),
    blockedCount: blockedActions.length,
    quarantinedCount: quarantinedGroups.reduce((total, actions) => total + actions.length, 0),
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
  scope: string | null;
  view: SyncStatusView;
}

export interface SyncStatusProviderProps {
  children: ReactNode;
  /** Optional fixed clock for deterministic rendering/tests. */
  now?: () => number;
}

export function SyncStatusProvider({ children, now }: SyncStatusProviderProps) {
  const { session } = useAuth();
  const syncScope = sessionScopeKey(session);
  const [stored, setStored] = useState<ScopedView>({ scope: null, view: EMPTY_VIEW });
  const activeScope = useRef<string | null>(syncScope);
  const requestGeneration = useRef(0);

  // Invalidate old-account reads during render, before passive-effect cleanup.
  if (activeScope.current !== syncScope) {
    activeScope.current = syncScope;
    requestGeneration.current += 1;
  }

  const refresh = useCallback(async () => {
    const requestScope = syncScope;
    const generation = ++requestGeneration.current;
    if (!requestScope) {
      setStored({ scope: null, view: EMPTY_VIEW });
      return;
    }

    const next = await readSyncStatus(now ? now() : Date.now(), requestScope);
    if (
      generation !== requestGeneration.current ||
      activeScope.current !== requestScope
    ) {
      return;
    }
    setStored({ scope: requestScope, view: next });
  }, [now, syncScope]);

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

  const loading = syncScope !== null && stored.scope !== syncScope;
  const visible = !loading && syncScope ? stored.view : EMPTY_VIEW;
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
