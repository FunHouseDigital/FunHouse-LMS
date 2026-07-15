/**
 * Background Sync wiring tests (Task 13.4).
 *
 * Validates: Requirements 5.2
 *
 * Real Background Sync / service workers aren't available under jsdom, so we
 * test the extracted, jsdom-safe wiring: tag classification, the SW→client
 * flush-request message contract, and the client-side listener/registration
 * guards. `src/sw.ts` composes these same helpers in the worker context.
 */
import { describe, it, expect, vi } from 'vitest';
import {
  BACKGROUND_SYNC_TAG,
  SYNC_FLUSH_MESSAGE_TYPE,
  makeSyncFlushMessage,
  isSyncFlushMessage,
  handleSyncTag,
  notifyClientsToFlush,
  isBackgroundSyncAvailable,
  listenForFlushRequests,
  type FlushClient,
  type SyncFlushMessage,
} from './backgroundSync';
import { registerServiceWorker } from './register';

describe('sync tag classification (Req 5.2)', () => {
  it('handles only the funhouse-sync tag', () => {
    expect(BACKGROUND_SYNC_TAG).toBe('funhouse-sync');
    expect(handleSyncTag('funhouse-sync')).toBe(true);
    expect(handleSyncTag('some-other-tag')).toBe(false);
    expect(handleSyncTag('')).toBe(false);
  });
});

describe('flush-request message contract', () => {
  it('builds a well-formed message defaulting to the funhouse-sync tag', () => {
    const msg = makeSyncFlushMessage();
    expect(msg.type).toBe(SYNC_FLUSH_MESSAGE_TYPE);
    expect(msg.tag).toBe('funhouse-sync');
  });

  it('recognises its own messages and rejects foreign ones', () => {
    expect(isSyncFlushMessage(makeSyncFlushMessage())).toBe(true);
    expect(isSyncFlushMessage({ type: 'something-else' })).toBe(false);
    expect(isSyncFlushMessage(null)).toBe(false);
    expect(isSyncFlushMessage('funhouse-sync-flush')).toBe(false);
  });
});

describe('notifyClientsToFlush (SW → clients)', () => {
  it('posts a flush message to every open client and returns the count', () => {
    const received: SyncFlushMessage[] = [];
    const makeClient = (): FlushClient => ({
      postMessage: (m) => received.push(m),
    });
    const clients = [makeClient(), makeClient(), makeClient()];

    const notified = notifyClientsToFlush(clients);

    expect(notified).toBe(3);
    expect(received).toHaveLength(3);
    for (const m of received) {
      expect(m.type).toBe(SYNC_FLUSH_MESSAGE_TYPE);
      expect(m.tag).toBe('funhouse-sync');
    }
  });

  it('is a safe no-op when no client is open', () => {
    expect(notifyClientsToFlush([])).toBe(0);
  });
});

describe('client-side guards under jsdom', () => {
  it('reports Background Sync unavailable (no SyncManager in jsdom)', () => {
    expect(isBackgroundSyncAvailable()).toBe(false);
  });

  it('listenForFlushRequests returns a no-op unsubscribe when SW is unavailable', () => {
    const onFlush = vi.fn();
    const unsubscribe = listenForFlushRequests(onFlush);
    expect(typeof unsubscribe).toBe('function');
    // No service worker to message → callback is never invoked; cleanup is safe.
    expect(() => unsubscribe()).not.toThrow();
    expect(onFlush).not.toHaveBeenCalled();
  });

  it('registerServiceWorker is a safe no-op without navigator.serviceWorker', () => {
    expect(() => registerServiceWorker()).not.toThrow();
  });
});
