/// <reference lib="webworker" />
/**
 * Custom service worker (Workbox `injectManifest` mode) — Req 3.2, 3.3, 3.4,
 * 5.2, 9.2, 13.5, 16.3.
 *
 * ### Why injectManifest (not generateSW)
 * Task 13.3 requires a real `sync` **event handler** for the `funhouse-sync`
 * tag. `generateSW` can only emit declarative precache configuration and has no
 * clean way to add an arbitrary `self.addEventListener('sync', …)`.
 * `injectManifest` lets us author the worker directly, precache the app shell,
 * retire legacy protected-response caches, and wire Background Sync in one
 * place. Bearer-authorized API reads are deliberately stored only in the
 * account-scoped IndexedDB layer, never in CacheStorage.
 *
 * The file is compiled by `vite-plugin-pwa`; it is never imported by the app or
 * the test suite (the cache rules and sync wiring it depends on live in the
 * jsdom-safe `cachingConfig` / `backgroundSync` modules, which the tests target
 * directly).
 */
import { precacheAndRoute, createHandlerBoundToURL } from 'workbox-precaching';
import { NavigationRoute, registerRoute } from 'workbox-routing';
import { clearAuthenticatedResponseCaches } from './pwa/authenticatedCaches';
import { handleSyncTag, notifyClientsToFlush } from './pwa/backgroundSync';

declare const self: ServiceWorkerGlobalScope & {
  __WB_MANIFEST: Array<string | { url: string; revision: string | null }>;
};

/**
 * The Background Sync API `SyncEvent` is not part of the standard TS lib, so we
 * declare the minimal surface we use (`tag` + `waitUntil` via ExtendableEvent).
 */
interface SyncEvent extends ExtendableEvent {
  readonly tag: string;
}

// ---- Update-on-next-launch (Req 3.4) ----------------------------------------
// `registerType: 'autoUpdate'` in vite.config drives the client-side update;
// the worker takes control on the next navigation without forcing a mid-capture
// reload.
self.skipWaiting();
self.addEventListener('activate', (event) => {
  event.waitUntil(
    clearAuthenticatedResponseCaches().then(() => self.clients.claim()),
  );
});

// ---- App-shell precache + SPA navigation fallback (Req 3.2, 3.3) ------------
precacheAndRoute(self.__WB_MANIFEST);

// Serve the precached index.html for all navigations so the SPA shell renders
// with zero connectivity. `denylist` keeps API and asset requests out of the
// navigation route.
const navigationRoute = new NavigationRoute(createHandlerBoundToURL('index.html'), {
  denylist: [/^\/api\//, /\/[^/?]+\.[^/]+$/],
});
registerRoute(navigationRoute);

// Bearer-authorized API responses are intentionally excluded from Workbox
// runtime caching. Account-scoped IndexedDB owns offline data; the activation
// handler above removes response caches left by older workers.

// ---- Background Sync (Req 5.2) ----------------------------------------------
// On the `funhouse-sync` tag, message every open client to run
// `Sync_Engine.flush()` in the page context (where the Local_Store, bearer
// token, and engine already live). No-op for any other tag or when no client is
// open — the foreground online/interval fallback then flushes on next launch.
self.addEventListener('sync', ((event: SyncEvent) => {
  if (!handleSyncTag(event.tag)) return;
  event.waitUntil(
    self.clients
      .matchAll({ type: 'window', includeUncontrolled: true })
      .then((clients) => {
        notifyClientsToFlush(clients, event.tag);
      }),
  );
}) as EventListener);
