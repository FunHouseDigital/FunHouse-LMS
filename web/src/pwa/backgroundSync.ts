/**
 * Background Sync wiring shared between the service worker and the app (Req 5.2).
 *
 * This module is Workbox-free and jsdom-safe so it can be unit-tested directly
 * (Task 13.4). It holds:
 *  - the `funhouse-sync` tag (re-exported from the Sync_Engine — single source),
 *  - the message contract the SW uses to ask open clients to flush,
 *  - a pure `handleSyncTag` helper that classifies a `sync` event's tag,
 *  - `notifyClientsToFlush(clients)` which the SW's `sync` handler calls, and
 *  - `listenForFlushRequests(onFlush)` which the app calls to react to those
 *    messages by running `Sync_Engine.flush()`.
 *
 * The service worker's `sync` event handler (see `src/sw.ts`) posts a message to
 * every open window client so the flush runs in the page context where the
 * Local_Store, Auth_Manager (bearer token), and Sync_Engine already live. This
 * avoids duplicating the entire sync stack inside the worker. When no client is
 * open the event resolves as a no-op and the foreground `online`/interval
 * fallback (task 8.10 `SyncScheduler`) flushes on next launch.
 *
 * See design.md "PWA / Service Worker → Background Sync".
 */
import { BACKGROUND_SYNC_TAG } from '../domain/syncEngine';

export { BACKGROUND_SYNC_TAG };

/** The message type the SW posts to clients to request a queue flush. */
export const SYNC_FLUSH_MESSAGE_TYPE = 'funhouse-sync-flush' as const;

/** The message payload shape exchanged between the SW and its clients. */
export interface SyncFlushMessage {
  type: typeof SYNC_FLUSH_MESSAGE_TYPE;
  tag: string;
}

/** Build the flush-request message for a given sync tag. */
export function makeSyncFlushMessage(tag: string = BACKGROUND_SYNC_TAG): SyncFlushMessage {
  return { type: SYNC_FLUSH_MESSAGE_TYPE, tag };
}

/** Type guard: is an arbitrary message a well-formed flush request? */
export function isSyncFlushMessage(data: unknown): data is SyncFlushMessage {
  return (
    typeof data === 'object' &&
    data !== null &&
    (data as { type?: unknown }).type === SYNC_FLUSH_MESSAGE_TYPE
  );
}

/**
 * Classify a `sync` event tag: returns `true` only for our tag so the SW
 * handler stays a clean no-op for any other registered sync (Req 5.2).
 */
export function handleSyncTag(tag: string): boolean {
  return tag === BACKGROUND_SYNC_TAG;
}

/** The minimal client surface the SW needs to post a flush request. */
export interface FlushClient {
  postMessage(message: SyncFlushMessage): void;
}

/**
 * Post a flush request to every open client (called from the SW `sync` handler).
 * Returns the number of clients notified. Safe with an empty list (no client
 * open → 0, handled by the foreground fallback later).
 */
export function notifyClientsToFlush(
  clients: readonly FlushClient[],
  tag: string = BACKGROUND_SYNC_TAG,
): number {
  const message = makeSyncFlushMessage(tag);
  for (const client of clients) {
    client.postMessage(message);
  }
  return clients.length;
}

/**
 * Feature-detect Background Sync in the current environment. Returns `false`
 * under jsdom / browsers without a service worker so callers can no-op cleanly.
 */
export function isBackgroundSyncAvailable(): boolean {
  return (
    typeof navigator !== 'undefined' &&
    'serviceWorker' in navigator &&
    typeof self !== 'undefined' &&
    'SyncManager' in (self as unknown as Record<string, unknown>)
  );
}

/**
 * Subscribe the app to SW flush requests: when the service worker posts a
 * `funhouse-sync-flush` message, invoke `onFlush()` (which runs
 * `Sync_Engine.flush()`). Returns an unsubscribe function; a no-op (returning a
 * no-op unsubscribe) where `serviceWorker` is unavailable (e.g. jsdom).
 */
export function listenForFlushRequests(onFlush: () => void): () => void {
  if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)) {
    return () => {};
  }
  const handler = (event: MessageEvent) => {
    if (isSyncFlushMessage(event.data)) {
      onFlush();
    }
  };
  navigator.serviceWorker.addEventListener('message', handler);
  return () => navigator.serviceWorker.removeEventListener('message', handler);
}
