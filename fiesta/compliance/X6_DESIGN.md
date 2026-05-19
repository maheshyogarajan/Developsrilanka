# X6 Compliance Gates -- Design Document

**Status:** v1.0 -- shipped on `wave3/x6-compliance-gates`, 2026-05-20.
**Council ref:** `working files/strategic/council/_briefs/fiesta_council_brief.json`
feature code X6 ("subagent_f compliance gates (cross-screen)"). Risk B mitigation
from `THE_PATH_20260520.md` ("FIESTA must not be characterisable by IRD as a
systemic evasion facilitator").

---

## 1. Why this gate exists

Lanka.tax's operating license depends on FIESTA being **audit-defensible by
construction**, not by post-hoc review. Three risks have to be mechanically
prevented at the customer-journey surface (not in a later batch sweep):

| Risk | What goes wrong without the gate | Gate mitigation |
|---|---|---|
| **Audit substance** -- IRD looks at a sample of FIESTA-filed returns and finds patterns of indefensible deduction structuring | Customer signs off on a return where section-6 "wholly, exclusively, necessarily" doesn't hold | S5/S6/S7/S12 rules cite section 6, refuse to silently pass implausible numbers |
| **Related-party non-disclosure** -- customer pays a relative as "Service Provider" without section-195 disclosure on the agreement | The agreement looks like a normal arm's-length contract; IRD finds the relationship in a separate data run and re-characterises the deduction | S5 precheck + S8 default-on disclosure + S14 final-gate cannot-bypass block |
| **Foreign-source misclassification** -- customer with US-source income files as if all SL-source (or vice versa) | Wrong tax regime applied (remittance basis section 71 vs ordinary section 5) | S3 + S4 flag profile-vs-statement mismatches |

The gate is **upstream** of subagent_f (which gates CEO-OS outbound comms and
SF writes). subagent_f handles "is it safe to push this change to lanka.tax
ops?"; this gate handles "is it safe to let this customer continue down the
funnel?". Different surfaces, different concerns.

---

## 2. Per-screen rule catalogue

| Screen | Rule ID | Decision | IRA section | What it checks |
|---|---|---|---|---|
| **S2** signup | `S2-EMAIL-FORMAT` | block | section 120 | Email is a valid format (regex). |
| **S2** signup | `S2-PASSWORD-WEAK` | warn | (security baseline) | Password >= 8 chars. |
| **S3** profile | `S3-NIC-FORMAT` | warn | section 120 | NIC matches 9-digit-V/X or 12-digit format. |
| **S3** profile | `S3-ADDRESS-INCOMPLETE` | warn | section 120 | line1 + city populated. |
| **S3** profile | `S3-FOREIGN-INCOME-SOURCE-MISSING` | warn | section 71 | Foreign-income flag = true -> source must be named. |
| **S4** connect-earnings | `S4-EARNINGS-MISMATCH-FOREIGN` | warn | sections 71+73 | Foreign-source statement but no foreign-source declaration. |
| **S4** connect-earnings | `S4-EARNINGS-MISMATCH-LOCAL` | warn | sections 5+6 | Local-source statement but only foreign-source declared. |
| **S5** reduce-tax | `S5-RELATED-PARTY-PRECHECK` | warn | section 195 | Planned SPs share NIC/surname/address with customer -- early heads-up. |
| **S6** service-providers | `S6-SP-QUAL-UNKNOWN` | warn | section 6 | SP qualification tier not in reference table. |
| **S6** service-providers | `S6-SP-FEE-ABOVE-MARKET` | warn | section 6 | Fee > 1.5x ceiling for declared tier. |
| **S7** property-owner | `S7-RENTAL-ABOVE-INDEX` | warn | sections 6+60 | Rent > 1.5x CBSL housing-rent index per sqft. |
| **S8** service-agreement | `S8-SECTION-195-AUTO-ENABLED` | warn | section 195 | Related-party signals fired -> we enabled disclosure on customer's behalf. |
| **S8** service-agreement | `S8-SECTION-195-OVERRIDE-DENIED` | **block** | section 195 | Customer tried to turn off disclosure when signals present. |
| **S9** rental-agreement | `S9-RENTAL-AGREEMENT-ABOVE-INDEX` | warn | sections 6+60 | Agreement-side mirror of S7 rule. |
| **S12** your-tax-bill | `S12-DEDUCTION-RATIO-HIGH` | warn | section 6 | Deductions > 40% of gross income. |
| **S12** your-tax-bill | `S12-DEDUCTION-RATIO-EXCESSIVE` | **block** | section 6 | Deductions > 60% of gross income. |
| **S14** submit | `S14-UNRESOLVED-WARNINGS` | warn | (workflow) | Prior-screen warnings still un-acknowledged. |
| **S14** submit | `S14-SECTION-195-MISSING` | **block** | section 195 | Any related-party-flagged agreement is missing the disclosure block. |
| **S14** submit | `S14-DEDUCTION-RATIO-FINAL` | **block** | section 6 | Submit-time recheck of the 60% ratio; CEO override flag bypasses. |

**Count summary:**
- 10 screens covered
- 19 distinct rule IDs
- 14 yellow warnings, 5 red blocks
- Every rule cites an IRA section (or is explicitly tagged "security baseline" / "workflow")

---

## 3. Threshold tuning + justification

### 3a. Deduction-ratio thresholds (S12 + S14)

| Threshold | Value | Justification |
|---|---|---|
| Warn | 40% | Empirical: Lanka.tax's existing client base shows ~12% of self-employed returns sit at 30-40% effective deduction ratio without IRD issue. Above 40% historically correlates with a 3x higher IRD-query rate (sample n=~280 returns 24/25). |
| Block | 60% | Above 60%, IRD nearly always queries. The risk of an indefensible return becomes a license risk for Lanka.tax (Risk B). |

**Tuning surface:** the constants live at the top of `gate.py`
(`DEDUCTION_RATIO_WARN_THRESHOLD`, `DEDUCTION_RATIO_BLOCK_THRESHOLD`) and can
be hot-tuned without redeploying customer-facing code. Future v1.1 makes them
per-segment (foreign-income vs domestic vs property-rental).

### 3b. SP fee ceilings (S6)

Hard-coded tier ceilings (LKR/month):

| Tier | Ceiling | 1.5x warn line |
|---|---|---|
| junior | 50,000 | 75,000 |
| mid | 200,000 | 300,000 |
| senior | 500,000 | 750,000 |
| specialist | 1,500,000 | 2,250,000 |

These are starting anchors -- they were extracted from Lanka.tax's 23/24 +
24/25 invoice corpus. v1.1 should plug in CA-Sri-Lanka published rate cards
when those become structured-data accessible.

### 3c. Rent index (S7 + S9)

Fallback: `Rs 120 / sqft / month * 1.5 = Rs 180 / sqft / month` as the warn
line. This is a Colombo proxy for the CBSL Housing Rent Index. The real CBSL
feed plugs in via `fiesta/tax/data/cbsl_rent_index.yaml` (not yet shipped --
the constant `RENT_INDEX_FALLBACK_LKR_PER_SQFT` is a single point of update
when that data lands).

---

## 4. False-positive vs false-negative trade-off

**We err toward false-positive.** The cost asymmetry is severe:

| Outcome | Cost |
|---|---|
| **False positive** (we flag a legitimate claim) | Customer is annoyed for ~30 seconds and either (a) acknowledges the warning + proceeds, or (b) books a consultant. Both outcomes preserve Lanka.tax's license. |
| **False negative** (we silently pass an indefensible claim) | Return gets filed, IRD reviews, finds pattern, Lanka.tax loses operating license. **Existential risk.** |

The asymmetry is on the order of 10^4. Council R1-R3 (unanimous, 2026-05-19)
confirmed: 30% false-positive yellow-warning rate is acceptable; 0%
false-negative is the design target on the red-block path.

**Implication for thresholds:** the 40%/60% deduction-ratio numbers above
should drift DOWN over time as we learn which mid-band cases are
legitimate, not up. The default direction of tuning is more sensitive, not
less.

---

## 5. Customer-facing copy guidelines

Tone per `feedback_helping_not_collecting_for` ("we are helping you collect
YOUR information") and `feedback_phase_gate_before_client_action`:

| Copy type | Voice | Example |
|---|---|---|
| **Green** | Confident, brief | "Looks audit-defensible." |
| **Yellow** | Peer-to-engineer, observational | "Heads up -- here's what we noticed: your deductions are 45% of gross. That's above the level where IRD reviews become more likely." |
| **Red** | Decisive, helpful, with a way forward | "A few things need a closer look. We recommend a 30-min consultant review (Rs 5,000) before proceeding." |

**Banned patterns** (lifted from `feedback_template_immediate_no_business_day_language` and `feedback_helping_not_collecting_for`):
- "You violated rule X"
- "You can't do that"
- "You will be reviewed by IRD" (we can't predict that)
- "Talk to your accountant"  (we are the accountant)
- Any sentence that starts with "You must..."

Preferred patterns:
- "We noticed..."
- "Here's what we'd suggest..."
- "This is allowed but..."
- "We've enabled X on your behalf" (when defaults-on fire)

---

## 6. Override semantics

| Severity | Customer override? | Routes to |
|---|---|---|
| Yellow | Yes, with logged acknowledgement | `customer_self_serve` -- proceed |
| Red | No -- system blocks regardless of customer wishes | `consultant_booking` -- S17 (Wave 5 v1.1) or interim Lanka.tax booking page |

Override audit lives in the `compliance_overrides` SQLite/postgres table.
Every yellow override is correlatable per-customer + per-rule for v1.1 ML
tuning ("which rule has the highest customer-acknowledged-then-still-OK rate?
That rule's threshold is too tight").

CEO can bypass any red block via the `ceo_override_*` flags on the customer
record (v1.0 has `ceo_override_deduction_ratio`; more flags added as new
red rules ship). CEO overrides are logged separately for compliance review.

---

## 7. v1.1 evolution path

1. **Auto-route red blocks to consultant booking.** When `request_override`
   returns `routed_to="consultant_booking"`, the UI should deep-link to
   S17 (`/booking/consultant?prefill_rule=<rule_id>`). For v1.0, S17 isn't
   shipped -- we render a generic "book a consultant" CTA whose href is
   Lanka.tax's existing booking page.

2. **ML-tuned thresholds.** Once 1000+ override events exist, fit a logistic
   regression on (customer_segment, rule_id, override_outcome) -> "did this
   override correlate with an IRD query later?". Use the coefficients to
   per-segment-tune the threshold constants in `gate.py`.

3. **CBSL rent index feed.** Replace `RENT_INDEX_FALLBACK_LKR_PER_SQFT` with
   a per-district lookup table sourced from CBSL monthly publications.

4. **CA-SL rate cards for S6.** Plug published professional rates per
   qualification + experience-year into `SP_QUALIFICATION_TIERS`.

5. **Section 195 detector swap.** When wave4's `related_party.py` merges into
   main, remove the heuristic-only fallback path in `_rule_S5_related_party_check`
   and `_rule_S8_section_195_disclosure`.

6. **Per-action rule firing.** Currently rules fire on every gate_check
   regardless of `action`. v1.1 makes rules action-aware
   (e.g. "S12-DEDUCTION-RATIO-EXCESSIVE only fires on action=submit_for_filing").

7. **Cross-customer pattern detection (X6 v2).** A pattern that fires
   *the same way* on >5 unrelated customers in a week is itself a signal --
   either of a new exploit pattern OR a rule that's too sensitive. Surface
   via admin dashboard.

---

## 8. Files

| Path | Role |
|---|---|
| `fiesta/compliance/gate.py` | Public API: `gate_check()`, `GateResult`. Pure rule functions. |
| `fiesta/compliance/events.py` | Persistence: `log_gate_check()`, `query_recent_events()`. SQLite-first, JSONL fallback. |
| `fiesta/compliance/override.py` | Customer override + consultant-booking handoff. |
| `fiesta/compliance/__init__.py` | Re-exports. Lazy-loads related_party from sister branch. |
| `fiesta/templates/components/gate_banner.html` | Banner renderer (green/yellow/red). |
| `fiesta/templates/components/gate_warning.html` | Yellow-warning panel with per-rule acknowledge button. |
| `tests/compliance/test_gates.py` | 24 unit + 3 integration tests (27 total). |
| `fiesta/compliance/X6_DESIGN.md` | This file. |

---

## 9. Open decisions for CEO

1. **Deduction-ratio thresholds.** Defaults 40%/60% are evidence-anchored but
   conservative. Loosen to 45%/65% for foreign-income segment? Tighten to
   35%/55% for property-owner-claiming segment? Default: keep as-is for v1.0,
   revisit at 30-day data review.

2. **Red-block escalation path.** v1.0 routes to a generic "book a
   consultant" page. Lanka.tax already has a booking flow that takes
   payment up-front. Should the X6 path skip payment (i.e., free pre-block
   triage call) for the first 90 days to gather false-positive evidence,
   or is paid-from-day-1 the right test? Default: paid -- it filters
   serious customers and matches existing Lanka.tax economics.

3. **Section 195 disclosure auto-enable wording.** Currently we render this
   as a yellow warning ("We've enabled section-195 disclosure on your
   agreement"). Alternative: silently enable, no banner. The yellow warning
   is more transparent but introduces friction. Default: keep transparent;
   review at first cohort.
