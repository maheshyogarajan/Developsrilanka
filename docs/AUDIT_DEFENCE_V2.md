# Audit-Defence PDF v2

**Status:** Opt-in via feature flag (`AUDIT_PDF_V2_ENABLED`, default OFF).
**Branch:** `tier-d6/b14-audit-v2`.
**Spec source:** Wave-6 inventory item #52 (B14 — Audit-Defence PDF v2).
**v1 generator (preserved unchanged):** `fiesta/tax_bill/audit_pack.py`.
**v2 generator:** `fiesta/tax_bill/audit_pack_v2.py`.

---

## What v2 adds over v1

| Section | v1 | v2 |
|---|---|---|
| A — Cover / filing summary | Yes | Yes (preserved with v2 doc reference) |
| B — **Per-claim evidence chain** | No | **NEW** — one row per claim with source records |
| C — **IRA sections cited (verbatim text)** | One-cell IRA hint | **NEW** — full quoted IRA text, alphabetised by section |
| D — **Calculation methodology** | Bracket table only | **NEW** — income roll-up → deductions → bracket walk → formula |
| E — Customer attestation | Yes | Yes (preserved, with §120(6)(a) record-keeping reference) |

Page footer with page number, branding, and document reference now appears on every page.

---

## How v2 works

The PDF v2 builder reads the same `TaxBillReport` v1 reads. It produces three
new sections by walking the verified `TaxInputs` snapshot:

1. **`claim_provenance.all_claim_rows(inputs)`** — emits one dict per non-zero
   tax claim (income category or deduction line). Each row carries:
   - `label`, `amount_lkr`
   - `ira_section_refs` — list of bare numeric section numbers (e.g. `["6", "13"]`)
   - `sources` — list of source records (IncomeEntry / RemittanceEntry /
     DeductionClaim) with id, date, currency, amount, FX rate, payer, filename
   - `calculation_trace` — short bulleted list of human-readable steps

2. **`claim_provenance.cited_section_numbers(rows)`** — collects the union of
   section numbers cited across all rows so Section C renders only the
   sections actually relevant to this filing.

3. **`claim_provenance.cites_by_section()`** — loads the IRA cite catalog
   from `static/data/ira_cites.json` and indexes it by section number.

The PDF then renders Section B as per-claim header + source table + trace
block, Section C as section heading + quoted text + per-section relevance
note, and Section D as a 3-step methodology summary (income → deductions →
bracket walk → formula).

---

## IRA cite catalog

**File:** `static/data/ira_cites.json` (versioned, schema_version 1).

**v1 catalog ships 13 verbatim sections:**
§2, §3, §5, §6, §7, §51, §52, §83 (and §83A inline), §85, §92, §93, §94, §120.

These cover every section reference the FIESTA deductions catalog currently
emits except two:
- **§13** (capital allowance / depreciation on plant & equipment) — placeholder
  entry marked `"todo": true`. Cited from `equipment_capex` and
  `solar_installation` deduction categories.
- **§195** (disclosure of payments to associated persons) — placeholder
  entry marked `"todo": true`. Cited when a service-provider or rental
  counterparty is flagged related-party.

Both TODO entries render in the PDF with a clear "Text pending" banner so the
auditor knows to consult the published Act directly. **They must be filled in
before v2 ships to GA.**

### How the cite text was sourced

Each section's `text` field is a verbatim excerpt pulled from the IRA KG
(`mcp__ira__get_section`) on 2026-05-24. Right-margin amendment metadata
(`S30 of 10/2021`, `w.e.f. 01.04.2018`, etc.) was trimmed for PDF readability.
The full unedited text is always available through the IRA KG at the same
section number.

### Adding a new IRA section

1. Pull the verbatim text via the IRA KG MCP tool
   (`mcp__ira__get_section({"section_num": "12"})`) or from a verified PDF
   of the consolidated Act.
2. Append a new entry to `static/data/ira_cites.json` under `sections`:
   ```json
   {
     "section": "12",
     "title": "<section short title>",
     "heading_path": "PART X > CHAPTER Y > Section 12",
     "pages": "NN-NN",
     "text": "<verbatim text, trim right-margin amendment marginalia>",
     "relevance_to_foreign_income_earner": "<one or two sentences>",
     "todo": false
   }
   ```
