import { LEGACY_AUTHENTICATED_CACHE_NAMES } from './cachingConfig';

/** Maximum time legacy cleanup may delay activation or an account transition. */
export const AUTHENTICATED_CACHE_CLEANUP_TIMEOUT_MS = 1_000;

/**
 * Best-effort removal of protected API caches created by older releases.
 *
 * Current releases never write bearer-authorized responses to CacheStorage;
 * account-scoped IndexedDB is the only offline data store. Some browsers and
 * private modes expose `caches` but reject or indefinitely stall operations,
 * so legacy cleanup must never reject or become a login prerequisite.
 */
export async function clearAuthenticatedResponseCaches(
  timeoutMs = AUTHENTICATED_CACHE_CLEANUP_TIMEOUT_MS,
): Promise<void> {
  if (typeof caches === 'undefined') return;

  const cleanup = Promise.allSettled(
    LEGACY_AUTHENTICATED_CACHE_NAMES.map((cacheName) =>
      Promise.resolve().then(() => caches.delete(cacheName)),
    ),
  );

  let timeout: ReturnType<typeof setTimeout> | undefined;
  try {
    await Promise.race([
      cleanup,
      new Promise<void>((resolve) => {
        timeout = globalThis.setTimeout(resolve, timeoutMs);
      }),
    ]);
  } finally {
    if (timeout !== undefined) globalThis.clearTimeout(timeout);
  }
}
