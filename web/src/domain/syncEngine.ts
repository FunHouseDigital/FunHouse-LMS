/**
 * Sync_Engine — flush the durable Sync_Queue to `POST /sync`, reconcile the
 * per-action results, and drive retry/background-sync behaviour (Req 5, 6).
 *
 * See design.md "Sync_Engine", "Idempotency & retry safety", "Background sync
 * & fallback", and "Dependency D2 (local-id resolution)".
 *
 * The engine is a thin orchestrator over the Local_Store (queue I/O) and the
 * Container_API client (the only network edge). All business rules live here so
 * they are exercised directly by the property-based tests against a mocked API
 * and fake-indexeddb.
 *
 * ### Reconcile contract (Req 5.3–5.7)
 * On a `200` response each result is matched to its stored action **by
 * `client_id`**:
 *  - `applied` / `skipped` → status updated, removed from the unsynced set.
 *  - `rejected`            → retained locally with its `reason`, excluded from
 *                            future batches (Req 5.6, 6.5).
 *  - no matching result    → left `unsynced` and retried later (Req 5.3).
 * `created_at` and `client_id` are copied **verbatim** into every batch and are
 * never regenerated (Req 5.7). On a network/non-200 failure the whole affected
 * set is retained and only `attempt_count` is bumped (Req 5.5).
 *
 * ### Dependency D2 — local-id resolution
 * A player registered offline has no server id yet, so dependent
 * `consent`/`session`/`payment`/`entitlement` actions reference the **player
 * action's `client_id`** as their `payload.player_id`. The engine sends player
 * actions first; once a `player` action is `applied` (returning `record_id`) it
 * rewrites those dependents' `player_id` to the server `record_id` before they
 * are transmitted — within the same flush, or a later one. The local→server
 * mapping is also persisted in `meta` so late-captured dependents resolve too.
 */
import type { StoredSyncAction, SyncAction, SyncResult } from './types';
import { UnauthorizedError } from '../api/client';
import type { ContainerApiClient } from '../api/client';
import {
  countUnsynced,
  getAction,
  getActionsByStatus,
  getMeta,
  getUnsyncedActions,
  putAction,
  setLastSuccessfulSync,
  setMeta,
  updateActionStatus,
} from '../store/localStore';

/** The Background Sync registration tag (Req 5.2). */
export const BACKGROUND_SYNC_TAG = 'funhouse-sync';

/** `meta` key holding the local-player-id → server-record-id map (D2). */
export const RESOLUTION_META_KEY = 'player_id_resolutions';
/** `meta` key holding local-session-id → server-record-id mappings. */
export const SESSION_RESOLUTION_META_KEY = 'session_id_resolutions';
/** Browser event used to invalidate mounted player rosters after local changes. */
export const PLAYER_DIRECTORY_CHANGED_EVENT = 'funhouse-player-directory-changed';

interface PlayerDirectoryChangedDetail {
  scope: string | null;
}

/** Notify mounted player consumers that local identity state changed. */
export function notifyPlayerDirectoryChanged(scope: string | null): void {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(
    new CustomEvent<PlayerDirectoryChangedDetail>(PLAYER_DIRECTORY_CHANGED_EVENT, {
      detail: { scope },
    }),
  );
}

/** Subscribe to player-directory changes for one authenticated owner. */
export function subscribePlayerDirectoryChanged(
  scope: string | null,
  listener: () => void,
): () => void {
  if (typeof window === 'undefined') return () => undefined;
  const handle = (event: Event) => {
    const changed = event as CustomEvent<PlayerDirectoryChangedDetail>;
    if (changed.detail?.scope === scope) listener();
  };
  window.addEventListener(PLAYER_DIRECTORY_CHANGED_EVENT, handle);
  return () => window.removeEventListener(PLAYER_DIRECTORY_CHANGED_EVENT, handle);
}

/** Build the account-scoped player resolution metadata key. */
export function playerResolutionMetaKey(scope: string | null): string {
  return scope ? `${RESOLUTION_META_KEY}:${scope}` : RESOLUTION_META_KEY;
}

/** Build the account-scoped session resolution metadata key. */
export function sessionResolutionMetaKey(scope: string | null): string {
  return scope
    ? `${SESSION_RESOLUTION_META_KEY}:${scope}`
    : SESSION_RESOLUTION_META_KEY;
}