3. The cite catalog is loaded once per process and cached. A Flask restart
   is required for changes to take effect in long-running workers.
4. Tests in `tests/tax_bill/test_b14_audit_pdf_v2.py::test_b14_09_ira_cites_loader_schema`
   enforce a floor of 10 sections + non-TODO status for §6 + §120 + §52.

---

## Per-claim provenance model

The provenance helper produces three kinds of rows today:

- `income` — one per non-zero income category in `inputs.income_by_category_lkr`.
  Sources are pulled from `IncomeEntry` (and `RemittanceEntry` for the
  `foreign_remittance` category) when a DB session is available; otherwise
  the row carries a single synthetic "aggregated" source with a provenance
  note pointing to the upstream screen.

- `deduction` — one per claimed `DeductionClaim` (S5). The source row carries
  the catalog's `category_id`, the evidence status, estimated vs actual
  amounts, the cap note (if any), and any free-text notes.

- `exemption_relief` — reserved. Phase-1 personal reliefs flow through the
  deduction list (Fifth Schedule entries are part of the catalog). The
  empty hook is the extension point for future per-relief itemisation.

### Adding a new claim type

1. Add a builder function to `fiesta/tax_bill/claim_provenance.py`
   following the `build_income_claim_rows` / `build_deduction_claim_rows`
   pattern. Each row must carry `claim_kind`, `claim_id`, `label`,
   `amount_lkr`, `ira_section_refs`, `sources`, `calculation_trace`.
2. Include the new builder in `all_claim_rows()`.
3. Add a sample case in `tests/tax_bill/test_b14_audit_pdf_v2.py` so the
   rendered PDF is exercised against a realistic fixture.

---

## Feature flag — `AUDIT_PDF_V2_ENABLED`

**Default:** OFF. v2 is opt-in until 14-day stability gate clears.

### Enabling for a session / process

Two equivalent ways (env var wins if both are set):

```bash
# 1. Environment variable (preferred for prod fly machines)
export AUDIT_PDF_V2_ENABLED=true

# 2. feature_flags.DEFAULT_FLAGS (preferred for local dev)
#    Edit feature_flags.py and add:
#      "AUDIT_PDF_V2_ENABLED": True,
```

After setting either: restart the Flask app. The route resolves the flag at
request time via `feature_flags.is_feature_enabled("AUDIT_PDF_V2_ENABLED")`
with an env-var fallback (`feature_flags._v2_flag_enabled` helper in
`fiesta/tax_bill/routes.py`).

### Disabling rollback

`unset AUDIT_PDF_V2_ENABLED` (or set it to `false` / `0`) and restart. With
the flag off, `?v=2` query parameter silently falls back to v1 — no broken
links for customers who bookmarked the v2 URL.

### CEO operator instructions

When you (the CEO) decide v2 is GA-ready:

1. Confirm §13 + §195 catalog TODOs are resolved (`grep "todo" static/data/ira_cites.json`).
2. Set `AUDIT_PDF_V2_ENABLED=true` on fly:
   ```bash
   fly secrets set AUDIT_PDF_V2_ENABLED=true -a fiesta-mvp
   ```
3. Update the customer-facing S12 export button to default to v2 (drop the
   `?v=2` query param requirement — flip the route's default-v decision
   from "v1 unless v=2" to "v2 unless v=1").
4. Once v2 is the production default for ≥14 days with zero customer-reported
   issues, remove v1 (`fiesta/tax_bill/audit_pack.py`) and the route
   fallback in `routes.py::export_audit_pack`.

### Route behaviour matrix

| Flag value | `?v` query | Generator used | PDF filename suffix |
|---|---|---|---|
| OFF (default) | omitted | v1 | _(none)_ |
| OFF | `?v=2` | v1 (silent fallback) | _(none)_ |
| ON | omitted | v1 | _(none)_ |
| ON | `?v=2` | **v2** | `_v2` |
| ON | `?v=1` | v1 | _(none)_ |

Sample admin URLs (spot-check after enabling):

```
GET /tax-bill/2025-26/export          → v1 (always works)
GET /tax-bill/2025-26/export?v=2      → v2 when flag is ON, else silent v1
GET /tax-bill/2024-25/export?v=2      → v2 for prior year (same logic)
```

---

## Page cap

