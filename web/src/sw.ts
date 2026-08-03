/// <reference lib="webworker" />
/**
 * Custom service worker (Workbox `injectManifest` mode) — Req 3.2, 3.3, 3.4,
 * 5.2, 9.2, 13.5, 16.3.
 *
 * ### Why injectManifest (not generateSW)
 * Task 13.3 requires a real `sync` **event handler** for the `funhouse-sync`
 * tag. `generateSW` can only emit declarative precache + `runtimeCaching`; it
 * has no clean way to add an arbitrary `self.addEventListener('sync', …)`.
 * `injectManifest` lets us author the worker directly — precache via
 * `self.__WB_MANIFEST`, register the same runtime read-cache strategies through
 * the shared `cachingConfig`, AND wire the Background Sync handler — all in one
 * place. This refines design.md's earlier "generateSW + small custom plugin"
 * note now that a bespoke event handler is actually needed.
 *
 * The file is compiled by `vite-plugin-pwa`; it is never imported by the app or
 * the test suite (the cache rules and sync wiring it depends on live in the
 * jsdom-safe `cachingConfig` / `backgroundSync` modules, which the tests target
 * directly).
 */
import { precacheAndRoute, createHandlerBoundToURL } from 'workbox-precaching';
import { NavigationRoute, registerRoute } from 'workbox-routing';
import {
  CacheFirst,
  NetworkFirst,
  StaleWhileRevalidate,
  type Strategy,
} from 'workbox-strategies';
import { CacheableResponsePlugin } from 'workbox-cacheable-response';
import { ExpirationPlugin } from 'workbox-expiration';
import {
  RUNTIME_CACHE_RULES,
  matchRuntimeCacheRule,
  type CacheStrategyName,
  type RuntimeCacheRule,
} from './pwa/cachingConfig';
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
self.addEventListener('activate', () => self.clients.claim());

// ---- App-shell precache + SPA navigation fallback (Req 3.2, 3.3) ------------
precacheAndRoute(self.__WB_MANIFEST);

// Serve the precached index.html for all navigations so the SPA shell renders
// with zero connectivity. `denylist` keeps API and asset requests out of the
// navigation route.
const navigationRoute = new NavigationRoute(createHandlerBoundToURL('index.html'), {
  denylist: [/^\/api\//, /\/[^/?]+\.[^/]+$/],
});
registerRoute(navigationRoute);

// ---- Runtime read caching (Req 9.2, 13.5, 16.3) -----------------------------

/**
 * Workbox normally keys cached API responses only by URL. These reads are
 * bearer-authorized, so include a one-way token digest in cache keys to prevent
 * responses from one authenticated session being served to another. The
 * original request (and Authorization header) is still used for the network.
 */
const authenticatedCacheKeyPlugin = {
  async cacheKeyWillBeUsed({ request }: { request: Request }): Promise<Request> {
    const authorization = request.headers.get('Authorization');
    if (!authorization) return request;

    const digest = await crypto.subtle.digest(
      'SHA-256',
      new TextEncoder().encode(authorization),
    );
    const sessionKey = Array.from(new Uint8Array(digest), (byte) =>
      byte.toString(16).padStart(2, '0'),
    ).join('');
    const cacheUrl = new URL(request.url);
    cacheUrl.searchParams.set('__funhouse_session', sessionKey);
    return new Request(cacheUrl.toString(), request);
  },
};

function buildStrategy(rule: RuntimeCacheRule): Strategy {
  const cacheableResponse = new CacheableResponsePlugin({ statuses: [0, 200] });
  const commonPlugins = [authenticatedCacheKeyPlugin, cacheableResponse];

  const strategyByName: Record<CacheStrategyName, () => Strategy> = {
    StaleWhileRevalidate: () =>
      new StaleWhileRevalidate({ cacheName: rule.cacheName, plugins: commonPlugins }),
    NetworkFirst: () =>
      new NetworkFirst({ cacheName: rule.cacheName, plugins: commonPlugins }),
    CacheFirst: () =>
      new CacheFirst({
        cacheName: rule.cacheName,
        plugins: rule.expiration
          ? [
              ...commonPlugins,
              new ExpirationPlugin({
                maxEntries: rule.expiration.maxEntries,
                maxAgeSeconds: rule.expiration.maxAgeSeconds,
              }),
            ]
          : commonPlugins,
      }),
  };
  return strategyByName[rule.strategy]();
}

// Prebuild one strategy instance per rule (so each keeps its own cache).
const strategies = new Map<string, Strategy>(
  RUNTIME_CACHE_RULES.map((rule) => [rule.cacheName, buildStrategy(rule)]),
);

// A single matcher/handler pair keyed off pathname (origin-independent so it
// works whether the API is same-origin or on a separate host).
registerRoute(
  ({ url, request }) => request.method === 'GET' && matchRuntimeCacheRule(url) !== undefined,
  (options) => {
    const rule = matchRuntimeCacheRule(options.url)!;
    return strategies.get(rule.cacheName)!.handle(options);
  },
);

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
