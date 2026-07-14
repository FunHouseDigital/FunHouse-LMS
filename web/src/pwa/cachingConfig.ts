/**
 * PWA manifest + runtime read-caching configuration (Req 3.1, 3.2, 3.3, 9.2,
 * 13.5, 16.3).
 *
 * This module is deliberately **pure data + pure matchers** with NO Workbox or
 * service-worker imports, so it can be:
 *  - imported by `vite.config.ts` to feed `vite-plugin-pwa` the manifest, and
 *  - imported by the custom service worker (`src/sw.ts`) to drive
 *    `registerRoute`, and
 *  - unit-tested directly under jsdom (no real SW required — Task 13.4).
 *
 * ### Cross-origin matching
 * The Container_API commonly lives on a **different origin** from the static PWA
 * bundle (e.g. the app is served from a CDN while the API is at
 * `https://api.example.com` or `http://localhost:8000` in dev — see
 * `authState.getDefaultBaseUrl`). Runtime-cache route matching therefore keys
 * off the request **pathname only** (never the origin), so the same rules apply
 * regardless of where the API is deployed.
 *
 * See design.md "PWA / Service Worker → Runtime caching for GET reads".
 */

/** Workbox runtime strategy names used by the read caches. */
export type CacheStrategyName = 'StaleWhileRevalidate' | 'NetworkFirst' | 'CacheFirst';

/** A single runtime-caching rule, matched by pathname against GET reads. */
export interface RuntimeCacheRule {
  /** Stable identifier (used as the Workbox cache name and in tests). */
  readonly cacheName: string;
  /** The Workbox strategy to apply. */
  readonly strategy: CacheStrategyName;
  /** HTTP method this rule applies to (all reads here are GET). */
  readonly method: 'GET';
  /** Human-readable description of what this rule caches and why. */
  readonly description: string;
  /**
   * Pathname matcher. Receives the request URL's `pathname` (origin-independent)
   * and returns whether this rule owns the request.
   */
  readonly matches: (pathname: string) => boolean;
  /** Optional cache-expiration hints (used by CacheFirst long-TTL catalog). */
  readonly expiration?: {
    readonly maxEntries?: number;
    readonly maxAgeSeconds?: number;
  };
}

// ---- Pathname matchers (origin-independent) ----------------------------------

/** Exact `GET /revenue/summary` (query string is stripped before matching). */
export function isRevenueSummaryPath(pathname: string): boolean {
  return pathname === '/revenue/summary';
}

/** Exact `GET /alerts`. */
export function isAlertsPath(pathname: string): boolean {
  return pathname === '/alerts';
}

/** Exact `GET /players` (the roster list — NOT a nested players sub-resource). */
export function isPlayersListPath(pathname: string): boolean {
  return pathname === '/players';
}

/** `GET /players/{id}/entitlements` for any non-empty id segment. */
export function isPlayerEntitlementsPath(pathname: string): boolean {
  return /^\/players\/[^/]+\/entitlements$/.test(pathname);
}

/** Exact `GET /products` (near-static catalog). */
export function isProductsPath(pathname: string): boolean {
  return pathname === '/products';
}

/** One day, in seconds — the long TTL for the near-static product catalog. */
export const PRODUCTS_MAX_AGE_SECONDS = 24 * 60 * 60;

/**
 * The runtime read-caching rules (Req 9.2, 13.5, 16.3):
 *  - StaleWhileRevalidate for `/revenue/summary`, `/alerts`, `/players`
 *    (render instantly from cache, refresh in the background).
 *  - NetworkFirst for `/players/{id}/entitlements` (balance freshness preferred,
 *    fall back to cache offline).
 *  - CacheFirst (long TTL) for `/products` (catalog is near-static, needed
 *    offline for Sell/units).
 *
 * Order matters: the first matching rule wins, so the specific
 * `/players/{id}/entitlements` rule precedes the broad `/players` rule.
 */
export const RUNTIME_CACHE_RULES: readonly RuntimeCacheRule[] = [
  {
    cacheName: 'api-revenue-summary',
    strategy: 'StaleWhileRevalidate',
    method: 'GET',
    description: 'Revenue summary — cached instantly, refreshed in background (Req 13.5)',
    matches: isRevenueSummaryPath,
  },
  {
    cacheName: 'api-alerts',
    strategy: 'StaleWhileRevalidate',
    method: 'GET',
    description: 'Operational alerts — cached offline, no client recompute (Req 16.3, 16.4)',
    matches: isAlertsPath,
  },
  {
    cacheName: 'api-player-entitlements',
    strategy: 'NetworkFirst',
    method: 'GET',
    description: 'Entitlement balances — freshness preferred, cache fallback offline (Req 8.5)',
    matches: isPlayerEntitlementsPath,
  },
  {
    cacheName: 'api-players',
    strategy: 'StaleWhileRevalidate',
    method: 'GET',
    description: 'Player roster — renders offline from cache (Req 9.2)',
    matches: isPlayersListPath,
  },
  {
    cacheName: 'api-products',
    strategy: 'CacheFirst',
    method: 'GET',
    description: 'Product catalog — near-static, long TTL, needed offline for Sell (Req 12)',
    matches: isProductsPath,
    expiration: { maxEntries: 32, maxAgeSeconds: PRODUCTS_MAX_AGE_SECONDS },
  },
];

/**
 * Resolve the runtime-cache rule that owns a given request URL, or `undefined`
 * if none do. The URL's query string is ignored; matching is by pathname only
 * so it works cross-origin (see module docs). Exported for the SW and tests.
 */
export function matchRuntimeCacheRule(url: URL): RuntimeCacheRule | undefined {
  return RUNTIME_CACHE_RULES.find((rule) => rule.matches(url.pathname));
}

// ---- Web app manifest (Req 3.1) ----------------------------------------------

/**
 * The web app manifest passed to `vite-plugin-pwa`. Kept here (rather than
 * inline in `vite.config.ts`) so the exact fields are assertable in tests
 * (Task 13.4) and there is a single source of truth.
 */
export const pwaManifest = {
  name: 'FunHouse Revenue',
  short_name: 'FunHouse',
  description: 'Offline-first revenue capture and reporting for the FunHouse Operating System',
  start_url: '/',
  scope: '/',
  display: 'standalone' as const,
  background_color: '#0b0b0f',
  theme_color: '#0b0b0f',
  icons: [
    { src: '/icons/icon-192.png', sizes: '192x192', type: 'image/png' },
    { src: '/icons/icon-512.png', sizes: '512x512', type: 'image/png' },
    {
      src: '/icons/icon-maskable-512.png',
      sizes: '512x512',
      type: 'image/png',
      purpose: 'maskable' as const,
    },
  ],
};

/** Glob patterns for the precache manifest (`self.__WB_MANIFEST`) — Req 3.2. */
export const PRECACHE_GLOB_PATTERNS = [
  '**/*.{js,css,html,ico,png,svg,webp,woff,woff2}',
];
