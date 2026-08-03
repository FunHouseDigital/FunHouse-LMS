import { RUNTIME_CACHE_RULES } from './cachingConfig';

/**
 * Remove service-worker API responses tied to the previous bearer token.
 * IndexedDB holds the deliberately offline-capable, account-scoped copies;
 * CacheStorage must not return one account's authorized response to another.
 */
export async function clearAuthenticatedResponseCaches(): Promise<void> {
  if (typeof caches === 'undefined') return;
  await Promise.all(
    RUNTIME_CACHE_RULES.map((rule) => caches.delete(rule.cacheName)),
  );
}
