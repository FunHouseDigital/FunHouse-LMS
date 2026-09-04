import { defineConfig } from '@playwright/test';

const PWA_ORIGIN = 'http://127.0.0.1:4173';

export default defineConfig({
  testDir: './e2e',
  testMatch: 'field-acceptance-hermetic.spec.ts',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 180_000,
  expect: { timeout: 15_000 },
  reporter: [['line']],
  outputDir: 'test-results/field-acceptance-hermetic',
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
  webServer: {
    command: 'npm run preview -- --host 127.0.0.1 --port 4173 --strictPort',
    url: PWA_ORIGIN,
    reuseExistingServer: process.env.CI !== 'true',
    timeout: 30_000,
  },
});