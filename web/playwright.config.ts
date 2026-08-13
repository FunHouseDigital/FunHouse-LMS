import { defineConfig } from '@playwright/test';

const PWA_ORIGIN = 'https://funhouse-revenue-pwa.vercel.app';

export default defineConfig({
  testDir: './e2e',
  testMatch: 'production-smoke.spec.ts',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  reporter: [['line']],
  outputDir: 'test-results/production-smoke',
  use: {
    baseURL: PWA_ORIGIN,
    browserName: 'chromium',
    headless: true,
    locale: 'en-ZA',
    timezoneId: 'Africa/Johannesburg',
    serviceWorkers: 'allow',
    trace: 'off',
    screenshot: 'off',
    video: 'off',
  },
});
