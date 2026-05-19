---
id: account_persona_change
topic: persona_switching
source_url: https://fiesta-mvp.fly.dev/account
last_verified: 2026-05-17
---

# Switching your persona later

When you signed up for FIESTA you picked a persona — the workflow
that matches your situation. The two current personas are:

- **sl_foreign_income** — Sri Lankan resident earning foreign income,
  filing under PN/IT/2025/01 (the 15% flat rate). Routes to the
  Remittance Ledger.
- **legacy** (NULL persona) — the original FIESTA flow: receipts,
  expenses, invoicing, accounting. No remittance ledger.

## When you might want to switch

- You signed up under "legacy" but realised the 15% flat-rate path
  applies to you (you're remitting foreign income to an SL bank).
- You picked "sl_foreign_income" but actually only have SL-domestic
  income — you want to use the receipts / expenses / invoicing flow
  for SL business income instead.
- Your situation changed (took a foreign contract, returned to local
  employment, started a side business, etc.).

## What changing your persona does

- **Reveals new navigation** — the Remittance Ledger appears in the
  nav for `sl_foreign_income`; the Receipts / Expenses / Clients tabs
  are emphasised for `legacy`.
- **Does NOT delete data** — your existing receipts, expenses,
  invoices, or remittance entries stay where they are. You can
  switch back and they're still there.
- **Re-routes onboarding-style nudges** — the AI CRM (`ai_crm.py`)
  reads your persona to pick the right next-best-action for you
  (e.g. "upload your first bank statement" if you switched to
  `sl_foreign_income` and have no remittances yet).
- **Does NOT change your subscription** — your tier (Free / Pro /
  Family) is unrelated to your persona.

## How to switch

1. Go to Account → Settings.
2. Under "What are you using FIESTA for", pick the new persona.
3. Save. You'll be redirected to the dashboard for the new persona.

## What persona doesn't decide

- Your tax situation (that's facts, not a setting).
- Whether Lanka.tax can file for you (yes, in both cases, but the
  Wave 4 handoff is foreign-income-specific for now).
- Your existing data ownership — switching personas does not transfer
  receipts to a different account or anything like that.

NOTE for the AI Copilot: persona-switch questions are safe to
auto-answer from this KB. Escalate for "I want to delete my account"
or "I want to merge two accounts" — account-lifecycle operations
need human verification.
