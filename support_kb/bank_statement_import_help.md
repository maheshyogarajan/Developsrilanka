---
id: bank_statement_import_help
topic: importer_usage
source_url: https://fiesta-mvp.fly.dev/remittance/import
last_verified: 2026-05-17
---

# Bank-statement import — how to use the importer

The importer at /remittance/import accepts a PDF or CSV bank statement,
extracts the credit lines, asks Gemini to classify which are foreign
remittances, and lets you confirm them on a review page before they
land in your ledger.

## Supported formats

- **PDF** — text PDFs exported from your bank's online portal. Most
  major SL banks (Commercial Bank, HNB, NDB, Sampath, BOC, Seylan,
  DFCC, NSB, Pan Asia) emit text PDFs by default.
- **CSV** — comma, semicolon, or tab separated. UTF-8, UTF-8 BOM,
  Windows-1252, and Latin-1 encodings all work.

**NOT supported**: scanned PDFs (image-only). If your statement is a
scan, either (a) export a text PDF from the portal, or (b) ask your
bank for a CSV export, or (c) enter remittances manually at
/remittance/new.

## How the importer works

1. You upload the file (max 8 MB).
2. We sniff magic bytes to confirm it's actually a PDF or CSV (an
   .xlsx renamed to .pdf is rejected here — Wave H hardening).
3. We hash the file (SHA-256) and check if you've imported the same
   file in the last 7 days. If yes, we warn and ask you to tick a
   "duplicate-OK" box before continuing.
4. We redact account numbers, card numbers, and running balances
   from the text BEFORE sending to Gemini (Wave H R1 PII rule).
5. Gemini classifies each credit as foreign remittance or not, with
   a confidence label (high / medium / low).
6. You land on a review page with one row per detected credit. Tick
   the ones you want to import, edit any field, then click Confirm.
7. We auto-look-up the CBSL rate for each remittance date and freeze
   it on the record.

## Daily quota

You can import up to **10 statements per 24 hours**. The cap exists
to prevent runaway Gemini cost in case of bot abuse or accidental
loop. If you hit it, come back tomorrow or use manual entry.

## What gets skipped

- **Debit lines** (money OUT) — we only import credits.
- **Internal transfers** ("FUND TRANSFER FROM SELF", "OWN A/C") —
  Gemini classifies these as non-foreign and we skip them.
- **Salary from a local employer** — classified as non-foreign.
- **Ambiguous rows** (missing foreign currency or amount) — skipped
  with a count shown after confirmation. You can add them manually.

## Common issues

- **"Couldn't extract any credits"**: PDF is image-only OR the CSV
  has an unusual format. Try exporting a CSV from the bank's portal.
- **"This exact file was already uploaded"**: duplicate detection.
  Tick the duplicate-OK checkbox if you really do want to re-import.
- **Confidence is "low" for a row I know is foreign income**: edit
  the row in the review page — set foreign currency + amount yourself.
  Your edit overrides Gemini's classification.

NOTE for the AI Copilot: importer questions are safe to auto-answer
from this KB. Escalate for "the importer hung", "my upload was lost",
"I got billed for an import I didn't do" — these are operational
incidents, not usage questions.
