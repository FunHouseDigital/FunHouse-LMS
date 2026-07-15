import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { VitePWA } from 'vite-plugin-pwa';
import { pwaManifest, PRECACHE_GLOB_PATTERNS } from './src/pwa/cachingConfig';

// PWA / service-worker wiring (task 13).
//
// We use `injectManifest` (custom `src/sw.ts`) rather than `generateSW` because
// the Background Sync handler (task 13.3) needs a bespoke `sync` event listener
// that generateSW cannot express. The custom worker precaches the build output
// (`self.__WB_MANIFEST`), adds an SPA navigation fallback, registers the runtime
// read-cache strategies from `src/pwa/cachingConfig`, and wires the
// `funhouse-sync` background-sync handler.
export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      strategies: 'injectManifest',
      srcDir: 'src',
      filename: 'sw.ts',
      registerType: 'autoUpdate',
      injectRegister: null, // registration is done explicitly via src/pwa/register.ts
      manifest: pwaManifest,
      injectManifest: {
        globPatterns: PRECACHE_GLOB_PATTERNS,
      },
      devOptions: {
        enabled: false,
        type: 'module',
      },
    }),
  ],
  server: {
    port: 5173,
  },
});
