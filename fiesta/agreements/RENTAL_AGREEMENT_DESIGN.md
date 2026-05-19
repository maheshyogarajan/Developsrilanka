# S9 Rental Agreement Generator — Design

**Version:** v1.0 (DRAFT — pending Lanka.tax legal review returning 2026-05-27)
**Branch:** `wave3/s9-rental-agreement`
**Built:** 2026-05-20

## 1. Why this exists

FIESTA customers (Sri Lankan tax residents earning foreign income) typically claim a deduction under IRA s.6 for the rent of the workspace they use to produce that income. The customer's landlord is OFTEN a relative — high s.195 (associated-persons) audit risk. If the customer is audited, the rental agreement is one of the first documents IRD asks for. A boilerplate "we rent a flat" agreement won't survive scrutiny; FIESTA generates an IRD-defensible one.

## 2. Scope

In:
- Generate a PDF Rental Agreement from a structured form (tenant, landlord, property, term, rent).
- Auto-detect s.195 associated-person signals (NIC family-signature, surname, address, bank account, owner-rents-from-self heuristic).
- Default the s.195 disclosure ON when signals fire; allow override with required reason (audit trail).
- Compute home-office-portion of rent (when tenant uses only part of the premises for the trade or business).
- Compute and warn about Stamp Duty Act exposure when the term exceeds 364 days.
- Persist every render to `rental_agreement_generated` for audit.

Out (deferred):
- Auto-renewal handling (X3 year-end rollover, Wave 5).
- e-signature integration (deferred, Wave 6).
- Co-sign send-by-email (deferred, Wave 6 — same surface as service agreement).
- Cadence-mismatch feedback to S11 invoice cadence tracker (one-way ref now, two-way after S11 ships).
- Bilingual (English + Sinhala) rendering — English only at v1.

## 3. Architecture

```
fiesta/agreements/
  __init__.py                 # public surface
  templates/
    rental_agreement.j2       # ~280 line jinja2 -> markdown-lite source
  pdf_engine.py               # shared ReportLab pipeline + reference IDs
  rental_pdf.py               # S9-specific orchestration
  rental_routes.py            # Flask blueprint /agreements/rental
  models.py                   # SQLAlchemy table + pydantic DTOs
  stamp_duty.py               # Stamp Duty Act exposure calculator
  RENTAL_AGREEMENT_DESIGN.md  # this file
```

Render pipeline:

```
RentalAgreementInput (pydantic, validated)
  -> _resolve_related_party (calls fiesta.compliance.detect_related_party)
  -> stamp_duty_for_term
  -> mint_reference_id (deterministic)
  -> jinja2 template render -> markdown-lite string
  -> pdf_engine.render_blocks_to_pdf -> bytes
  -> SHA-256 -> RentalPDFOutput + persisted RentalAgreementGenerated row
```

### Why ReportLab not WeasyPrint
- WeasyPrint is the council-preferred (HTML/CSS fidelity), but it needs cairo/pango binary dependencies absent from the Replit and Fly base images we deploy to. ReportLab is already on the dependency list and produces a layout-correct PDF for a structured-document use case. The Jinja2 -> markdown-lite -> ReportLab pipeline keeps the template authorable by anyone who can write Markdown.

## 4. s.195 integration

The §195 disclosure block is rendered when ANY of:

1. `fiesta.compliance.related_party.detect_related_party()` returns `should_default_on_disclosure=True` (signals: STATED_RELATIONSHIP, SAME_BANK_ACCOUNT, SAME_NIC_PREFIX, SAME_ADDRESS, SAME_SURNAME, IRREGULAR_CADENCE, ABOVE_MARKET_RATE, BELOW_MARKET_RATE) — confidence threshold 0.25 (overdetection deliberate).
2. `input.s195_force_on` — staff or CEO override.
3. `input.customer_status_owner_rented_from_self` — customer rents from a property they themselves own (corporate vehicle or self-managed entity). Always defaults ON.

It can be turned OFF via `input.s195_force_off`, which REQUIRES a non-empty `input.s195_override_reason`. Pydantic validator catches missing reasons at the schema layer; the orchestrator catches it as defence in depth. The reason persists to `s195_override_reason` on the audit row.

When the disclosure block renders it pulls in the commercial-substance evidence prompts (market-rate citations, payment cadence, owner-occupation, third-party quotes). These are always shown in the disclosed template — the customer is on notice that they need to retain this evidence for seven years.

## 5. Stamp Duty Act

Per Stamp Duty Act No. 12 of 2006:
- Term ≤ 364 days → exempt under the Schedule. No duty.
- Term > 364 days → LKR 1 per LKR 1,000 of total consideration (rent over the full term + any premium). Minimum LKR 25.

`stamp_duty_for_term(term_days, total_rent_lkr, premium_lkr)` returns `(payable_amount, chargeable, reason, band)`. Used both by `rental_pdf.py` (to bake the warning + Schedule-SD note into the PDF) and the route layer (to surface a warning ribbon in the preview UI).

