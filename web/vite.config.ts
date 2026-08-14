import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { VitePWA } from 'vite-plugin-pwa';
import { pwaManifest, PRECACHE_GLOB_PATTERNS } from './src/pwa/cachingConfig';

const vercelCommitSha = process.env.VERCEL_GIT_COMMIT_SHA?.trim().toLowerCase();
const hasValidVercelCommitSha = /^[0-9a-f]{40}$/.test(vercelCommitSha ?? '');

if (process.env.VERCEL === '1' && !hasValidVercelCommitSha) {
  throw new Error('VERCEL_GIT_COMMIT_SHA is required for Vercel PWA builds');
}

const releaseId = hasValidVercelCommitSha ? vercelCommitSha!.slice(0, 7) : 'local';

// PWA / service-worker wiring (task 13).
//
// We use `injectManifest` (custom `src/sw.ts`) rather than `generateSW` because
// the Background Sync handler (task 13.3) needs a bespoke `sync` event listener
// that generateSW cannot express. The custom worker precaches the build output
// (`self.__WB_MANIFEST`), adds an SPA navigation fallback, retires legacy
// protected-response caches, and wires the `funhouse-sync` background-sync
// handler.
export default defineConfig({
  define: {
    // Public Git metadata only. Never expose the process environment to the browser.
    __APP_RELEASE_ID__: JSON.stringify(releaseId),
  },
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
