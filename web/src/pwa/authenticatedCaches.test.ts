import { afterEach, describe, expect, it, vi } from 'vitest';
import { LEGACY_AUTHENTICATED_CACHE_NAMES } from './cachingConfig';
import { clearAuthenticatedResponseCaches } from './authenticatedCaches';

describe('legacy authenticated cache cleanup', () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('settles successfully when CacheStorage rejects every deletion', async () => {
    const deleteCache = vi.fn(async () => {
      throw new DOMException('Storage access denied', 'SecurityError');
    });
    vi.stubGlobal('caches', { delete: deleteCache });

    await expect(clearAuthenticatedResponseCaches()).resolves.toBeUndefined();
    expect(deleteCache).toHaveBeenCalledTimes(LEGACY_AUTHENTICATED_CACHE_NAMES.length);
  });

  it('stops waiting when a browser CacheStorage operation never settles', async () => {
    vi.useFakeTimers();
    const deleteCache = vi.fn(() => new Promise<boolean>(() => undefined));
    vi.stubGlobal('caches', { delete: deleteCache });

    const cleanup = clearAuthenticatedResponseCaches(25);
    await vi.advanceTimersByTimeAsync(25);

    await expect(cleanup).resolves.toBeUndefined();
  });

  it('is a no-op when CacheStorage is unsupported', async () => {
    vi.stubGlobal('caches', undefined);
    await expect(clearAuthenticatedResponseCaches()).resolves.toBeUndefined();
  });
});
