/**
 * Capture and sync-recovery services. All automatic and manual triggers share
 * one account-aware flush coordinator, then refresh durable sync health.
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
import { useAuth } from './authState';
import { sessionScopeKey } from './referenceDataState';
import { useSyncStatus } from './syncState';
import {
  SyncEngine,
  SyncScheduler,
  type FlushOutcome,
  type FlushResult,
} from '../domain/syncEngine';
import { countUnsynced } from '../store/localStore';
import { commitCapture } from '../ui/captureCommit';
import type { CaptureResult } from '../domain/captures/types';
import { listenForFlushRequests } from '../pwa/backgroundSync';

export interface CaptureServicesValue {
  scheduler: Pick<SyncScheduler, 'onEnqueue'>;
  /** Persist and enqueue locally; the network nudge continues asynchronously. */
  commit: (result: CaptureResult) => Promise<void>;
  /** Retry only the active account's eligible unsynced actions. */
  retrySync: () => Promise<FlushResult>;
  /** True only while the active account's flush is running. */
  syncing: boolean;
  /** Safe classification of the active account's most recent attempt. */
  lastAttemptOutcome: FlushOutcome | null;
  /** False when the authenticated JWT cannot establish a durable owner scope. */
  scopeAvailable: boolean;
}

const ServicesContext = createContext<CaptureServicesValue | null>(null);

export interface ServicesProviderProps {
  children: ReactNode;
  /** Inject a scheduler in capture-component tests. */
  scheduler?: Pick<SyncScheduler, 'onEnqueue'>;
}

interface Runtime {
  scheduler: Pick<SyncScheduler, 'onEnqueue'>;
  full?: SyncScheduler;
  engine?: SyncEngine;
}

interface ActiveFlush {
  scope: string;
  sessionGeneration: number;
  promise: Promise<FlushResult>;
}

interface AttemptView {
  scope: string | null;
  sessionGeneration: number;
  syncing: boolean;
  outcome: FlushOutcome | null;
}

function emptyFlushResult(): FlushResult {
  return {
    outcome: 'empty',
    attempted: 0,
    applied: 0,
    skipped: 0,
    rejected: 0,
    remainingUnsynced: 0,
  };
}

