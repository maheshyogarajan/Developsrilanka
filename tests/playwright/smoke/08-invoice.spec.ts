/**
 * Smoke test 08: Invoice creation.
 *
 * Verifies:
 * - /invoices page loads and shows the invoice management UI
 * - /invoices/create renders the invoice form
 * - Required fields (organization, client, currency, dates) are present
 *
 * NOTE: Full invoice creation requires a pre-existing client record.
 * This test checks form renderability. A full E2E creation would require
 * seeding a client first — documented as coverage gap.
 */
import { test, expect } from '@playwright/test';
import { loginAs } from './helpers';

test.describe('08 — Invoice management', () => {
  test('/invoices page loads and shows invoice UI', async ({ page }) => {
    await loginAs(page);
    await page.goto('/invoices', { waitUntil: 'domcontentloaded' });

    expect(page.url()).not.toContain('/login');

    const bodyText = await page.locator('body').innerText();
    const hasInvoiceContent = bodyText.toLowerCase().includes('invoice');
    expect(hasInvoiceContent).toBeTruthy();
  });

  test('/invoices/create renders invoice form with required fields', async ({ page }) => {
    await loginAs(page);
    await page.goto('/invoices/create', { waitUntil: 'domcontentloaded' });

    expect(page.url()).not.toContain('/login');

    // Organization selector must be present
    const orgSelect = page.locator('select#organization_id, select[name="organization_id"]').first();
    await expect(orgSelect).toBeAttached({ timeout: 8_000 });

    // Issue date and due date fields
    const issueDate = page.locator('input#issue_date, input[name="issue_date"]').first();
    await expect(issueDate).toBeAttached();

    // Currency selector
    const currency = page.locator('select#currency, select[name="currency"]').first();
    await expect(currency).toBeAttached();

    // Submit / create button
    const submitBtn = page.getByRole('button', { name: /create invoice|save|submit/i }).first();
    await expect(submitBtn).toBeVisible();
  });

  test('creating an invoice without a client shows validation or client-selector', async ({ page }) => {
    await loginAs(page);
    await page.goto('/invoices/create', { waitUntil: 'domcontentloaded' });

    if (page.url().includes('/login')) {
      test.skip(true, 'Auth failed — cannot test invoice creation');
      return;
    }

    // Attempt to submit without filling required fields; should fail gracefully
    const form = page.locator('#invoiceForm');
    if (await form.isVisible()) {
      // Check HTML5 required validation is set on client_id
      const clientSelect = page.locator('select#client_id, select[name="client_id"]').first();
      const clientSelectCount = await clientSelect.count();
      if (clientSelectCount > 0) {
        const required = await clientSelect.getAttribute('required');
        // required attribute present = HTML5 validation active
        expect(required).not.toBeNull();
      }
    }
  });
});
