/**
 * PWA manifest + runtime-caching config tests (Task 13.4).
 *
 * Validates: Requirements 3.1, 3.2, 3.3, 3.4
 *
 * Real service workers / Background Sync are unavailable under jsdom, so we test
 * the extracted, jsdom-safe config module directly: the manifest object fed to
 * `vite-plugin-pwa`, and the runtime read-cache rule set + pathname matchers
 * that the custom SW registers.
 */
import { describe, it, expect } from 'vitest';
import {
  pwaManifest,
  PRECACHE_GLOB_PATTERNS,
  RUNTIME_CACHE_RULES,
  matchRuntimeCacheRule,
  isRevenueSummaryPath,
  isAlertsPath,
  isPlayersListPath,
  isPlayerEntitlementsPath,
  isProductsPath,
  PRODUCTS_MAX_AGE_SECONDS,
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
    const bySize = pwaManifest.icons.map((i) => `${i.sizes}:${i.purpose ?? 'any'}`);
    expect(bySize).toContain('192x192:any');
    expect(bySize).toContain('512x512:any');
    expect(bySize).toContain('512x512:maskable');

    const maskable = pwaManifest.icons.find((i) => i.purpose === 'maskable');
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
    for (const ext of ['js', 'css', 'html', 'png']) {
      expect(joined).toContain(ext);
    }
  });
});

describe('runtime read-caching rules (Req 3.3, 9.2, 13.5, 16.3)', () => {
  function ruleFor(pathname: string) {
    return matchRuntimeCacheRule(new URL(`https://api.example.com${pathname}`));
  }

  it('uses StaleWhileRevalidate for summary, alerts and roster', () => {
    expect(ruleFor('/revenue/summary')?.strategy).toBe('StaleWhileRevalidate');
    expect(ruleFor('/alerts')?.strategy).toBe('StaleWhileRevalidate');
    expect(ruleFor('/players')?.strategy).toBe('StaleWhileRevalidate');
  });

  it('uses NetworkFirst for player entitlements (freshness preferred)', () => {
    expect(ruleFor('/players/abc-123/entitlements')?.strategy).toBe('NetworkFirst');
  });

  it('uses CacheFirst with a long TTL for the product catalog', () => {
    const rule = ruleFor('/products');
    expect(rule?.strategy).toBe('CacheFirst');
    expect(rule?.expiration?.maxAgeSeconds).toBe(PRODUCTS_MAX_AGE_SECONDS);
    expect(PRODUCTS_MAX_AGE_SECONDS).toBe(24 * 60 * 60);
  });

  it('matches by pathname regardless of origin (cross-origin API)', () => {
    // Same rule resolved whether the API is on localhost:8000 or a remote host.
    const local = matchRuntimeCacheRule(new URL('http://localhost:8000/revenue/summary'));
    const remote = matchRuntimeCacheRule(new URL('https://api.funhouse.co.za/revenue/summary'));
    expect(local?.cacheName).toBe('api-revenue-summary');
    expect(remote?.cacheName).toBe('api-revenue-summary');
  });

  it('ignores query strings when matching', () => {
    const rule = matchRuntimeCacheRule(
      new URL('https://api.example.com/revenue/summary?period=weekly&location=cpt'),
    );
    expect(rule?.cacheName).toBe('api-revenue-summary');
  });

  it('prefers the specific entitlements rule over the broad players rule', () => {
    // Ordering guarantee: /players/{id}/entitlements must not be swallowed by /players.
    expect(ruleFor('/players/xyz/entitlements')?.cacheName).toBe('api-player-entitlements');
    expect(ruleFor('/players')?.cacheName).toBe('api-players');
  });

  it('does not cache unrelated paths (e.g. /sync, /auth/login)', () => {
    expect(ruleFor('/sync')).toBeUndefined();
    expect(ruleFor('/auth/login')).toBeUndefined();
    expect(ruleFor('/players/xyz/history')).toBeUndefined();
  });

  it('every rule targets GET with a unique cache name', () => {
    const names = new Set<string>();
    for (const rule of RUNTIME_CACHE_RULES) {
      expect(rule.method).toBe('GET');
      expect(names.has(rule.cacheName)).toBe(false);
      names.add(rule.cacheName);
    }
    expect(names.size).toBe(RUNTIME_CACHE_RULES.length);
  });
});

describe('pathname matchers', () => {
  it('match exactly and reject near-misses', () => {
    expect(isRevenueSummaryPath('/revenue/summary')).toBe(true);
    expect(isRevenueSummaryPath('/revenue/summary/extra')).toBe(false);

    expect(isAlertsPath('/alerts')).toBe(true);
    expect(isAlertsPath('/alertss')).toBe(false);

    expect(isPlayersListPath('/players')).toBe(true);
    expect(isPlayersListPath('/players/1')).toBe(false);

    expect(isPlayerEntitlementsPath('/players/1/entitlements')).toBe(true);
    expect(isPlayerEntitlementsPath('/players//entitlements')).toBe(false);
    expect(isPlayerEntitlementsPath('/players/1/history')).toBe(false);

    expect(isProductsPath('/products')).toBe(true);
    expect(isProductsPath('/product')).toBe(false);
  });
});