PM finding (P11 Resilience): the rate is statutory and has been amended multiple times; the constants in `stamp_duty.py` need Lanka.tax legal verification before GA. The MIN_CHARGEABLE_STAMP_LKR and STAMP_RATE_PER_KLKR constants are the audit surface.

## 6. Home-office portion

The tenant may use only part of the premises for the trade or business (typical SL pattern: spare room of a residence). The form captures `home_office_percentage` (0 < x ≤ 1). The template:
- Renders clause 2.3 ONLY when percentage < 1.0.
- Computes `home_office_portion_lkr = monthly_rent * percentage` and prints it in clause 2.3.
- Persists the percentage and the LKR amount to the audit row.

The customer can therefore present the agreement AND the deduction line on their return as a coherent narrative ("I rent the whole flat for Rs 60K/month, the spare room I use 100% as my office is 30% of the floor area, so I claim Rs 18K/month under IRA s.6").

## 7. Reference IDs

Format: `RA-{tax_year}-{user_initials}-{4HEX}`.

Deterministic per (user_id, tax_year, term_start, monthly_rent). Re-rendering the same agreement after a template-version bump produces the same reference — IDs are stable across template revisions but unique across logical agreements.

## 8. Persistence

`rental_agreement_generated` is append-only. Every render produces a new row. No update / no delete. PDFs sit on disk at `${FIESTA_AGREEMENT_PDF_DIR}/{reference_id}.pdf` (default `generated/agreements/`). The SHA-256 is stored on the row so we can verify the PDF on disk hasn't been tampered with.

## 9. Cadence consistency hook (S11)

When S11 (invoice cadence tracker, Wave 4) compares actual rent payment cadence against the agreement's monthly cadence, it joins `rental_agreement_generated.monthly_rent_lkr` and `term_start` / `term_end`. The shape exposed today is exactly what S11 needs; no further work required on the S9 side.

## 10. CEO design decisions (logged for the build dispatch)

1. **365+ day rental support.** Decision: support, with a stamp-duty warning + automatic Schedule-SD note. Rationale: some customers have multi-year leases and want a single instrument; pushing them to manually annual-renew is friction. The compliance gate (stamp_duty.py) keeps it audit-defensible.

2. **Owner-rented-from-self legitimacy threshold.** Decision: legitimate but always discloses §195. The CEO's apartment-rental SPV pattern is real and valid — the customer owns a property through a corporate vehicle and rents it back. The arrangement is legal; the §195 disclosure with commercial-substance evidence (market rate, payment cadence, no shell-company indicators) is what makes it audit-defensible. No threshold to "ban" it.

3. **Foreign-currency rent handling.** Decision: accept FX-denominated rent (USD/GBP/EUR/AUD/SGD/INR/JPY); auto-convert to LKR for the stamp-duty calc using CBSL selling rate (placeholder fallback rates in code). Template clause 4.4 explicitly cites CBSL date-of-credit conversion. Reasoning: foreign-currency rent is rare but happens (e.g. SL flat rented to a non-resident who pays in USD); refusing it would be over-restrictive.

## 11. PM findings

### S9 × P6 Compliance
The §195 disclosure is the audit-defence surface. The detector defaults to OVERDETECTION (FP <15%, FN ~0%) on purpose: Lanka.tax operating licence depends on never under-disclosing. The override path (s195_force_off + required reason) is the customer's safety valve. The disclosure block content (clause 10) names every signal class as evidence anchor and commits the customer to a 7-year retention obligation — this matches IRD audit timeline.

### S9 × P3 Architect
`pdf_engine.py` is the shared surface with S8. Both builds land independently. Convergence work post-merge: extract the markdown-lite parser into a thirty-line helper and let both PDFs author their own templates against the same renderer. Reference-ID minting is already shared.

### S9 × P11 Resilience (Stamp Duty Act conformance)
`STAMP_RATE_PER_KLKR` and `MIN_CHARGEABLE_STAMP_LKR` are statutory constants that have been amended in every Finance Act since 2018. The two-line audit surface is intentional — Lanka.tax legal verifies these BEFORE GA, with a 90-day staleness audit thereafter (matches the council's market_rates_table.yaml audit cadence per RELATED_PARTY_DESIGN.md). A WARN-on-stale check should ship in Wave 4 alongside the related-party calibration sweep.

## 12. Test coverage

`tests/agreements/test_rental_agreement.py` — 16 cases (see file for the matrix). Pure-function tests of the orchestrator + stamp-duty + §195 integration. Routes are tested separately when Flask app context is available; the worker thread of this build leaves route tests as integration coverage to be added when the parallel S8 build's app-wiring lands.

## 13. Deferred / open

- Welsh-style mid-clause renumbering when §195 fires currently uses a string-conditional in the template (`"11" if ctx.related_party.disclosure_applied else "10"`). A proper clause-numbering helper would be cleaner. Logged for Wave 4 refactor.
- The PDF metadata `creationDate` is sanitised but ReportLab's invariant mode would be more rigorous. Defer to Wave 4.
- Witnesses block always renders 2 lines (SL convention); some customers will want 0 or 1 witness. Add a `witness_count` field in v1.1.
- Foreign-currency FX table is a fallback; production should pull live CBSL selling rate. Defer to Wave 4.
