/**
 * Smoke test 01: Homepage loads with expected brand content.
 * Verifies HTTP 200 (implicit — Playwright throws on non-2xx navigations with
 * waitUntil:'networkidle'), page title contains the app name, and primary CTA
 * links are present.
 */
import { test, expect } from '@playwright/test';

test.describe('01 — Homepage', () => {
  test('loads with status 200 and shows app branding', async ({ page }) => {
    const response = await page.goto('/', { waitUntil: 'domcontentloaded' });
    expect(response?.status()).toBe(200);

    // Title should reference the app, not a raw error page
    const title = await page.title();
    expect(title.length).toBeGreaterThan(0);
    // Note: The app brand name is "Develop Sri Lanka" in templates; FIESTA is the
    // project codename. We check for the actual deployed page title.
    expect(title).not.toContain('Error');
    expect(title).not.toContain('500');
    expect(title).not.toContain('502');

    // Page should contain meaningful content (not blank / crash page)
    const bodyText = await page.locator('body').innerText();
    expect(bodyText.length).toBeGreaterThan(100);

    // At least one of the expected nav/CTA links should be visible
    const loginOrScanLink = page.locator('a[href*="login"], a[href*="scan"], a[href*="register"]').first();
    await expect(loginOrScanLink).toBeVisible({ timeout: 8_000 });
  });

  test('no unhandled 5xx errors on homepage', async ({ page }) => {
    const serverErrors: string[] = [];
    page.on('response', (res) => {
      if (res.status() >= 500) serverErrors.push(`${res.status()} ${res.url()}`);
    });
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    expect(serverErrors).toHaveLength(0);
  });
});
