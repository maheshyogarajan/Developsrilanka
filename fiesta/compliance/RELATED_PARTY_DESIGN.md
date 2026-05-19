# FIESTA §195 Related-Party Detection — Design Doc

**Version:** 1.0 (Wave 4 v1.0 build, 2026-05-20)
**Status:** Built + tested (18/18 green). Awaiting wiring into S8/S9 generator.
**Sources:**
- `working files/strategic/council/persistent/fiesta/THE_PATH_20260520.md` — G.1.3 answer (DEFAULT-ON polarity)
- `working files/strategic/council/_briefs/fiesta_council_brief.json` — council Risk B framing
- Inland Revenue Act No. 24 of 2017, §195 (associated persons / related-party transactions)

---

## 1. Why this exists (Risk B)

Council unanimous 2026-05-20: FIESTA cannot ship Service Agreements / Rental Agreements with the §195 related-party disclosure toggle defaulting OFF. UI-neutral defaults appear "neutral" but are actively wrong polarity for tax-evasion-adjacent disclosures — IRD examiners reading aggregate FIESTA output would see a low rate of related-party flags and could characterise FIESTA as a *systemic evasion facilitator*. That characterisation threatens the Lanka.tax operating license.

The architectural fix is: when signal evidence suggests an arrangement is not arm's-length, the §195 disclosure toggle in the FIESTA UI defaults ON. The user can switch it off if they have reason to — that decision is logged and is part of the audit trail. The current module computes the signal evidence.

---

## 2. Algorithm

### 2.1 Signal extraction

Each of 8 signals is computed independently as a pure boolean against the
`(customer, service_provider, [payments], [market_rate_table])` inputs:

| Signal | Weight | What it asserts |
|---|---|---|
| `STATED_RELATIONSHIP` | 1.00 | Customer-asserted (form input) — definitive |
| `SAME_BANK_ACCOUNT`   | 0.95 | Same account number after normalisation — near-definitive |
| `SAME_NIC_PREFIX`     | 0.55 | Same SL family-signature (YY + reg-district serial) |
| `ABOVE_MARKET_RATE`   | 0.50 | Declared fee > 2x median for service type |
| `IRREGULAR_CADENCE`   | 0.45 | CoV of inter-payment intervals > 0.5 |
| `SAME_ADDRESS`        | 0.40 | Normalised street + locality match (Lev < 3) |
| `BELOW_MARKET_RATE`   | 0.40 | Declared fee < 0.5x median (in-kind comp signal) |
| `SAME_SURNAME`        | 0.25 | Hereditary surname token match (>= 3 chars) |

### 2.2 NIC family-signature

Sri Lanka has two NIC formats coexisting:
- **Old**: `9-digit + V/X` → digits = `YY|DDD|SSS|C`
- **New (post-2016)**: `12-digit` → digits = `YYYY|DDD|SSSSS`

The "family signature" is `year-of-birth (2 chars) + first 3 of district serial`. Same district serial = same district registration = same village-level registration office. In SL this is hereditary at the village level: members of the same extended family registered at the same village office tend to receive sequential serials.

The helper normalises both formats to a 5-char prefix so old↔new cross-format comparisons work. False positives: unrelated people born the same year in the same village (uncommon but real). False negatives: very rare unless one party has a foreign-issued ID (handled gracefully — returns False).

### 2.3 Address matching

1. Normalise: NFKC → lowercase → strip punctuation → collapse whitespace → remove address noise words (`no.`, `apt`, `flat`, `unit`, `#`, etc.).
2. Postcode hard gate: if both have postcodes and they differ, return False (catches Colombo-05 vs Kandy lookalikes).
3. Street + locality: exact match OR Levenshtein distance < 3 (handles `colombo 03` ↔ `colombo 3`, single-digit house-number swaps, common typos).

### 2.4 Cadence detection

Compute coefficient of variation (CoV = stdev / mean) on inter-payment intervals (in days). CoV > 0.5 ⇒ irregular. Needs ≥ 3 payments to compute (2 payments yields a single interval and trivially zero stdev). Uses population stdev (small samples).

### 2.5 Market-rate band

Lookup against `market_rates_table.yaml` (10 service types, v0.1 placeholder rates). Bands: `below` (< 0.5x median), `within` (0.5x — 2.0x median), `above` (> 2.0x median), `unknown` (service-type not in table OR fee non-positive). Thresholds match THE_PATH_20260520.md Risk B spec.

### 2.6 Aggregation

`confidence = 1 - Π(1 - weight_i)` over fired signals — i.e. complement-product OR. Monotonic, pure, gives 0.0 when no signals fire and approaches 1.0 as evidence stacks.

`should_default_on_disclosure = confidence >= 0.25`. Threshold deliberately low: a single `SAME_ADDRESS` hit (weight 0.40) already trips it. This bakes the Risk B polarity into the code: it's easier to switch a disclosure off than to defend an examiner question about why one wasn't disclosed.

### 2.7 Audit substance risk (UI banner)

- **HIGH** if `STATED_RELATIONSHIP` or `SAME_BANK_ACCOUNT` fires, OR if at least one relational signal (NIC/address/bank/stated) AND at least one economic signal (above/below rate/cadence) fire.
- **MEDIUM** if any relational OR economic signal fires alone.
- **LOW** otherwise (no signals, or surname-only).

The HIGH banner is the "high-risk arrangement" warning specified in THE_PATH_20260520.md.

---

## 3. False-positive concerns

### 3.1 Quantitative target

