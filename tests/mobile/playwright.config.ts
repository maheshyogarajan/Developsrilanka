/**
 * Tier C-5 mobile audit Playwright config — kept separate from smoke config
 * so the snapshot capture + viewport regression test can be invoked independently.
 *
 * Run from tests/playwright (where node_modules lives):
 *   BASE_URL=https://fiesta-mvp.fly.dev MOBILE_AUDIT_PHASE=before \
 *     npx playwright test --config ../mobile/playwright.config.ts
 */
import { defineConfig, devices } from '@playwright/test';
import * as path from 'path';

export default defineConfig({
  testDir: '.',                       // this directory (tests/mobile/)
  fullyParallel: false,                // mobile capture is sequential to avoid login races
  forbidOnly: !!process.env.CI,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [
    ['list'],
    ['json', { outputFile: path.resolve(__dirname, '..', '..', '_tier_c_mobile_audit', 'playwright_results.json') }],
  ],
  use: {
    baseURL: process.env.BASE_URL || 'https://fiesta-mvp.fly.dev',
    headless: true,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