/** Outcome classification for a `flush()` attempt. */
export type FlushOutcome = 'ok' | 'empty' | 'network-error' | 'unauthorized';

export interface FlushResult {
  outcome: FlushOutcome;
  /** Number of actions transmitted (across both D2 phases). */
  attempted: number;
  applied: number;
  skipped: number;
  rejected: number;
  /** Count of actions still `unsynced` after the attempt. */
  remainingUnsynced: number;
  /** The underlying error for `network-error` / `unauthorized` outcomes. */
  error?: unknown;
}

interface Agg {
  attempted: number;
  applied: number;
  skipped: number;
  rejected: number;
}

interface TransmitOutcome {
  outcome: 'ok' | 'network-error' | 'unauthorized';
  /** local player-action `client_id` → server `record_id` for applied players. */
  playerResolutions: Record<string, string>;
  /** local session-action `client_id` → server `record_id` for applied sessions. */
  sessionResolutions: Record<string, string>;
  error?: unknown;
}

function emptyAgg(): Agg {
  return { attempted: 0, applied: 0, skipped: 0, rejected: 0 };
}

/** The player reference of an action: the mirror, else `payload.player_id`. */
function playerRefOf(action: StoredSyncAction): string | undefined {
  if (typeof action.player_id === 'string') return action.player_id;
  const payload = action.payload as Record<string, unknown> | undefined;
  const pid = payload?.player_id;
  return typeof pid === 'string' ? pid : undefined;
}

/** The session reference carried by an attendance action, when present. */
function sessionRefOf(action: StoredSyncAction): string | undefined {
  if (action.entity !== 'attendance') return undefined;
  const payload = action.payload as Record<string, unknown> | undefined;
  const sessionId = payload?.session_id;
  return typeof sessionId === 'string' ? sessionId : undefined;
}

export interface SyncEngineConfig {
  client: Pick<ContainerApiClient, 'sync'>;
  /** Invoked when a `401` surfaces so the Auth_Manager can clear the JWT (Req 1.7). */
  onUnauthorized?: () => void;
  /** Current authenticated account/location/school owner of queued actions. */
  getScope?: () => string | null;
}

export class SyncEngine {
  private readonly client: Pick<ContainerApiClient, 'sync'>;
  private readonly onUnauthorized?: () => void;
  private readonly getScope?: () => string | null;
  private readonly scoped: boolean;
  /** Serialise flushes so concurrent triggers (online + interval) don't race. */
  private inFlight: Promise<FlushResult> | null = null;

  constructor(config: SyncEngineConfig) {
    this.client = config.client;
    this.onUnauthorized = config.onUnauthorized;
    this.getScope = config.getScope;
    this.scoped = config.getScope !== undefined;
  }

  /**
   * Flush all `unsynced` actions to `POST /sync` and reconcile the results.
   * Safe to call repeatedly; overlapping calls share the in-flight promise.
   */
  flush(): Promise<FlushResult> {
    if (this.inFlight) return this.inFlight;
    const run = this.doFlush().finally(() => {
      this.inFlight = null;
    });
    this.inFlight = run;
    return run;
  }

  private emptyResult(): FlushResult {
    return {
      outcome: 'empty',
      attempted: 0,
      applied: 0,
      skipped: 0,
      rejected: 0,
      remainingUnsynced: 0,
    };
  }

  /** True while the authenticated owner captured at flush start is unchanged. */
  private isScopeCurrent(scope: string | null): boolean {
    return !this.scoped || (this.getScope?.() ?? null) === scope;
  }

