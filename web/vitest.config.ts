import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      // vite-plugin-pwa's virtual module doesn't exist under Vitest (the plugin
      // isn't active); alias it to a stub so `src/pwa/register.ts` resolves.
      'virtual:pwa-register': fileURLToPath(
        new URL('./src/pwa/__mocks__/pwaRegister.ts', import.meta.url),
      ),
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/setupTests.ts'],
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    css: false,
  },
});