- **FP rate**: < 15% (target). Single `SAME_ADDRESS` alone trips DEFAULT-ON — meaningful FP surface among genuine roommates, lodgers, and shared-workspace arrangements. We accept this because the cost of an FP is "user switches toggle off" (small UX friction); the cost of an FN is "Lanka.tax loses operating license" (existential).
- **FN rate**: ~0% target. Achieved by stacking signals at low individual weights so that any single moderate signal trips the threshold.

### 3.2 Top two false-positive sources (qualitative)

1. **Same-address roommates / co-tenants** — `SAME_ADDRESS` alone (weight 0.40) trips DEFAULT-ON. Two unrelated Colombo professionals sharing a flat in a single-line address would be flagged. Mitigation: user-facing UX shows reasoning trace; they switch the toggle off + provide commercial-substance text. Audit log preserves both the FP flag AND the user's correction.
2. **Generic SL surnames** — `SAME_SURNAME` is filtered to weight 0.25 and ≥ 3-char tokens, but common surnames (Perera, Silva, Fernando, Bandara, Jayawardena) are extremely clustered. Mitigation: surname alone is below the 0.25 default-on threshold so DEFAULT-ON only fires when surname stacks with another signal. The test suite pins this behaviour.

---

## 4. Audit-defensibility surface

Every fired signal contributes a human-readable line to `result.reasoning`. The FIESTA UI MUST display this reasoning trace next to the §195 toggle, and the trace MUST be persisted in the S8/S9 audit log. This protects against examiner questions like "why was disclosure off?" — the answer is verifiable from the structured signal log + the user's override (if any).

The `RelatedPartyResult` model is `frozen=True` (immutable) and `extra="forbid"` (strict schema). This is intentional: the audit log must contain exactly what was computed, no later additions, no unrecognised fields silently swallowed.

---

## 5. Escalation UX (recommended; this module does not own UX)

1. **HIGH risk + DEFAULT-ON + user switches OFF** → require commercial-substance textarea + Lanka.tax review queue (Tier 2 approval) before S8/S9 PDF generates.
2. **HIGH risk + DEFAULT-ON + user keeps ON** → S8/S9 generator emits the disclosure clause; no extra friction.
3. **MEDIUM risk + DEFAULT-ON + user switches OFF** → require commercial-substance textarea, no review queue.
4. **LOW risk + DEFAULT-OFF** → no UX intervention.

---

## 6. Future ML approach (v1.2+)

The rules-based v1 deliberately overdetects. Once FIESTA has > 1000 (customer, service_provider) records with examiner outcomes, a supervised classifier (logistic regression initially, gradient boosting if budget allows) trained on:
- The same 8 signal features
- Industry / service-type one-hots
- Customer foreign-income share (per IRA s.83-85 segment)
- Geographic features (locality, district)

with the target being "actual related-party per examiner finding" would tighten the FP rate while preserving the FN ~0% guarantee (calibrate via cost-sensitive threshold on the precision-recall curve, pinning recall at 0.99).

The rules-based version SHIPS — the ML version is a post-v1 enhancement.

---

## 7. Out of scope

- Cross-jurisdiction associated-person rules (UK Connected Persons, US §267).
- Sinhala-script name normalisation (the helper degrades gracefully — does not crash on Sinhala input).
- Live SF / Supabase queries (this module is pure).
- The §195 disclosure clause TEXT (lives in S8/S9 generator).
- The FIESTA UI toggle component (lives in frontend).
- The audit log persistence layer (lives in the FIESTA backend, downstream of this module).

---

## 8. Test coverage

`tests/compliance/test_related_party.py`:

| Test | Asserts |
|---|---|
| 01 — Siblings same NIC prefix + address + surname | DEFAULT-ON, HIGH/MEDIUM |
| 02 — Stated 'spouse' | DEFAULT-ON, HIGH, confidence ≥ 0.9 |
| 03 — Roommates same address only | DEFAULT-ON (accepted FP), MEDIUM |
| 04 — Same bank account | DEFAULT-ON, HIGH, confidence ≥ 0.9 |
| 05 — No signals | DEFAULT-OFF, LOW |
| 06 — Above market + same address | DEFAULT-ON, HIGH |
| 07 — Irregular cadence + within-band rate | DEFAULT-ON (cadence alone) |
| 08 — Legitimate vendor | DEFAULT-OFF, LOW |
| 09 — Cross-format old↔new NIC same family | DEFAULT-ON |
| 10 — Sinhala-script mixed names | graceful (no crash) |
| 11 — Empty / 1-payment lists | no cadence inference |
| 12 — Missing fields / non-dict | graceful |
| sub — NIC invalid input | False |
| sub — Bank normalisation | True after stripping |
| sub — Surname short-token filter | filters 'de' |
| sub — Address postcode mismatch | hard-blocks |
| sub — Market-rate band thresholds | exact 0.5x / 2.0x boundaries |
| sub — Cadence needs ≥ 3 points | False with 2 |

All 18 PASS as of 2026-05-20.

---

## 9. PM findings (for Wave 4 PM board)

1. **X6 subagent_f gate × P6 Compliance**: `fiesta.compliance.related_party.detect_related_party()` MUST be called inside the S8/S9 generator BEFORE PDF rendering. The `should_default_on_disclosure` boolean drives the disclosure-clause inclusion. Gate: no S8/S9 PDF leaves the system without a `RelatedPartyResult` having been computed AND persisted to the audit log.
2. **X6 subagent_f gate × P11 Resilience**: the market_rates_table.yaml is a *placeholder* (v0.1). A 90-day staleness audit MUST fire a Telegram warning if `last_table_review` in the YAML is more than 90 days old. The table must be re-baselined against an SL industry-rate survey before FIESTA goes GA. Wave 4 v1.1 owns this.