  private async doFlush(): Promise<FlushResult> {
    const syncScope = this.getScope?.() ?? null;
    // In production a missing scope means logout/session replacement. Never
    // fall back to the unscoped queue, which could belong to another account.
    if (this.scoped && syncScope === null) {
      return this.emptyResult();
    }

    // Apply any resolutions learned in a previous flush before batching (D2).
    await this.applyStoredResolutions(syncScope);
    await this.applyStoredSessionResolutions(syncScope);
    if (!this.isScopeCurrent(syncScope)) return this.emptyResult();

    const unsynced = await getUnsyncedActions(syncScope);
    if (unsynced.length === 0) return this.emptyResult();

    // D2: dependents referencing a player action still queued locally are
    // deferred until that player is applied (and its id resolved).
    const localPlayerIds = new Set(
      unsynced.filter((a) => a.entity === 'player').map((a) => a.client_id),
    );
    const localSessionIds = new Set(
      unsynced.filter((a) => a.entity === 'session').map((a) => a.client_id),
    );

    const phase1: StoredSyncAction[] = [];
    const deferred: StoredSyncAction[] = [];
    for (const action of unsynced) {
      const pid = playerRefOf(action);
      const sessionId = sessionRefOf(action);
      const waitsForPlayer =
        action.entity !== 'player' && pid !== undefined && localPlayerIds.has(pid);
      const waitsForSession =
        sessionId !== undefined && localSessionIds.has(sessionId);
      if (waitsForPlayer || waitsForSession) {
        deferred.push(action);
      } else {
        phase1.push(action);
      }
    }

    const agg = emptyAgg();

    // Phase 1: players + independent actions, ordered by created_at/client_id.
    const t1 = await this.transmit(phase1, agg, syncScope);
    if (t1.outcome !== 'ok') {
      return this.failureResult(agg, t1, syncScope);
    }

    // Resolve dependents from phase-1 player/session applies, then send them.
    if (Object.keys(t1.playerResolutions).length > 0) {
      await this.persistResolutions(t1.playerResolutions, syncScope);
      await this.applyStoredResolutions(syncScope);
    }
    if (Object.keys(t1.sessionResolutions).length > 0) {
      await this.persistSessionResolutions(t1.sessionResolutions, syncScope);
      await this.applyStoredSessionResolutions(syncScope);
    }
    if (!this.isScopeCurrent(syncScope)) {
      return this.failureResult(
        agg,
        this.scopeChangedOutcome(),
        syncScope,
      );
    }

    const phase2: StoredSyncAction[] = [];
    for (const action of deferred) {
      const fresh = await getAction(action.client_id);
      if (!fresh || fresh.status !== 'unsynced') continue;
      const pid = playerRefOf(fresh);
      const sessionId = sessionRefOf(fresh);
      // Still references an unresolved local producer → wait for a later phase/flush.
      if (pid !== undefined && localPlayerIds.has(pid)) continue;
      if (sessionId !== undefined && localSessionIds.has(sessionId)) continue;
      phase2.push(fresh);
    }

    if (phase2.length > 0) {
      const t2 = await this.transmit(phase2, agg, syncScope);
      if (t2.outcome !== 'ok') {
        return this.failureResult(agg, t2, syncScope);
      }
      if (Object.keys(t2.playerResolutions).length > 0) {
        await this.persistResolutions(t2.playerResolutions, syncScope);
      }
      if (Object.keys(t2.sessionResolutions).length > 0) {
        await this.persistSessionResolutions(t2.sessionResolutions, syncScope);
        await this.applyStoredSessionResolutions(syncScope);
      }
    }

    return {
      outcome: 'ok',
      ...agg,
      remainingUnsynced: await countUnsynced(syncScope),
    };
  }

  private async failureResult(
    agg: Agg,
    t: TransmitOutcome,
    scope: string | null,
  ): Promise<FlushResult> {
    return {
      outcome: t.outcome,
      ...agg,
      remainingUnsynced: await countUnsynced(scope),
      error: t.error,
    };
  }

