/**
 * Smoke test 03: Signup flow.
 * Registers a unique test user, verifies the confirmation / verify-email-reminder
 * page renders correctly.
 *
 * NOTE: The app sends a real verification email via SendGrid; this test does NOT
 * click the email link (that requires email inbox access). It only verifies the
 * registration form submits successfully and the post-registration page renders.
 *
 * Rate-limit awareness: The app limits to 3 registration attempts per IP.
 * We use a unique timestamped email each run to avoid "already registered" collisions.
 */
import { test, expect } from '@playwright/test';

test.describe('03 — Signup / Registration', () => {
  test('register page renders with email + password fields', async ({ page }) => {
    const response = await page.goto('/register', { waitUntil: 'domcontentloaded' });
    expect(response?.status()).toBe(200);

    await expect(page.locator('input[name="email"]')).toBeVisible();
    await expect(page.locator('input[name="password"]')).toBeVisible();
    await expect(page.locator('button#registerButton, button[type="submit"]').first()).toBeVisible();
  });

  test('registering a new user redirects to email confirmation page', async ({ page }) => {
    const ts = Date.now();
    const testEmail = `playwright.reg.${ts}@smarter.tax`;
    const testPassword = 'SmokeTest$2026!';

    await page.goto('/register', { waitUntil: 'domcontentloaded' });

    await page.locator('input[name="email"]').fill(testEmail);
    await page.locator('input[name="password"]').fill(testPassword);

    // Fill confirm password if present (not in form data but frontend-only validation)
    const confirmInput = page.locator('#confirmPassword');
    if (await confirmInput.isVisible()) {
      await confirmInput.fill(testPassword);
    }

    // Ensure action = register
    await page.locator('input[name="action"]').evaluate((el: HTMLInputElement) => {
      el.value = 'register';
    });

    await Promise.all([
      page.waitForNavigation({ waitUntil: 'domcontentloaded', timeout: 20_000 }).catch(() => {}),
      page.locator('#registerButton, button[type="submit"]').first().click(),
    ]);

    // Allow animation to play out
    await page.waitForTimeout(4000);

    const currentUrl = page.url();
    const pageText = await page.locator('body').innerText();

    // Accept either:
    // 1. Redirected to /verify-email-reminder (expected happy path)
    // 2. Still on /register with error (rate-limited or already exists — acceptable warning)
    const isOnVerifyPage = currentUrl.includes('verify-email') || currentUrl.includes('reminder');
    const hasSuccessSignal = pageText.toLowerCase().includes('verify') ||
                             pageText.toLowerCase().includes('check your email') ||
                             pageText.toLowerCase().includes('verification');
    const hasRateLimit = pageText.toLowerCase().includes('rate limit') ||
                         pageText.toLowerCase().includes('too many');

    if (hasRateLimit) {
      console.warn('Rate limit hit during registration smoke test — skipping assertion');
      test.skip(true, 'Rate limit reached; skipping registration result assertion');
    } else {
      expect(isOnVerifyPage || hasSuccessSignal).toBeTruthy();
    }
  });
});
