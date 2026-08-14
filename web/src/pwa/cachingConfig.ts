/**
 * PWA manifest and app-shell precache configuration (Req 3.1, 3.2, 3.3).
 *
 * Bearer-authorized API responses deliberately do not use CacheStorage. Their
 * offline copies live in account-scoped IndexedDB; duplicating them in a
 * browser-managed response cache risks stale cross-account data and makes
 * login depend on CacheStorage support. The fixed names below are retained
 * only so new service workers can remove caches created by older releases.
 */

/** Protected-response caches created by releases before the IndexedDB-only policy. */
export const LEGACY_AUTHENTICATED_CACHE_NAMES = [
  'api-revenue-summary',
  'api-alerts',
  'api-player-entitlements',
  'api-players',
  'api-products',
] as const;

/** No bearer-authorized API route may write responses to CacheStorage. */
export const RUNTIME_CACHE_RULES = [] as const;

/**
 * The web app manifest passed to `vite-plugin-pwa`. Kept here so the exact
 * installability fields remain directly testable.
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
