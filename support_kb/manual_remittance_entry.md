---
id: manual_remittance_entry
topic: manual_entry_field_meanings
source_url: https://fiesta-mvp.fly.dev/remittance/new
last_verified: 2026-05-17
---

# Manual remittance entry — what each field means

The form at /remittance/new is the one-row-at-a-time path. Use it when
your bank statement is image-only, when a single payment slipped past
the importer, or when you want full control over what FIESTA stores.

## Required

- **Remittance date** — the date the bank credited the foreign-currency
  amount to your SL account. NOT the date you raised the invoice or
  the date the payer initiated the transfer. Use the credit date as
  shown on your SL bank statement.

- **Foreign currency** — the original currency of the credit. Pick from
  the dropdown (USD, GBP, EUR, AUD, CAD, AED, SGD, JPY, CHF, NZD).
  If your currency isn't listed, contact support to request it.

- **Foreign amount** — the amount in the foreign currency, as shown on
  your bank statement. Use the gross amount BEFORE the bank's
  conversion to LKR. Numbers only; commas are stripped automatically.

## Strongly recommended

- **LKR amount (bank rate)** — the LKR amount the bank actually
  credited to your account. This is the rate the bank used; we keep
  it for reconciliation against your bank statement. It is NOT the
  CBSL rate — that's a separate column we compute.

- **Payer name** — who sent the money. As shown on your bank's credit
  advice (e.g. "UPWORK GLOBAL INC", "CLIENT-NAME-PVT-LTD"). Used in
  the evidence pack and helps you recognise repeat payers.

- **Source country (ISO 2)** — two-letter country code (US, GB, AU,
  CA, AE, SG, JP, CH, NZ, IE, DE, NL, ...). Used for DTA analysis.

## Auto-populated (you can override)

- **CBSL rate** — the CBSL middle rate for the remittance date. We
  look this up automatically; you can override by typing your own
  value. If you do, it's stored with `source: manual` and FIESTA
  caches it for the next entry on the same date.

- **CBSL rate source** — a label: `cbsl`, `cbsl_cached`, `ecb_proxy`,
  or `manual`. Only `cbsl` and `cbsl_cached` are IRD-defensible
  for filing. `ecb_proxy` must be replaced with the official CBSL
  rate before you file the return.

## Optional but useful

- **Foreign tax withheld (amount + currency)** — if foreign tax was
  withheld at source (e.g. salary with UK PAYE deductions, US
  freelancer with backup withholding), record the withheld amount
  and the currency. Used for DTA tax-credit claims.

- **Notes** — free text for your own reference. Common uses: project
  name, invoice number you raised, milestone description, payment
  schedule.

## Computed automatically

- **LKR amount (CBSL)** = foreign amount × CBSL rate. This is the
  number that goes on your IRD return.

- **Tax year** — derived from the remittance date. SL tax year runs
  1 April to 31 March (e.g. 1 Apr 2026 to 31 Mar 2027 is year of
  assessment 2026/27, internally stored as "2026-27").

## After saving

The entry lands on /remittance/dashboard. Each entry has a
completeness status:
- **ird_ready** — has foreign amount, currency, payer, CBSL rate
  from a verified source. Safe to file.
- **evidence_ready** — has the numbers but missing payer / country.
  File-able but evidence pack is incomplete.
- **incomplete** — missing required fields. Edit before filing.

NOTE for the AI Copilot: field-meaning questions are safe to auto-answer.
"What does X mean?", "Where do I find Y?" map to this KB. Escalate for
"my entry disappeared", "the numbers don't match what I entered" —
operational issues, not usage questions.
