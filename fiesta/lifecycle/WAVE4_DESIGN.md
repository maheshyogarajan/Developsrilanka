# FIESTA Wave 4 — Lifecycle (X3 + S11) Design

Status: v1.0 (2026-05-20). Branch `wave4/lifecycle-x3-s11`.
Council brief: `working files/strategic/council/_briefs/fiesta_council_brief.json`.
PM doc: `THE_PATH_20260520.md` Week 4-5.

## Why this exists

Three things turn a one-shot filing tool into a year-round companion:
1. **Tax year transition (X3).** Customers don't see the tool once a year on
   30 November. They live in it across April–March, accumulating evidence as
   the year unfolds.
2. **Cadence tracking (S11).** Coverage gaps are the single most common
   IRD audit finding (10 monthly rent payments but only 7 receipts in the
   ledger). Detecting them automatically + nudging on cadence is what
   moves FIESTA from spreadsheet replacement to compliance partner.
3. **Audit-defensible ledger.** Every action (invoice add, reminder sent,
   transition) must be reconstructable 7 years later from a single
   append-only log.

## X3 — Year-End Transition

### TaxYear model
- `year_label`: short "2025/26" or long "2025/2026". Stored short, exposed
  both via `short_label` / `long_label` properties for round-trip with
  doclens-v1 T10 schema (long) and SF EmailTemplate names (long) but a
  short URL slug in FIESTA UI.
- `start_date / end_date`: 1 Apr / 31 Mar of the relevant calendar years.
- `filing_window_close`: 30 Nov in calendar year of YoA end. **Off-by-one
  bait** — 25/26 ends 31 Mar 2026, filing closes 30 Nov 2026, not 2025.
  Encoded once here so no caller can miscompute it.
- Pydantic v2 `ConfigDict(frozen=True)` — immutable, cheap to compare.

### current_tax_year(now)
SL local arithmetic (UTC+5:30, no DST). 31 Mar 18:30 UTC = 1 Apr 00:00
Colombo, which is **already in the new year**. Tests pin this boundary.

### filing_window_status
Four states: `open / closing_soon / overdue / filed`. `closing_soon` fires
at T-30 days (council brief X3) so the UI banner aligns with the email
reminder schedule.

### transition_customer_to_new_year (pure function)
Pre-conditions enforced (block on violation):
1. New year strictly after current.
2. If prior-year return is OVERDUE (past 30 Nov + unfiled) -> block. The
   customer can resume but FIESTA flags "file 25/26 first" because parallel-
   year work with an unfiled prior year is the most common audit-trail
   confusion source.

Carry-over rules:
- Service Providers: all carry; auto-renew prompt if `contract_end_date`
  falls inside the closing year.
