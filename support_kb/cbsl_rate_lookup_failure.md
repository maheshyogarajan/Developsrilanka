---
id: cbsl_rate_lookup_failure
topic: fx_failure_recovery
source_url: https://www.cbsl.gov.lk/en/rates-and-indicators/exchange-rates
last_verified: 2026-05-17
---

# What to do when no CBSL rate is available

For most remittance dates FIESTA will auto-populate the CBSL middle
rate. Sometimes it can't — here's what's happening and what to do.

## Why a CBSL rate might be unavailable

1. **The date is a weekend or SL public holiday.** CBSL doesn't publish
   rates on non-business days.
2. **The date is very recent (today / yesterday in some cases).** CBSL
   publishes after the day closes; intraday queries can come back empty.
3. **The date is very old (pre-2006-11-11).** CBSL's online archive
   doesn't go back further than that on the public API.
4. **CBSL website is temporarily down.** Happens occasionally — usually
   recovers within an hour.
5. **The currency is unusual** (e.g. some less-traded crosses). CBSL
   only publishes ~30 currencies.

## What FIESTA does automatically

Tiered fallback (per fx_rate_service.py):

- **Tier 1 — cache hit**: if we've looked up that (currency, date)
  before, we re-use the cached value. Marked `cbsl_cached` — same
  IRD defensibility as `cbsl`.
- **Tier 2 — live CBSL scrape**: works for any date back to 2006-11-11
  IF CBSL is reachable AND the date is a business day. Marked `cbsl`.
- **Tier 3 — open-er-api proxy** (TODAY only): if CBSL is unreachable
  AND the request is for today's rate, we fall through to a free
  ECB-sourced rate. Marked `ecb_proxy`. **Not IRD-defensible** —
  the UI flags it for manual CBSL confirmation before you file.
- **Tier 4 — manual entry**: if none of the above works (historical
  date, weekend, CBSL down), the form lets you type the rate yourself.
  Marked `manual`. Cite cbsl.gov.lk for the nearest published business
  day in your evidence pack.

## What to do as the user

If you see a remittance with rate source `ecb_proxy` or `manual`:

1. Go to cbsl.gov.lk → Rates & Indicators → Exchange Rates.
2. Look up the published middle rate for the credit date (or nearest
   prior business day for weekend / holiday credits).
3. Edit the remittance entry and paste the official CBSL rate.
4. Print or screenshot the CBSL page for your evidence pack.

The rate-source label flips from `ecb_proxy`/`manual` to `manual` once
you save — this is your audit trail that the value came from CBSL.

## What to do if the bank credited a non-business-day amount

Most SL banks credit at the previous business day's CBSL middle rate
for weekend/holiday remittances. The credit DATE on your statement
is the weekend/holiday date, but the RATE used should be the prior
business day's CBSL rate. Use that prior business day's rate in
FIESTA — IRD has consistently accepted this.

If your bank uses a different convention (a few private-bank
relationship accounts do), match what the bank actually used and keep
the bank statement as evidence.

NOTE for the AI Copilot: rate-failure questions are safe to auto-answer
from this KB. Escalate for "IRD has assessed me at a different rate"
or "my bank disputes my rate" — these are dispute scenarios that
need human judgment.
