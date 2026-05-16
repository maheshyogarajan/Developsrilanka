import { defineConfig, devices } from '@playwright/test';

/**
 * FIESTA smoke-test Playwright config.
 * BASE_URL can be overridden via env: BASE_URL=https://... npx playwright test
 * Default: https://fiesta-mvp.fly.dev
 */
export default defineConfig({
  testDir: './smoke',
  fullyParallel: false,   // smoke tests share login state — run sequentially
  forbidOnly: !!process.env.CI,
  retries: 1,
  timeout: 30_000,
  expect: { timeout: 10_000 },

  reporter: [
    ['list'],
    ['html', { open: 'never', outputFolder: 'playwright-report' }],
    ['junit', { outputFile: 'test-results/results.xml' }],
  ],

  use: {
    baseURL: process.env.BASE_URL || 'https://fiesta-mvp.fly.dev',
    headless: true,
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    trace: 'on-first-retry',
    // Storage state reused across tests within project
    storageState: undefined,
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