- Rental Agreements: same prompt pattern (364-day standard SL leases).
- Bank Details: copied silently (accounts don't shift annually).
- Persona: copied silently (changes are explicit user actions).
- Recurring Expenses: caller filters to `is_recurring=True` before passing.

Function is pure (no DB write) so unit tests use plain `_DummyCustomer`
duck-types. Production wiring lives in `rollover_scheduler.run_daily_pass`.

### rollover_scheduler — Celery beat (daily)

Runs at **00:30 Asia/Colombo = 19:00 UTC previous day**. Single daily pass
keeps the cron entry simple and idempotent. Inside the pass, six reminder
trigger dates are computed per customer relative to YoA boundaries:

| Offset (days) | Event |
| --- | --- |
| year_end - 1 | year_closing_tomorrow (email) |
| year_end + 0 | year_ended_today (silent, audit only) |
| year_end + 1 | new_year_transition_invite (email — X3 main) |
| filing_close - 30 | filing_deadline_approaching |
| filing_close - 7 | filing_deadline_approaching |
| filing_close - 1 | filing_deadline_approaching |
| filing_close + 1 | filing_deadline_overdue |
| filing_close + 14 | filing_deadline_overdue (escalation) |

Idempotency: each decision carries an `idempotency_key` of form
`cust{id}:{event}:{date}`. The audit log is queried for the prior 7 days
before each pass so a worker restart can't double-fire.

The Celery wiring (decorator + task) lives in repo-root
`rollover_scheduler_tasks.py` (NOT in this package) to keep
`fiesta.lifecycle` testable without Celery imports.

## S11 — Invoice Cadence Tracking

### Invoice model
- `amount_lkr: Decimal` (mandatory). `amount_foreign + currency` optional;
  caller converts at invoice_date FX rate (out of scope here — Phase 2
  fiesta.tax.fx handles it).
- `ira_categorization` enum: aligns with fiesta.tax.types.Income components
  + fiesta.compliance bucket names. Drives where the invoice surfaces in
  the return preparation flow.
- `status: issued | paid | pending` — separate state machine from
  cadence (cadence cares about invoice dates, not payment dates).

### CadenceCheck algorithm
1. Sort invoices by `invoice_date`.
2. Compute inter-invoice intervals (days).
3. mean + stddev + coefficient_of_variation (CV = stddev / mean).
4. Bucket `actual_cadence`:
   - CV > 0.30 -> "irregular"
   - else mean <= 45d "monthly", <=120d "quarterly", <=220d "biannual",
     <=400d "annual".
5. Coverage gaps: any interval > 1.5x nominal period -> count missing
   cycles = round(interval / period) - 1.

### Cadence regularity thresholds (CV)
- 0.00 to 0.15: regular (passes silently).
- 0.15 to 0.30: mildly irregular (no flag, but visualised).
- > 0.30: irregular (flag set, surfaces to X6 compliance gate).

These values are conservative on the **overdetection-OK** principle that
underwrites Wave 4 related-party detection — false positives create a
"review this SP" prompt; false negatives hide audit risk.

### upcoming_invoice_reminder
- T-5 days before next expected invoice date -> `monthly_invoice_due_soon`.
- T+15 days (half-period) past expected date with no invoice -> `monthly_invoice_missing`.
- Irregular cadence with coverage gaps -> `next_due_after_irregular_gap`.

### S11 -> X6 compliance link
`is_above_market_rate(avg_amount, market_rate, ratio=1.25)` is the bridge.
When the cadence is irregular AND the average invoice is > 25% above market,
fiesta.compliance.market_rates_table X6 gate refuses silent deduction
acceptance — the customer must explain or split the invoice. Both signals
must fire jointly to avoid false positives on legitimately premium SPs
(licensed auditors, specialist consultants) whose cadence is regular.

## Audit Log

`LifecycleAudit` is the facade; `InMemoryAuditStore` is the test store;
`SqlAlchemyAuditStore` (to be built in wiring phase) is the production
store backed by a new `lifecycle_audit_events` table.

Each row is immutable. "Edit invoice X" -> NEW row with event_type
`invoice.edited` and payload `{before, after}`. Never UPDATE.

`payload_hash` = SHA-256 of sorted JSON payload — tamper detection at
IRD audit time. If a row's hash doesn't match its payload, alarm.

`export_customer_year_ledger(customer_id, year_label)` returns the
JSON-serialisable dict that the PDF generator renders. PDF generation
itself lives in repo-root `pdf_utils.py` to keep this package free of
ReportLab/WeasyPrint dependencies.

## Templates

- `templates/lifecycle/year_end_transition.html` — the X3 main page that
  greets the customer on 1 April. Bootstrap 5 + the existing FIESTA
  layout. CTAs route to `lifecycle.service_providers`, `lifecycle.rental_agreements`,
  etc. (blueprints to be wired when the Flask routes layer is built).
- `templates/lifecycle/s11_invoice_cadence.html` — per-SP cadence view.
  Monthly grid (12 dots colour-coded paid/issued/pending/missing).
  Audit-risk summary (Low/Medium/High based on irregular_flag x above_market).
  Quick-add CTA for next-due invoice.

## V1.1 Roadmap

1. **Auto-categorise invoice (LLM-driven).** Doclens-v1-ported pipeline
   reads the invoice PDF, classifies into IRACategory, pre-fills period
   start/end + amount. Customer confirms in one click.
2. **Invoice OCR.** Extend doclens-v1 with an `invoice_extraction` schema
   (parallel to T10). Useful for the 5% of FIESTA customers who still
   receive PDF invoices over email rather than ledger-import.
3. **Multi-year cadence comparison.** Compare 26/27 cadence to 25/26 for
   each SP — large variance triggers an X6 challenge.
4. **Customer-edited expected cadence.** Currently auto-inferred; let the
   customer override (e.g. quarterly retainer where 3 of 4 quarters
   already paid -> avoid "missing 1Q26" false alarm in May 26).

## Design Decisions for CEO

1. **Auto-renew rentals vs. manual confirm.**
   *Chosen: manual confirm.* Rental agreements that lapsed in the closing
   year route to a prompt block on the X3 transition page (Y/N per
   agreement). Silently renewing would commit the customer to a tax
   position they may not hold — wrong direction relative to "client-of-IRD"
   posture.

2. **Filing deadline reminder schedule.**
   *Chosen: T-30 / T-7 / T-1 / T+1 / T+14.* Five touches across 45 days.
   First touch matches `closing_soon` UI banner threshold. Fewer touches
   risks under-reminding; more risks notification fatigue.

3. **Idempotency window.**
   *Chosen: 7 days.* `run_daily_pass` queries audit log for prior 7 days
   when deciding whether to dispatch. Catches week-long worker outages
   without re-sending older reminders.

4. **Cadence detection minimum data.**
   *Chosen: 2 invoices required for cadence; 1 returns a low-confidence
   check.* Below 2, we can't compute intervals. Above 12, the CV is
   well-defined; between 2-12 we still emit a check but the irregular_flag
   is suppressed until 4+ samples (to avoid early-onboarding noise).
   **NOTE: this latter rule is in the design doc but NOT implemented in
   v1.0 detect_cadence — flag for the council review of Wave 4 follow-up.**

## PM findings (suggested)

| Finding | Lens | Why |
| --- | --- | --- |
| X3 transition makes FIESTA the system of record for SL tax-year boundaries; need DR plan for 1 April outage. | P3 Architect / P11 Resilience | A failed 1 April pass strands customers in the wrong year. Need a "catch-up pass" semantics + monitoring. |
| S11 cadence detection is O(N log N) per customer per pass. At 10K customers x 20 SPs the daily pass is ~50ms. Linear; watch growth. | P10 Cost-to-Run | Beat schedule + Celery worker capacity. |
| S11-> X6 link tightens compliance posture: above-market AND irregular -> mandatory user explanation logged to audit. Strengthens audit-defence narrative. | P6 Compliance / P9 Audit-defence | Cross-link in customer "explain this SP" flow. Re-uses existing audit log surface. |
| X3 carry-over rules embed assumption that SPs and rentals continue across years by default. Surface in UI so customer sees it as a *decision* (renew/end) rather than a silent commit. | P11 Resilience / P15 User-trust | Two-row prompt block in the welcome screen does this. |

## File inventory

```
fiesta/lifecycle/
  __init__.py
  audit_log.py
  invoice_cadence.py
  reminders.py
  rollover_scheduler.py
  year_end.py
  WAVE4_DESIGN.md
templates/lifecycle/
  s11_invoice_cadence.html
  year_end_transition.html
tests/lifecycle/
  __init__.py
  test_x3_s11.py
```

Lines (approx): 1,800 module + 350 templates + 400 tests = 2,550.