  /**
   * Transmit a set of actions and reconcile the response. Mutates `agg` with the
   * per-status tallies. On any transport failure the affected actions are
   * retained `unsynced` with a bumped `attempt_count` (Req 5.5).
   */
  private async transmit(
    actions: StoredSyncAction[],
    agg: Agg,
    scope: string | null,
  ): Promise<TransmitOutcome> {
    if (actions.length === 0) {
      return { outcome: 'ok', playerResolutions: {}, sessionResolutions: {} };
    }
    if (!this.isScopeCurrent(scope)) return this.scopeChangedOutcome();

    // Build the wire batch, copying created_at/client_id verbatim (Req 5.7).
    const batch: SyncAction[] = actions.map((a) => ({
      client_id: a.client_id,
      entity: a.entity,
      created_at: a.created_at,
      payload: a.payload,
    }));

    let result: SyncResult;
    try {
      // The scope check and client call are synchronous up to token capture, so
      // a different account cannot supply credentials for this batch.
      result = await this.client.sync(batch);
    } catch (err) {
      if (!this.isScopeCurrent(scope)) return this.scopeChangedOutcome(err);
      await this.retainAll(actions);
      if (err instanceof UnauthorizedError) {
        this.onUnauthorized?.();
        return {
          outcome: 'unauthorized',
          playerResolutions: {},
          sessionResolutions: {},
          error: err,
        };
      }
      return {
        outcome: 'network-error',
        playerResolutions: {},
        sessionResolutions: {},
        error: err,
      };
    }
    // If logout/account replacement happened while the request was in flight,
    // leave local reconciliation for a later flush under the captured owner.
    if (!this.isScopeCurrent(scope)) return this.scopeChangedOutcome();

    agg.attempted += actions.length;
    const byId = new Map(result.results.map((r) => [r.client_id, r]));
    const playerResolutions: Record<string, string> = {};
    const sessionResolutions: Record<string, string> = {};

    for (const action of actions) {
      const r = byId.get(action.client_id);
      if (!r) {
        // Submitted action with no matching result → still unsynced, retry (Req 5.3).
        await this.bumpAttempt(action.client_id);
        continue;
      }
      if (r.status === 'applied' || r.status === 'skipped') {
        await updateActionStatus(action.client_id, r.status);
        if (r.status === 'applied') agg.applied += 1;
        else agg.skipped += 1;
        if (action.entity === 'player' && r.record_id) {
          playerResolutions[action.client_id] = r.record_id;
        }
        if (action.entity === 'session' && r.record_id) {
          sessionResolutions[action.client_id] = r.record_id;
        }
      } else {
        // rejected → retain locally with reason, exclude from future batches (Req 5.6).
        await updateActionStatus(action.client_id, 'rejected', r.reason ?? undefined);
        agg.rejected += 1;
      }
    }

    // A `200` response (even one with rejections) is a successful reach of the
    // server → advance the last-successful-sync marker (Req 6.4 basis).
    await setLastSuccessfulSync(new Date().toISOString(), scope);
    return { outcome: 'ok', playerResolutions, sessionResolutions };
  }

  private scopeChangedOutcome(error?: unknown): TransmitOutcome {
    return {
      outcome: 'network-error',
      playerResolutions: {},
      sessionResolutions: {},
      error: error ?? new Error('Authenticated sync scope changed during flush'),
    };
  }

  /** Retain every still-unsynced action, bumping its attempt counter (Req 5.5). */
  private async retainAll(actions: StoredSyncAction[]): Promise<void> {
    for (const action of actions) {
      await this.bumpAttempt(action.client_id);
    }
  }

  private async bumpAttempt(clientId: string): Promise<void> {
    const fresh = await getAction(clientId);
    if (fresh && fresh.status === 'unsynced') {
      fresh.attempt_count += 1;
      await putAction(fresh);
    }
  }

  private resolutionMetaKey(scope: string | null): string {
    return playerResolutionMetaKey(scope);
  }

  private sessionResolutionMetaKey(scope: string | null): string {
    return sessionResolutionMetaKey(scope);
  }

  /** Merge new local→server player-id mappings into the persisted resolution map. */
  private async persistResolutions(
    newOnes: Record<string, string>,
    scope: string | null,
  ): Promise<void> {
    const key = this.resolutionMetaKey(scope);
    const map = (await getMeta<Record<string, string>>(key)) ?? {};
    Object.assign(map, newOnes);
    await setMeta(key, map);
    notifyPlayerDirectoryChanged(scope);
  }

  /** Merge local→server session-id mappings into scoped metadata. */
  private async persistSessionResolutions(
    newOnes: Record<string, string>,
    scope: string | null,
  ): Promise<void> {
    const key = this.sessionResolutionMetaKey(scope);
    const map = (await getMeta<Record<string, string>>(key)) ?? {};
    Object.assign(map, newOnes);
    await setMeta(key, map);
  }

  /**
   * Rewrite any unsynced dependent action whose `player_id` matches a known
   * local→server mapping. Only `player_id` changes; `created_at`/`client_id`
   * are untouched (Req 5.7).
   */
  private async applyStoredResolutions(scope: string | null): Promise<void> {
    const key = this.resolutionMetaKey(scope);
    const map = (await getMeta<Record<string, string>>(key)) ?? {};
    if (Object.keys(map).length === 0) return;
    const unsynced = await getActionsByStatus('unsynced', scope);
    for (const action of unsynced) {
      if (action.entity === 'player') continue;
      const pid = playerRefOf(action);
      if (pid === undefined) continue;
      const resolved = map[pid];
      if (!resolved || resolved === pid) continue;
      const payload = { ...(action.payload as Record<string, unknown>), player_id: resolved };
      const updated: StoredSyncAction = { ...action, payload, player_id: resolved };
      await putAction(updated);
    }
  }

