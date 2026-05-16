/**
 * Smoke test 05: Authenticated landing / dashboard.
 *
 * After login, verifies:
 * - Dashboard / scan page loads (not redirected back to /login)
 * - Primary sidebar nav is present
 * - No 5xx errors in page resources
 *
 * Requires a valid TEST_EMAIL + TEST_PASSWORD account.
 */
import { test, expect, Page } from '@playwright/test';
import { loginAs } from './helpers';

async function loginAndGetPage(page: Page): Promise<void> {
  await loginAs(page);
  // If we land on verify-email-reminder, that's acceptable — email verification
  // is separate from auth. The user IS logged in.
}

test.describe('05 — Authenticated dashboard', () => {
  test('after login, app does not redirect back to /login', async ({ page }) => {
    await loginAndGetPage(page);
    const url = page.url();
    expect(url).not.toContain('/login');
  });

  test('sidebar / navigation is present after login', async ({ page }) => {
    await loginAndGetPage(page);

    // The layout template always renders the sidebar for authenticated users.
    // Check for at least one nav-related element.
    const sidebar = page.locator('.sidebar, nav, [class*="navbar"]').first();
    await expect(sidebar).toBeVisible({ timeout: 10_000 });
  });

  test('brand logo or brand text is visible in sidebar', async ({ page }) => {
    await loginAndGetPage(page);

    // brand-text "Develop Sri Lanka" or logo image
    const brand = page.locator('.brand-text, .sidebar-brand img, .sidebar-brand').first();
    await expect(brand).toBeVisible({ timeout: 8_000 });
  });

  test('no 5xx errors on authenticated landing page', async ({ page }) => {
    const serverErrors: string[] = [];
    page.on('response', (res) => {
      if (res.status() >= 500) serverErrors.push(`${res.status()} ${res.url()}`);
    });
    await loginAndGetPage(page);
    expect(serverErrors).toHaveLength(0);
  });
});
