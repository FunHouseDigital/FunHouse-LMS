import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import {
  LEGACY_AUTHENTICATED_CACHE_NAMES,
  PRECACHE_GLOB_PATTERNS,
  RUNTIME_CACHE_RULES,
  pwaManifest,
} from './cachingConfig';

describe('web app manifest (Req 3.1)', () => {
  it('declares the required installability fields', () => {
    expect(pwaManifest.name).toBe('FunHouse Revenue');
    expect(pwaManifest.short_name).toBe('FunHouse');
    expect(pwaManifest.start_url).toBe('/');
    expect(pwaManifest.scope).toBe('/');
    expect(pwaManifest.display).toBe('standalone');
    expect(pwaManifest.background_color).toBe('#0b0b0f');
    expect(pwaManifest.theme_color).toBe('#0b0b0f');
  });

  it('declares 192, 512 and a maskable-512 icon', () => {
    const bySize = pwaManifest.icons.map((icon) => `${icon.sizes}:${icon.purpose ?? 'any'}`);
    expect(bySize).toContain('192x192:any');
    expect(bySize).toContain('512x512:any');
    expect(bySize).toContain('512x512:maskable');

    const maskable = pwaManifest.icons.find((icon) => icon.purpose === 'maskable');
    expect(maskable?.src).toBe('/icons/icon-maskable-512.png');
    expect(maskable?.type).toBe('image/png');
  });

  it('every icon points under /icons/ and is a PNG', () => {
    for (const icon of pwaManifest.icons) {
      expect(icon.src.startsWith('/icons/')).toBe(true);
      expect(icon.type).toBe('image/png');
    }
  });
});

describe('precache glob patterns (Req 3.2)', () => {
  it('covers the hashed build assets that form the app shell', () => {
    expect(PRECACHE_GLOB_PATTERNS.length).toBeGreaterThan(0);
    const joined = PRECACHE_GLOB_PATTERNS.join(',');
    for (const extension of ['js', 'css', 'html', 'png']) {
      expect(joined).toContain(extension);
    }
  });
});

describe('protected API response caching', () => {
  it('registers no runtime CacheStorage routes for bearer-authorized data', () => {
    expect(RUNTIME_CACHE_RULES).toEqual([]);

    const workerSource = readFileSync('src/sw.ts', 'utf8');
    expect(workerSource).not.toContain('workbox-strategies');
    expect(workerSource).not.toContain('CacheableResponsePlugin');
    expect(workerSource).not.toContain('authenticatedCacheKeyPlugin');
    expect(workerSource.match(/registerRoute\(/g)).toHaveLength(1);
  });

  it('retains every previous cache name solely for migration cleanup', () => {
    expect(LEGACY_AUTHENTICATED_CACHE_NAMES).toEqual([
      'api-revenue-summary',
      'api-alerts',
      'api-player-entitlements',
      'api-players',
      'api-products',
    ]);
    expect(new Set(LEGACY_AUTHENTICATED_CACHE_NAMES).size).toBe(
      LEGACY_AUTHENTICATED_CACHE_NAMES.length,
    );
  });
});
