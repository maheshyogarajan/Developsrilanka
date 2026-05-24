# FIESTA SEO Articles — Backlog (Wave 6 A4 slice 1 → slices 2 & 3)

Backlog of 8 queued articles for the next slices of A4 (Wave 6 item #51). Slice 1
shipped the SEO substrate + 2 pilots (`how-sri-lankans-abroad-pay-tax-on-foreign-income`,
`sri-lanka-foreign-income-tax-2025-26-deadlines-rates`). Slices 2 + 3 will ship 4
articles each — split this list down the middle, or re-prioritise per the latest
SEO keyword data.

Each entry below has:
- title (h1 / og:title)
- slug (URL path under `/articles/<slug>`)
- target keyword (primary, used for h1 + 2-3 in-body anchors)
- target word count (range)
- 3-bullet outline
- core IRA sections to cite

How to ship one: drop a new file at `content/articles/<slug>.md` with the YAML
frontmatter (see existing pilots), restart the app (or call `seo_routes._reload_articles()`),
done. No DB migration, no admin UI.

---

## 1. NRR returnees — the Sri Lankan tax reset

- **Slug:** `non-resident-returnee-tax-sri-lanka`
- **Target keyword:** "sri lanka non resident returnee tax"
- **Word count:** 1100–1400
- **Outline:**
  - The day you re-establish residency under §69 — the 183-day count restarts.
  - How accumulated offshore savings are treated when first remitted post-return.
  - Capital-gain reset on foreign assets brought into Sri Lankan tax orbit.
- **IRA cites:** §5, §6, §7 (income definitions), §69 (residence), §71 (source), §80 (FTC).

## 2. RSU and equity comp for Sri Lankan residents at foreign tech firms

- **Slug:** `rsu-equity-compensation-sri-lanka-tax`
- **Target keyword:** "rsu tax sri lanka"
- **Word count:** 1200–1600
- **Outline:**
  - Grant vs vest vs sale — which event triggers Sri Lankan tax under §5(2)(j).
  - The dual tax exposure (vest in US/UK, sale anywhere, remit to SL) and how to use §80.
  - Practical record-keeping: vesting schedule, FX rate at vest, broker statements, foreign tax certificate.
- **IRA cites:** §5 (employment income, esp. (2)(j) shares allotted), §7 (investment), §80, §81.

## 3. The Sri Lanka–US Double Tax Treaty — what it actually does

- **Slug:** `sri-lanka-usa-dtaa-tax-treaty-guide`
- **Target keyword:** "sri lanka usa double tax treaty"
- **Word count:** 1300–1700
- **Outline:**
  - Who the treaty covers (residents of either country) and where it overrides domestic law.
  - Treaty rules for employment, business, dividends, interest, royalties, capital gains.
  - The credit method vs exemption method — and which one applies to common scenarios.
- **IRA cites:** §80, §81 (FTC framework); DTAA provisions as overlay (cite treaty articles).

## 4. The Sri Lanka–UK Double Tax Treaty — practical guide

- **Slug:** `sri-lanka-uk-dtaa-tax-treaty-guide`
- **Target keyword:** "sri lanka uk double tax treaty"
- **Word count:** 1200–1600
- **Outline:**
  - The 183-day employment exemption and when it's actually useful.
  - UK PAYE → Sri Lankan FTC mechanics for resident SL earners working for UK employers.
  - UK pension income (state pension, private pension) — source and treatment.
- **IRA cites:** §5, §69, §80, §81; SL-UK DTAA articles 4 (residence), 14 (employment), 17 (pensions).

## 5. The Sri Lanka–Australia Double Tax Treaty — practical guide

- **Slug:** `sri-lanka-australia-dtaa-tax-treaty-guide`
- **Target keyword:** "sri lanka australia double tax treaty"
- **Word count:** 1100–1500
- **Outline:**
  - Australian PAYG vs Sri Lankan APIT — interaction for cross-border employees.
  - Superannuation treatment when remitted to Sri Lanka.
  - Capital gains on Australian property held by Sri Lankan residents.
- **IRA cites:** §5, §7, §69, §80; SL-Australia DTAA articles (residence, employment, capital gains).

## 6. Crypto and capital gains — Sri Lanka

- **Slug:** `crypto-capital-gains-tax-sri-lanka`
- **Target keyword:** "crypto tax sri lanka capital gains"
- **Word count:** 1100–1500
- **Outline:**
  - Whether crypto is a "capital asset" under §7 and Chapter IV — current IRD position.
  - The CGT rate, the relevant tax year of assessment, and the practical valuation challenge for hot wallets.
  - Foreign-exchange (LKR-conversion) reporting basis — when the gain crystallises in LKR terms.
- **IRA cites:** §6(2)(c) / §7(2)(b) (gains from realisation), Chapter IV (calculation of gains), §80.

## 7. Bank statement upload — what makes a clean remittance record

- **Slug:** `sri-lanka-bank-statement-upload-tax-remittance`
- **Target keyword:** "sri lanka bank statement tax remittance"
- **Word count:** 900–1200
- **Outline:**
  - What the IRD looks for in an audit: traceability from inward remittance to source.
  - PDF vs CSV vs paper — the right format, and what to redact (and what not to).
  - The FIESTA "Drop in statement" flow walkthrough, with the screen the user lands on.
- **IRA cites:** §71 (source allocation); also IRD audit practice (cite where the IRD has published guidance).

## 8. Audit defence for foreign-income earners — what the IRD asks for

- **Slug:** `sri-lanka-tax-audit-defence-foreign-income`
- **Target keyword:** "sri lanka tax audit foreign income defence"
- **Word count:** 1300–1700
- **Outline:**
  - The typical IRD enquiry letter for foreign-income filers — what triggers it, what they ask.
  - The seven-document audit pack: TIN registration, return, schedules, bank statements, FX rates, foreign tax certificates, source declarations.
  - How FIESTA's record structure lines up with the IRD's evidence asks (a defensive-by-design pitch).
- **IRA cites:** §93 (filing), §99 (assessments), §128 (record-keeping); plus the audit framework references in the IRD's published manuals.

---

## Operator instructions for adding article 9, 10, 11... in the future

To add a new article in this system:

1. Copy one of the pilot files in `content/articles/` as a starter template.
2. Edit the frontmatter: `title`, `slug` (lowercase, hyphenated, must be URL-safe), `date`, `summary`, optional `hero_image`, `keywords` list, and optional `faq` list of `{q, a}` entries.
3. Write the body in Markdown (the in-tree converter handles headings, lists, bold/italic, links, blockquotes, code spans). Use `§<number>` for IRA citations to match the existing in-app convention. Verify each section number against an IRA source — never invent them.
4. Restart the app process (the article loader is process-lifetime cached). In dev: a SIGHUP-equivalent. In Fly.io: `fly deploy` or a rolling restart.
5. The article will:
   - Appear at `/articles/<slug>` with full Article + FAQPage + Breadcrumb JSON-LD.
   - Show up in the `/articles` index, sorted by `date` desc.
   - Be added to `/sitemap.xml` automatically.
   - Be picked up by Google / Bing on their next crawl (sitemap cached 1h).
6. No DB migration, no admin UI, no deploy gate. Just commit + push.

If you want the article OUT of the index temporarily (e.g. for staging review): rename the file extension from `.md` to `.md.draft`. The loader only picks up `*.md`.