  /** Rewrite attendance `session_id` references once their session is applied. */
  private async applyStoredSessionResolutions(scope: string | null): Promise<void> {
    const key = this.sessionResolutionMetaKey(scope);
    const map = (await getMeta<Record<string, string>>(key)) ?? {};
    if (Object.keys(map).length === 0) return;
    const unsynced = await getActionsByStatus('unsynced', scope);
    for (const action of unsynced) {
      const sessionId = sessionRefOf(action);
      if (sessionId === undefined) continue;
      const resolved = map[sessionId];
      if (!resolved || resolved === sessionId) continue;
      const payload = {
        ...(action.payload as Record<string, unknown>),
        session_id: resolved,
      };
      await putAction({ ...action, payload });
    }
  }
}

// ---- Background sync & foreground fallback (Req 5.2, 5.1) — task 8.10 ----

/**
 * Feature-detect the Background Sync API. Under jsdom (tests) and browsers
 * without service workers this returns `false`, so the fallback triggers are
 * used instead.
 */
export function isBackgroundSyncSupported(): boolean {
  return (
    typeof navigator !== 'undefined' &&
    'serviceWorker' in navigator &&
    typeof window !== 'undefined' &&
    'SyncManager' in window
  );
}

/**
 * Register the `funhouse-sync` background-sync tag so the queue flushes when
 * connectivity returns even if the app is backgrounded (Req 5.2). No-op (and
 * never throws) where Background Sync is unavailable.
 */
export async function registerBackgroundSync(): Promise<boolean> {
  if (!isBackgroundSyncSupported()) return false;
  try {
    const reg = (await navigator.serviceWorker.ready) as ServiceWorkerRegistration & {
      sync?: { register(tag: string): Promise<void> };
    };
    if (!reg.sync) return false;
    await reg.sync.register(BACKGROUND_SYNC_TAG);
    return true;
  } catch {
    return false;
  }
}

export interface SyncSchedulerConfig {
  /** The flush routine to invoke on triggers. */
  flush: () => Promise<FlushResult>;
  /** Returns the current count of unsynced items (drives the fallback interval). */
  countUnsynced: () => Promise<number>;
  /** Fallback polling interval in ms while unsynced items remain (default 30s). */
  intervalMs?: number;
}

/**
 * Foreground fallback for browsers without Background Sync (Req 5.1): flushes on
 * the window `online` event, and runs a lightweight interval while unsynced
 * actions remain. Also registers the background-sync tag after each enqueue
 * where supported.
 */
export class SyncScheduler {
  private readonly flush: () => Promise<FlushResult>;
  private readonly countUnsynced: () => Promise<number>;
  private readonly intervalMs: number;
  private timer: ReturnType<typeof setInterval> | null = null;
  private started = false;
  private readonly onlineHandler = () => {
    void this.flush();
  };

  constructor(config: SyncSchedulerConfig) {
    this.flush = config.flush;
    this.countUnsynced = config.countUnsynced;
    this.intervalMs = config.intervalMs ?? 30_000;
  }

  /** Attach the `online` listener and start the fallback poll loop. */
  start(): void {
    if (this.started) return;
    this.started = true;
    if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
      window.addEventListener('online', this.onlineHandler);
    }
    this.timer = setInterval(() => {
      void this.tick();
    }, this.intervalMs);
  }

  /** Detach listeners and stop the poll loop. */
  stop(): void {
    if (!this.started) return;
    this.started = false;
    if (typeof window !== 'undefined' && typeof window.removeEventListener === 'function') {
      window.removeEventListener('online', this.onlineHandler);
    }
    if (this.timer !== null) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  /** Poll: flush only while there is work to do. */
  private async tick(): Promise<void> {
    const pending = await this.countUnsynced();
    if (pending > 0) {
      await this.flush();
    }
  }

  /**
   * Call after each enqueue: register a background sync where supported, and
   * proactively flush if we appear to be online.
   */
  async onEnqueue(): Promise<void> {
    await registerBackgroundSync();
    const online = typeof navigator === 'undefined' || navigator.onLine !== false;
    if (online) {
      await this.flush();
    }
  }
}
