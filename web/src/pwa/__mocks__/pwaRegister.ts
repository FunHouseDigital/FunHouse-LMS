/**
 * Test stub for `vite-plugin-pwa`'s `virtual:pwa-register` module.
 *
 * The virtual module only exists inside a `vite-plugin-pwa` build; under Vitest
 * the plugin isn't active, so `vitest.config.ts` aliases `virtual:pwa-register`
 * to this stub. `registerServiceWorker()` guards on `navigator.serviceWorker`
 * (absent in jsdom) and returns before importing, so `registerSW` here is never
 * actually invoked in tests — the stub exists purely so import-analysis resolves.
 */
export function registerSW(_options?: { immediate?: boolean }): (reload?: boolean) => Promise<void> {
  return async () => {};
}