Spec target: under 30 pages for a typical filing.

The truncation safety valve in `audit_pack_v2.MAX_EVIDENCE_ROWS_PER_SECTION`
(default 80) caps Section B at 80 claim rows. With the typical 5-row
deduction-claim catalog + 3-6 income categories + 1-3 SPs, a typical filing
emits 9-14 rows in Section B and renders well under the cap. When truncation
fires, the PDF includes a clear "Truncated — full evidence available on
request" notice + the document reference ID.

Regression test: `test_b14_07_v2_typical_filing_under_page_cap` enforces the
30-page target for a 6-claim mixed-income fixture.

---

## Tests

`tests/tax_bill/test_b14_audit_pdf_v2.py` (10 tests):

| # | Test | What it proves |
|---|---|---|
| 01 | v1 unchanged regression | v1 PDF still renders + starts with `%PDF-` |
| 02 | v2 generates valid PDF | v2 builder produces well-formed PDF bytes |
| 03 | route v2 flag resolution | `_v2_flag_enabled` honours env var + feature_flags |
| 04 | v2 per-claim evidence | Section B header + at least one deduction label rendered |
| 05 | v2 IRA citations | Section C contains the §6 cite text |
| 06 | v2 calculation trace | Section D contains "methodology" + "roll-up" + "Deductions" |
| 07 | v2 under page cap | Typical filing renders ≤30 pages |
| 08 | v1 + v2 valid PDF bytes | Both builders return bytes starting with `%PDF-` |
| 09 | IRA cite loader schema | Catalog has ≥10 sections, §6 / §52 / §120 are non-TODO |
| 10 | Provenance rows for mixed income | `all_claim_rows` returns 6 rows for 3 income + 3 deductions |

Run:

```bash
cd C:/Users/mahes/fiesta_phase_a/Developsrilanka_d6_b14
python -m pytest tests/tax_bill/test_b14_audit_pdf_v2.py -v
```

PDF text-extraction in the tests uses `pypdf` (already a project dependency).
A best-effort fallback chain falls back to `PyPDF2`, then a raw byte-string
search, so the tests stay portable.

---

## Known limitations + TODOs

- **§13 and §195 cite text is a placeholder.** Both render in v2 with a
  "Text pending" red banner. Pull verbatim text from the IRA KG and flip the
  `"todo": true` flag before GA.
- **Per-claim source rows for income** are only as rich as the data the
  aggregator already loaded. RemittanceEntry rows carry payer + country +
  filename + FX source; legacy IncomeEntry rows carry less. v2 surfaces
  whatever is present and shows "-" for missing fields rather than failing.
- **Page count uses `/Type /Page` object counting**, which works for
  ReportLab-generated PDFs (negative-lookahead skips the `/Type /Pages`
  root) but is not a general-purpose PDF page-count solution.
- **Section B rendering is a flat-list of per-claim KeepTogether blocks**.
  At extreme truncation (>80 rows) the truncation notice is shown but the
  page cap is enforced by the row cap, not by per-page logic. If the catalog
  ever grows past ~20 deductions + 50 income categories simultaneously,
  consider a real PDF table-of-contents.
- **Customer-facing UX (S12 export button)** is unchanged. Once the CEO
  flips the global flag, the `?v=2` query parameter is the only way to opt
  into v2 from the browser. The S12 template needs a small change to add a
  "Try the v2 audit pack" link if you want customer-visible opt-in.
- **Translation:** v2 ships English-only. The IRA cite text is exclusively
  in English; the rest of the PDF copy is hard-coded English. Sinhala / Tamil
  localisation is a separate cut.

---

## Implementation notes

- **Lazy imports** in `routes.py::export_audit_pack` keep ReportLab and v2
  module off the hot path until export is actually requested. A failed import
  returns a 503 with the import-error string (preserved from v1 behaviour).
- **Defensive provenance loaders.** `_income_sources_for_category` swallows
  any DB exception and returns an empty list. The PDF still renders — it just
  shows the aggregated rollup as a single synthetic source.
- **Cache.** The IRA cite catalog is cached in `claim_provenance._IRA_CACHE`
  after the first call. The cache is invalidated only on process restart.
- **No DB writes.** v2 is read-only. The provenance helper performs SELECTs
  if a SQLAlchemy session is available; it never updates any row.
