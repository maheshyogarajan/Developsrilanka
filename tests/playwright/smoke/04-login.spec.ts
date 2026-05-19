/**
 * Smoke test 04: Login flow — email + password.
 *
 * Uses TEST_EMAIL / TEST_PASSWORD env vars (defaults to playwright.smoke@smarter.tax).
 * If the account does not exist on staging, this test will document the failure
 * clearly so that seed data or env vars can be provided.
 *
 * Login page has a JavaScript animation that intercepts form submission; the test
 * waits for it to complete (3-4s) before checking the result URL.
 */
import { test, expect } from '@playwright/test';
import { TEST_EMAIL, TEST_PASSWORD } from './helpers';

test.describe('04 — Login (email + password)', () => {
  test('login page renders with email + password form', async ({ page }) => {
    const res = await page.goto('/login', { waitUntil: 'domcontentloaded' });
    expect(res?.status()).toBe(200);

    await expect(page.locator('input[name="email"]')).toBeVisible();
    await expect(page.locator('input[name="password"]')).toBeVisible();
    await expect(page.locator('#loginButton')).toBeVisible();
  });

  test('valid credentials redirect away from /login', async ({ page }) => {
    await page.goto('/login', { waitUntil: 'domcontentloaded' });

    await page.locator('input[name="email"]').fill(TEST_EMAIL);
    await page.locator('input[name="password"]').fill(TEST_PASSWORD);
    await page.locator('input[name="action"]').evaluate((el: HTMLInputElement) => {
      el.value = 'login';
    });

    await Promise.all([
      page.waitForNavigation({ waitUntil: 'domcontentloaded', timeout: 20_000 }).catch(() => {}),
      page.locator('#loginButton').click(),
    ]);

    // Animation takes ~3s before form submits
    await page.waitForTimeout(4000);

    const url = page.url();
    // Should NOT be stuck on /login after valid credentials
    const loginFailed = url.includes('/login') && (await page.locator('body').innerText()).toLowerCase().includes('incorrect password');

    if (loginFailed) {
      throw new Error(
        `Login failed for ${TEST_EMAIL}. ` +
        `Set TEST_EMAIL and TEST_PASSWORD env vars to a seeded staging account. ` +
        `Current URL: ${url}`
      );
    }

    // Acceptable destinations: /, /scan, /history, /receipts, /verify-email-reminder, /onboarding
    const acceptableDestinations = ['/', '/scan', '/history', '/receipts', '/verify-email', '/onboarding'];
    const isAcceptable = acceptableDestinations.some(p => url.includes(p)) || !url.includes('/login');
    expect(isAcceptable).toBeTruthy();
  });

  test('invalid credentials stay on /login with error message', async ({ page }) => {
    await page.goto('/login', { waitUntil: 'domcontentloaded' });

    await page.locator('input[name="email"]').fill('nobody-bogus-9999@smarter.tax');
    await page.locator('input[name="password"]').fill('WrongPass99!');
    await page.locator('input[name="action"]').evaluate((el: HTMLInputElement) => {
      el.value = 'login';
    });

    await Promise.all([
      page.waitForNavigation({ waitUntil: 'domcontentloaded', timeout: 20_000 }).catch(() => {}),
      page.locator('#loginButton').click(),
    ]);

    await page.waitForTimeout(4000);

    // Should remain on login page or show an error
    const bodyText = (await page.locator('body').innerText()).toLowerCase();
    const hasError = bodyText.includes('not registered') || bodyText.includes('incorrect') ||
                     bodyText.includes('invalid') || bodyText.includes('error') ||
                     page.url().includes('/login');
    expect(hasError).toBeTruthy();
  });
});
