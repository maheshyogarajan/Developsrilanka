---
id: cbsl_middle_rate_rule
topic: fx_conversion
source_url: https://www.cbsl.gov.lk/en/rates-and-indicators/exchange-rates
last_verified: 2026-05-17
---

# Why IRD requires CBSL middle rate (not your bank's rate)

For Sri Lanka income tax purposes, foreign-currency credits must be
converted to LKR using the **Central Bank of Sri Lanka (CBSL) middle rate
on the date the credit was received** by your SL bank.

Your bank's rate is usually different from CBSL middle rate:
- The bank's "buying rate" includes a spread that benefits the bank.
- For inward remittances, this can be 1-3% below CBSL middle rate.
- For tax purposes, only the CBSL middle rate is IRD-defensible.

What FIESTA does:
- For each remittance, we look up the CBSL middle rate on the credit date.
- If CBSL has published a rate for that date, we mark it **Verified CBSL rate**
  (`source: cbsl` or `cbsl_cached`) — this IS IRD-defensible.
- If we can't reach CBSL but have a recent proxy (open-er-api, ECB-sourced),
  we mark it **Proxy rate — confirm with CBSL before filing**. Do NOT file
  with a proxy rate; replace it with the official CBSL rate before submission.
- If neither is available (e.g. weekend, public holiday, or historical date
  where CBSL hasn't archived a rate), the user must enter the rate manually.
  Take it from cbsl.gov.lk for the nearest published business day and cite
  the source.

Common gotchas:
- Weekends and public holidays have no CBSL publication. Most banks credit
  using the previous business day's CBSL rate; check your bank's statement.
- The "telegraphic transfer (TT) buying" column on the CBSL site is NOT
  the middle rate. The middle rate is the midpoint between TT buying and
  TT selling.
- For currencies CBSL does not publish (rare: some less-traded crosses),
  use the cross-rate via USD: foreign → USD via published rate → USD → LKR
  via CBSL.

Audit posture:
- IRD has accepted the CBSL middle rate consistently in past assessments.
- A bank's own buying rate, used as-is, is a common audit flag — IRD will
  recompute at CBSL middle rate and assess the difference + penalties.