export function ServicesProvider({ children, scheduler: injected }: ServicesProviderProps) {
  const { client, logout, session } = useAuth();
  const { refresh } = useSyncStatus();
  const syncScope = sessionScopeKey(session);
  const sessionIdentity = session?.access_token ?? null;
  const scopeRef = useRef<string | null>(syncScope);
  const sessionIdentityRef = useRef<string | null>(sessionIdentity);
  const sessionGenerationRef = useRef(0);
  const refreshRef = useRef(refresh);
  const logoutRef = useRef(logout);
  scopeRef.current = syncScope;
  if (sessionIdentityRef.current !== sessionIdentity) {
    sessionIdentityRef.current = sessionIdentity;
    sessionGenerationRef.current += 1;
  }
  const sessionGeneration = sessionGenerationRef.current;
  refreshRef.current = refresh;
  logoutRef.current = logout;

  const runFlushRef = useRef<() => Promise<FlushResult>>(
    async () => emptyFlushResult(),
  );
  const runtimeRef = useRef<Runtime | null>(null);
  const activeFlushRef = useRef<ActiveFlush | null>(null);
  const queuedByScopeRef = useRef(new Map<string, Promise<FlushResult>>());
  const [attempt, setAttempt] = useState<AttemptView>({
    scope: null,
    sessionGeneration: 0,
    syncing: false,
    outcome: null,
  });

  if (runtimeRef.current === null) {
    if (injected) {
      runtimeRef.current = { scheduler: injected };
    } else {
      const engine = new SyncEngine({
        client,
        onUnauthorized: () => {
          const active = activeFlushRef.current;
          if (active?.sessionGeneration === sessionGenerationRef.current) {
            logoutRef.current();
          }
        },
        getScope: () => scopeRef.current,
      });
      const full = new SyncScheduler({
        flush: () => runFlushRef.current(),
        countUnsynced: () => {
          const scope = scopeRef.current;
          return scope ? countUnsynced(scope) : Promise.resolve(0);
        },
      });
      runtimeRef.current = { scheduler: full, full, engine };
    }
  }

  const startFlush = useCallback(
    (scope: string, generation: number): Promise<FlushResult> => {
      const engine = runtimeRef.current?.engine;
      if (
        !engine ||
        scopeRef.current !== scope ||
        sessionGenerationRef.current !== generation
      ) {
        return Promise.resolve(emptyFlushResult());
      }

      const existing = activeFlushRef.current;
      if (
        existing?.scope === scope &&
        existing.sessionGeneration === generation
      ) {
        return existing.promise;
      }

      setAttempt({
        scope,
        sessionGeneration: generation,
        syncing: true,
        outcome: null,
      });
      let settled: FlushResult | null = null;
      let operation!: Promise<FlushResult>;
      operation = engine
        .flush()
        .catch((error: unknown) => ({
          ...emptyFlushResult(),
          outcome: 'network-error' as const,
          error,
        }))
        .then((result) => {
          settled = result;
          return result;
        })
        .finally(async () => {
          const isCurrentSession = () =>
            scopeRef.current === scope &&
            sessionGenerationRef.current === generation;

          if (isCurrentSession()) {
            await refreshRef.current().catch(() => undefined);
          }
          if (
            isCurrentSession() &&
            activeFlushRef.current?.promise === operation
          ) {
            setAttempt({
              scope,
              sessionGeneration: generation,
              syncing: false,
              outcome: settled?.outcome ?? 'network-error',
            });
          }
          // Keep the active slot through status refresh so a same-session
          // trigger cannot start a newer attempt that this completion masks.
          if (activeFlushRef.current?.promise === operation) {
            activeFlushRef.current = null;
          }
        });
      activeFlushRef.current = {
        scope,
        sessionGeneration: generation,
        promise: operation,
      };
      return operation;
    },
    [],
  );

  const runFlush = useCallback((): Promise<FlushResult> => {
    const requestedScope = scopeRef.current;
    const requestedGeneration = sessionGenerationRef.current;
    if (!requestedScope) return Promise.resolve(emptyFlushResult());

    const queueKey = `${requestedGeneration}:${requestedScope}`;
    const queued = queuedByScopeRef.current.get(queueKey);
    if (queued) return queued;

    const active = activeFlushRef.current;
    if (!active) return startFlush(requestedScope, requestedGeneration);
    if (
      active.scope === requestedScope &&
      active.sessionGeneration === requestedGeneration
    ) {
      return active.promise;
    }

    // A replacement session arriving while the old session settles gets one
    // queued run; repeated triggers for the replacement share it.
    let next!: Promise<FlushResult>;
    next = active.promise
      .catch(() => emptyFlushResult())
      .then(() => {
        queuedByScopeRef.current.delete(queueKey);
        if (
          scopeRef.current !== requestedScope ||
          sessionGenerationRef.current !== requestedGeneration
        ) {
          return emptyFlushResult();
        }
        return startFlush(requestedScope, requestedGeneration);
      });
    queuedByScopeRef.current.set(queueKey, next);
    return next;
  }, [startFlush]);
  runFlushRef.current = runFlush;

  // Start automatic online/interval triggers and service-worker requests.
  useEffect(() => {
    const full = runtimeRef.current?.full;
    if (!full) return undefined;
    full.start();
    const unsubscribe = listenForFlushRequests(() => {
      void runFlushRef.current();
    });
    return () => {
      unsubscribe();
      full.stop();
    };
  }, []);

  const value = useMemo<CaptureServicesValue>(() => {
    const scheduler = runtimeRef.current!.scheduler;
    const visibleAttempt =
      syncScope &&
      attempt.scope === syncScope &&
      attempt.sessionGeneration === sessionGeneration
        ? attempt
        : {
            scope: syncScope,
            sessionGeneration,
            syncing: false,
            outcome: null,
          };

    return {
      scheduler,
      scopeAvailable: syncScope !== null,
      syncing: visibleAttempt.syncing,
      lastAttemptOutcome: visibleAttempt.outcome,
      retrySync: runFlush,
      commit: async (result: CaptureResult) => {
        // Never create ownerless production records/actions from a malformed or
        // replaced authenticated session.
        if (
          !syncScope ||
          scopeRef.current !== syncScope ||
          sessionGenerationRef.current !== sessionGeneration
        ) {
          if (!syncScope) logoutRef.current();
          throw new Error('Authenticated sync scope is unavailable');
        }

        // Local persistence and the pending badge complete before any network
        // attempt. The scheduler nudge continues through the shared coordinator.
        await commitCapture(result, { scope: syncScope });
        await refresh();
        void scheduler.onEnqueue().catch(() => {
          void refreshRef.current();
        });
      },
    };
  }, [attempt, refresh, runFlush, sessionGeneration, syncScope]);

  return <ServicesContext.Provider value={value}>{children}</ServicesContext.Provider>;
}

/** Access capture/sync services; throws outside its provider. */
export function useServices(): CaptureServicesValue {
  const ctx = useContext(ServicesContext);
  if (!ctx) throw new Error('useServices must be used within a ServicesProvider');
  return ctx;
}
