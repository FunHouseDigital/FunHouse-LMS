/**
 * Capture services state — wires the Sync_Engine + scheduler into the React tree
 * and exposes a single `commit(result)` for the capture screens (Req 4, 5).
 *
 * It builds a {@link SyncEngine} over the auth-wired Container_API client and a
 * {@link SyncScheduler} for background-sync registration + the online/interval
 * fallback (task 8.10). After each commit it refreshes the sync-status view so
 * the unsynced badge updates immediately (Req 6.2, 11.4).
 *
 * Tests may inject a `scheduler` (and thus a mocked client) so no real network
 * is touched.
 */
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  type ReactNode,
} from 'react';
import { useAuth } from './authState';
import { useSyncStatus } from './syncState';
import { SyncEngine, SyncScheduler, type FlushResult } from '../domain/syncEngine';
import { countUnsynced } from '../store/localStore';
import { commitCapture, type CommitDeps } from '../ui/captureCommit';
import type { CaptureResult } from '../domain/captures/types';
import { listenForFlushRequests } from '../pwa/backgroundSync';

export interface CaptureServicesValue {
  scheduler: Pick<SyncScheduler, 'onEnqueue'>;
  /** Persist + enqueue a capture result, then refresh the sync badge. */
  commit: (result: CaptureResult) => Promise<void>;
}

const ServicesContext = createContext<CaptureServicesValue | null>(null);

export interface ServicesProviderProps {
  children: ReactNode;
  /** Inject a scheduler (tests); a default is built from the auth client otherwise. */
  scheduler?: Pick<SyncScheduler, 'onEnqueue'>;
}

export function ServicesProvider({ children, scheduler: injected }: ServicesProviderProps) {
  const { client, authManager } = useAuth();
  const { refresh } = useSyncStatus();

  const ref = useRef<{
    scheduler: Pick<SyncScheduler, 'onEnqueue'>;
    full?: SyncScheduler;
    flush?: () => Promise<FlushResult>;
  } | null>(null);
  if (ref.current === null) {
    if (injected) {
      ref.current = { scheduler: injected };
    } else {
      const engine = new SyncEngine({
        client,
        onUnauthorized: () => authManager.handleUnauthorized(),
      });
      const flush = () => engine.flush();
      const full = new SyncScheduler({
        flush,
        countUnsynced,
      });
      ref.current = { scheduler: full, full, flush };
    }
  }

  // Start/stop the fallback loop for the default (non-injected) scheduler, and
  // subscribe to Background Sync flush requests from the service worker (Req 5.2):
  // when the SW's `funhouse-sync` handler messages the page, run a flush here
  // where the Local_Store + bearer token live.
  useEffect(() => {
    const full = ref.current?.full;
    const flush = ref.current?.flush;
    if (full) {
      full.start();
      const unsubscribe = listenForFlushRequests(() => {
        void flush?.();
      });
      return () => {
        unsubscribe();
        full.stop();
      };
    }
    return undefined;
  }, []);

  const value = useMemo<CaptureServicesValue>(() => {
    const scheduler = ref.current!.scheduler;
    const deps: CommitDeps = { scheduler };
    return {
      scheduler,
      commit: async (result: CaptureResult) => {
        await commitCapture(result, deps);
        await refresh();
      },
    };
  }, [refresh]);

  return <ServicesContext.Provider value={value}>{children}</ServicesContext.Provider>;
}

/** Access the capture services; throws if used outside a {@link ServicesProvider}. */
export function useServices(): CaptureServicesValue {
  const ctx = useContext(ServicesContext);
  if (!ctx) {
    throw new Error('useServices must be used within a ServicesProvider');
  }
  return ctx;
}
