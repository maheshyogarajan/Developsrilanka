---
title: "Sri Lanka foreign-income tax 2025/26 — deadlines, rates, and what to file"
slug: sri-lanka-foreign-income-tax-2025-26-deadlines-rates
date: 2026-05-24
updated: 2026-05-24
author: FIESTA
summary: "Year-specific guide to filing foreign-income tax in Sri Lanka for the 2025/26 year of assessment — the dual-track 15% cap, quarterly instalment dates, the 30 November filing deadline, and what to assemble before you start."
keywords:
  - sri lanka tax 2025 2026
  - sri lanka tax deadline 30 november
  - sri lanka tax rates 2025
  - sri lanka quarterly tax instalment
  - foreign income tax sri lanka 15 percent
faq:
  - q: "When is the Sri Lankan tax return due for 2025/26 income?"
    a: "The Return of Income for the year of assessment 2025/26 (1 April 2025 to 31 March 2026) is due on 30 November 2026. Tax payable on assessment is also due on the same date. Late filing penalties apply under the Inland Revenue Act."
  - q: "What are the quarterly instalment dates?"
    a: "Under §90 of the IRA, quarterly instalments are due on 15 August, 15 November, and 15 February of the year of assessment, and 15 May of the following year. For 2025/26: 15 August 2025, 15 November 2025, 15 February 2026, and 15 May 2026. Instalment payers are individuals with assessable income from business, investment, or other sources, or from employment where the employer does not withhold under §83."
  - q: "What is the 15% cap on foreign income?"
    a: "From the 2025/26 year of assessment, foreign-sourced taxable income above the first Rs 1 million taxable band is taxed at a flat 15% (per the gazetted dual-track structure). This is a meaningful relief versus local-source income at the same level, which would face progressive rates rising to 36%. The cap applies only to genuinely foreign-source income — see our guide on residency and source rules for the line."
  - q: "Has personal relief changed for 2025/26?"
    a: "Personal relief for 2025/26 is Rs 1,800,000. This is up from the Rs 1,200,000 figure used in earlier amendments. Senior citizens (age 60+) get an additional Rs 500,000 under §51, for a total of Rs 2,300,000 of relief."
---

The 2025/26 year of assessment runs from **1 April 2025 to 31 March 2026** under the Sri Lankan Inland Revenue Act. If you have foreign-sourced income that is remitted to Sri Lanka during this period — or if you are a Sri Lankan tax resident with assessable foreign income on any basis — this guide tells you what's due, when, and how the dual-track rates work.

If you're not yet sure whether you owe Sri Lankan tax on foreign income at all, start with the companion article on [how Sri Lankans abroad pay tax on foreign income](/articles/how-sri-lankans-abroad-pay-tax-on-foreign-income) — it covers the residency test (§69) and the source rules (§71). This article assumes you've cleared those gates and you're now in filing territory.

## The dates that matter

For the 2025/26 year of assessment:

- **Year of assessment ends:** 31 March 2026.
- **First instalment due (§90):** 15 August 2025.
- **Second instalment due:** 15 November 2025.
- **Third instalment due:** 15 February 2026.
- **Fourth and final instalment due:** 15 May 2026.
- **Return of Income due:** 30 November 2026.
- **Final tax payable on assessment:** 30 November 2026.

Two of those dates are non-negotiable:

