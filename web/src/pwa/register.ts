/**
 * Service-worker registration entry point (Req 3.1–3.4).
 *
 * Uses `vite-plugin-pwa`'s injected `virtual:pwa-register` helper with
 * `registerType: 'autoUpdate'`, so a newly deployed worker installs in the
 * background and activates on the next launch (Req 3.4) without a forced
 * mid-capture reload.
 *
 * Guarded so it is a clean no-op when service workers are unavailable (e.g.
 * jsdom under test, or an insecure context). Called from `main.tsx`; never
 * imported by the test suite.
 */
export function registerServiceWorker(): void {
  if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)) {
    return;
  }
  // Dynamic import so the virtual module is only pulled in a real browser build.
  void import('virtual:pwa-register')
    .then(({ registerSW }) => {
      registerSW({ immediate: true });
    })
    .catch(() => {
      // Registration is best-effort; the app still works online without the SW.
    });
}
