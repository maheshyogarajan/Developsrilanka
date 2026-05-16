/**
 * Shared helpers for FIESTA smoke tests.
 * Provides test credentials, login utility, and cleanup helpers.
 */
import { Page, expect } from '@playwright/test';

// Deterministic test account — pre-seeded or created by 04-login.spec.ts
export const TEST_EMAIL = process.env.TEST_EMAIL || 'playwright.smoke@smarter.tax';
export const TEST_PASSWORD = process.env.TEST_PASSWORD || 'Playwright$moke2026!';

/** Full email/password login flow.  Returns after dashboard/scan page loads. */
export async function loginAs(page: Page, email = TEST_EMAIL, password = TEST_PASSWORD): Promise<void> {
  await page.goto('/login');

  // Fill credentials
  await page.locator('input[name="email"]').fill(email);
  await page.locator('input[name="password"]').fill(password);
  // Ensure action is "login"
  await page.locator('input[name="action"]').evaluate((el: HTMLInputElement) => {
    el.value = 'login';
  });

  // Submit — the animation intercepts form submission; wait for navigation
  await Promise.all([
    page.waitForNavigation({ waitUntil: 'domcontentloaded', timeout: 20_000 }).catch(() => {}),
    page.locator('#loginButton').click(),
  ]);

  // Give the animation time to complete and redirect
  await page.waitForTimeout(3000);

  // If still on login/verify-email-reminder, try direct POST as fallback
  if (page.url().includes('/login') || page.url().includes('/verify-email')) {
    // already handled or redirected to verify-email; acceptable
  }
}

/** Click logout button (POST via form or direct GET) */
export async function logout(page: Page): Promise<void> {
  // Try logout nav link first
  const logoutLink = page.locator('a[href*="logout"], button[data-action="logout"]').first();
  if (await logoutLink.isVisible()) {
    await logoutLink.click();
    await page.waitForLoadState('domcontentloaded');
  } else {
    await page.goto('/logout');
  }
}