1. **30 November 2026** — the filing deadline. Late filing triggers penalties under the IRA. Even if you can't pay in full, file on time and pay what you can; the Commissioner-General has discretion on settling balances, but no discretion to waive non-filing.
2. **The instalment dates** — if you are an instalment payer (most foreign-income earners are, because foreign employers typically don't operate Sri Lankan PAYE / APIT). Missing an instalment doesn't extend the filing date — it just adds a separate penalty on the instalment.

## Who has to pay quarterly?

Under **§90 of the IRA**, you are an "instalment payer" if you derive or expect to derive assessable income from:

- **Business** — freelance work, consulting, sole-proprietor income.
- **Investment** — rental, interest, dividends, royalties.
- **Other income** — anything not employment.
- **Employment income where the employer doesn't withhold under §83** — this is the catch-all for foreign employees: an Indian, US, UK, Australian, or UAE employer is not a Sri Lankan withholding agent, so they don't deduct Sri Lankan PAYE / APIT at source. You then owe the full liability quarterly.

The instalment calculation (§90(3)) is:

> (A − C) ÷ B
>
> where A = estimated tax payable for the full year, B = number of instalments remaining including the current one, C = tax already paid for the year (prior instalments + withholding).

If your foreign income is roughly even across the year, the simple version is: divide your estimated full-year tax into four equal quarters. If your income lumps (a year-end bonus, a single big invoice in March), front-load the later instalments to match.

## The 2025/26 rate structure

The 2025/26 year introduced a **dual-track structure** that distinguishes foreign-source from local-source income for the bands above the first taxable band.

**Personal relief:** Rs 1,800,000 (up from Rs 1,200,000 in 24/25). **Senior citizen extra relief:** Rs 500,000 (§51, age 60+).

After deducting relief, the bands work like this:

### First taxable band (shared, regardless of source)
- First Rs 1,000,000 of taxable income → **6%**.

### Above the first band: source matters

**Foreign-sourced taxable income above Rs 1M:**
- **15% flat (cap)** — the same rate applies all the way up.

**Local-sourced taxable income above Rs 1M:**
- Rs 1.0M – 1.5M → 18%
- Rs 1.5M – 2.0M → 24%
- Rs 2.0M – 2.5M → 30%
- Above Rs 2.5M → **36%**

This means a Sri Lankan resident earning Rs 10 million of pure foreign-source income (after relief: Rs 8.2M taxable) pays:

- Rs 60,000 (6% on first Rs 1M),
- Rs 1,080,000 (15% on the remaining Rs 7.2M)
- **= Rs 1,140,000 total, an effective rate of 13.9% on taxable income.**

The equivalent local-source earner with Rs 10M would face progressive rates rising to 36%, paying considerably more. The dual-track design is deliberate — the policy intent is to encourage Sri Lankan-resident professionals to bring foreign earnings into the local financial system.

The rates above are pinned in FIESTA's `slabs.yaml` and update on gazette publication. The source for the dual-track structure is the **First Schedule, Part I of the IRA** as amended through the 2025/26 gazette cycle.

## How source splitting actually works

The slabs distinguish "foreign-source" from "local-source" income, but most filers have a mix. The split is straightforward:

```
foreign_share = foreign_gross / (foreign_gross + local_gross)
```

Applied to taxable income above the first Rs 1M:

- The first Rs 1M (taxable) is at the shared 6% regardless of source.
- Of the remaining taxable income, the `foreign_share` portion is taxed at the 15% flat rate; the local portion walks the progressive 18/24/30/36 ladder.

Worked example — a freelancer with Rs 4M Sri Lankan-source consulting income and Rs 6M USD-remitted foreign-source income (gross, before relief):

- Gross combined: Rs 10M.
- After Rs 1.8M relief: Rs 8.2M taxable.
- Foreign share of the gross above relief: 6 / 10 = 60%.
- First Rs 1M at 6% (shared) = Rs 60,000.
- Remaining Rs 7.2M split: Rs 4.32M foreign at 15% = Rs 648,000; Rs 2.88M local walks the ladder (500K at 18% = 90K, 500K at 24% = 120K, 500K at 30% = 150K, 1.38M at 36% = ~497K) = Rs 857,000.
- **Total Sri Lankan tax: Rs 1,565,000.**

(FIESTA computes this to the cent and shows the bracket-by-bracket math in the tax preview — see [/tax-preview](/tax-preview).)

## What to file

The form is the **Return of Income (Form IT/IT/02)**, lodged with the Inland Revenue Department via the IRD e-services portal.

You'll need:

1. **Personal details** — TIN (Taxpayer Identification Number) and registered taxpayer profile.
2. **Bank statements for the Sri Lankan accounts that received foreign remittances** during 1 April 2025 to 31 March 2026.
3. **A reconciliation** of remittances showing source (employer / client / asset) and original-currency amount.
4. **Foreign tax certificates** (W-2, P60, payslip, etc.) for any foreign tax credit claim under §80.
5. **Local-source income records** — Sri Lankan employer payslips, business invoices, rental receipts, dividend statements.
6. **Deduction support** — receipts for any qualifying payments under §52, business expense records if you have business income.

If you've made quarterly instalment payments, the IRD's e-portal will show those credits when you log in. The return calculates final tax = total liability − instalment credits − foreign tax credit. A refund (where credits exceed liability) is processed by the IRD; a balance owing is due on 30 November 2026.

## What changes year-over-year — and what doesn't

For most foreign-income filers, the **structure is stable**:
- The residency test (§69) is unchanged.
- The source rules (§71) are unchanged.
- The remittance basis for resident individuals is unchanged.
- The 30 November filing deadline is unchanged.
- The §90 quarterly instalment dates are unchanged.
- The §80 foreign tax credit mechanism is unchanged.

What does change (and what you should re-check annually):
- **Personal relief amount** — moved from Rs 1.2M to Rs 1.8M for 2025/26.
- **Bracket widths and rates** — the dual-track structure (15% foreign cap, 18/24/30/36 local ladder) is new for 2025/26.
- **Specific gazette amendments** — e.g. relief categories, qualifying payment definitions.
- **DTAA updates** — Sri Lanka periodically renegotiates treaties; check the IRD website for the operative text.

## Where to get help

- For the rate calculator with bracket-by-bracket math and the IRA section behind each step, use FIESTA's [tax preview tool](/tax-preview).
- For the residency / source companion, see [how Sri Lankans abroad pay tax on foreign income](/articles/how-sri-lankans-abroad-pay-tax-on-foreign-income).
- To start your return for 2025/26, [add your first remittance](/remittance/new).
- For situations the article doesn't cover (DTAA interpretation, multi-jurisdiction structures, partial-year residency), the IRD operates a help desk and FIESTA offers a [consultation booking](/consultant/book).

## The takeaway

- **2025/26 ends 31 March 2026. Return due 30 November 2026.**
- **Quarterly instalments:** 15 Aug 2025, 15 Nov 2025, 15 Feb 2026, 15 May 2026.
- **Foreign-source income above the first Rs 1M of taxable income is capped at 15%.**
- **Local-source income above that band climbs to 36% at the top of the ladder.**
- **Personal relief is Rs 1.8M; senior relief adds Rs 0.5M.**
- **Foreign tax credit under §80 prevents double taxation — keep the foreign certificates.**

Mark the dates. The Sri Lankan tax year ends 31 March 2026 and the clock to 30 November starts immediately after.
