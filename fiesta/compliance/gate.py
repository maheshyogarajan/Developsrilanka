"""fiesta.compliance.gate -- Per-screen compliance gate for FIESTA customer journey.

X6 cross-cutting feature per `working files/strategic/council/_briefs/fiesta_council_brief.json`
(X6: "subagent_f compliance gates (cross-screen)") + THE_PATH_20260520.md
Risk B mitigation (audit substance -- FIESTA must not be characterisable by IRD
as a systemic evasion facilitator).

Design constraints (binding)
----------------------------
- Pure functions. No DB writes here (see fiesta.compliance.events for persistence).
- pydantic v2 result type. Type-strict, mypy clean.
- Each rule cites an IRA section number (e.g. "section 6 wholly, exclusively, necessarily").
- Customer-facing copy: empowerment, not paternalistic. "Here's what we noticed"
  not "you violated rule X". Tone per `feedback_phase_gate_before_client_action`
  and `feedback_helping_not_collecting_for`.
- False-positive bias: we err toward warning (yellow) over silent-pass to protect
  Lanka.tax's IRD-facing license posture (THE_PATH_20260520 Risk B).
- Critical issues (red blocks) are NON-NEGOTIABLE without escalation to a human
  consultant (S17 Wave 5 booking; for v1 they halt and surface CEO override).

Wiring
------
Call `gate_check(screen_id, customer_data, action)` from the screen route handler
BEFORE rendering the primary CTA. The returned GateResult drives:
  - Banner rendering (gate_banner.html / gate_warning.html)
  - Event log persistence (fiesta.compliance.events.log_gate_check)
  - CTA enable/disable state (passed=True -> enable, blocks=non-empty -> disable)

Integration with sister modules
-------------------------------
- IRA section 195 related-party detector (sibling branch wave4/related-party-default-on)
  is imported LAZILY inside `_rule_S5_related_party_check` and
  `_rule_S8_section_195_disclosure`. If unavailable, those rules degrade to a
  conservative best-effort signal (peer-name string match) rather than fail
  closed.
- `subagent_f.compliance.compliance_check` is the existing pre-execution gate
  for outbound comms / SF writes. The gate here is the UPSTREAM customer-facing
  surface; subagent_f sits later in the pipeline. We do NOT call it from here
  (different concerns: customer UX vs CEO-OS outbound veto).
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Constants -- copy/threshold tuning live here so design doc can cite line nrs.
# ---------------------------------------------------------------------------
DEDUCTION_RATIO_WARN_THRESHOLD = 0.40  # >40% of gross income flagged for review
DEDUCTION_RATIO_BLOCK_THRESHOLD = 0.60  # >60% blocked without CEO override
SP_RATE_MARKET_CEILING_MULTIPLIER = 1.5  # SP fee >150% of market = warning
SP_QUALIFICATION_TIERS = {  # IRA section 6 "necessarily" -- fee defensibility scaffold
    "junior": 50_000,  # LKR/month ceiling for junior tier
    "mid": 200_000,
    "senior": 500_000,
    "specialist": 1_500_000,
}
RENT_INDEX_FALLBACK_LKR_PER_SQFT = 120  # Colombo proxy; CBSL feed plugs in later
NIC_OLD_FORMAT_RE = re.compile(r"^\d{9}[VvXx]$")  # pre-2016 NIC
NIC_NEW_FORMAT_RE = re.compile(r"^\d{12}$")  # 2016+ NIC


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------
class GateResult(BaseModel):
    """Outcome of a single gate_check invocation.

    Fields
    ------
    passed:           True only when no warnings AND no blocks fired. Yellow
                      warnings DO permit `passed=False` while still allowing
                      proceed (UI shows banner, user can click through).
    warnings:         List of yellow flags. Each item: {"rule_id", "severity",
                      "message", "ira_section", "recommendation"}.
    blocks:           List of red blocks. Same shape as warnings but
                      severity="red". Cannot be overridden by customer.
    recommendations:  Free-form next-step suggestions (e.g. "consider booking
                      a consultant"). Empty when nothing to suggest.
    reasoning_trace:  Per-rule outcome log for audit. Ordered by rule execution.
                      Each entry: {"rule_id", "fired": bool, "reason": str}.
    """

    passed: bool = True
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    blocks: list[dict[str, Any]] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    reasoning_trace: list[dict[str, Any]] = Field(default_factory=list)

    def add_warning(
        self,
        rule_id: str,
        message: str,
        ira_section: str,
        recommendation: str,
    ) -> None:
        self.warnings.append(
            {
                "rule_id": rule_id,
                "severity": "yellow",
                "message": message,
                "ira_section": ira_section,
                "recommendation": recommendation,
            }
        )
        self.passed = False

    def add_block(
        self,
        rule_id: str,
        message: str,
        ira_section: str,
        recommendation: str,
    ) -> None:
        self.blocks.append(
            {
                "rule_id": rule_id,
                "severity": "red",
                "message": message,
                "ira_section": ira_section,
                "recommendation": recommendation,
            }
        )
        self.passed = False

    def trace(self, rule_id: str, fired: bool, reason: str) -> None:
        self.reasoning_trace.append(
            {"rule_id": rule_id, "fired": fired, "reason": reason}
        )


# ---------------------------------------------------------------------------
# Per-screen rules. Each function MUTATES the GateResult in place and returns
# nothing. Rules are pure (no DB, no env) -- all state arrives in customer_data.
# ---------------------------------------------------------------------------
def _rule_S2_email_format(result: GateResult, customer_data: dict[str, Any]) -> None:
    """S2 signup -- email format. IRA section 120 record-keeping (lite -- full KYC is S3)."""
    email = customer_data.get("email", "")
    pattern = r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
    if not email or not re.match(pattern, email):
        result.add_block(
            rule_id="S2-EMAIL-FORMAT",
            message="That email address doesn't look right -- please double-check.",
            ira_section="section 120 (record-keeping requires reachable contact)",
            recommendation="Use the email you'll check for tax correspondence.",
        )
        result.trace("S2-EMAIL-FORMAT", True, f"email='{email}' failed regex")
    else:
        result.trace("S2-EMAIL-FORMAT", False, "email format ok")


def _rule_S2_password_strength(
    result: GateResult, customer_data: dict[str, Any]
) -> None:
    """S2 signup -- password strength. Not IRA-driven; security baseline."""
    pw = customer_data.get("password", "") or ""
    if len(pw) < 8:
        result.add_warning(
            rule_id="S2-PASSWORD-WEAK",
            message="Your password is shorter than 8 characters.",
            ira_section="(security baseline -- not IRA-driven)",
            recommendation="Consider 12+ characters mixing letters, numbers, symbols.",
        )
        result.trace("S2-PASSWORD-WEAK", True, f"len={len(pw)}")
    else:
        result.trace("S2-PASSWORD-WEAK", False, "password length ok")


def _rule_S3_nic_format(result: GateResult, customer_data: dict[str, Any]) -> None:
    """S3 profile -- NIC format. IRA section 120 taxpayer identification."""
    nic = (customer_data.get("nic") or "").strip()
    if not nic:
        # NIC is optional at S3 (fills as user goes). Skip silently.
        result.trace("S3-NIC-FORMAT", False, "nic not supplied (optional)")
        return
    if NIC_OLD_FORMAT_RE.match(nic) or NIC_NEW_FORMAT_RE.match(nic):
        result.trace("S3-NIC-FORMAT", False, f"nic '{nic}' format ok")
        return
    result.add_warning(
        rule_id="S3-NIC-FORMAT",
        message=f"NIC '{nic}' doesn't match Sri Lankan formats (9 digits + V/X, or 12 digits).",
        ira_section="section 120 (taxpayer identification)",
        recommendation="Double-check your NIC on the card itself.",
    )
    result.trace("S3-NIC-FORMAT", True, f"nic '{nic}' did not match format")


def _rule_S3_address_completeness(
    result: GateResult, customer_data: dict[str, Any]
) -> None:
    """S3 profile -- address completeness. IRA section 120; needed for IRD correspondence."""
    addr = customer_data.get("address") or {}
    if not isinstance(addr, dict):
        addr = {}
    line1 = (addr.get("line1") or "").strip()
    city = (addr.get("city") or "").strip()
    if not line1 or not city:
        # Soft -- S3 is "fill as you need"
        result.add_warning(
            rule_id="S3-ADDRESS-INCOMPLETE",
            message="Address is incomplete (line 1 + city minimum needed for IRD letters).",
            ira_section="section 120 (correspondence address)",
            recommendation="Fill the postal address you'd give the IRD.",
        )
        result.trace("S3-ADDRESS-INCOMPLETE", True, "line1 or city missing")
    else:
        result.trace("S3-ADDRESS-INCOMPLETE", False, "address ok")


def _rule_S3_foreign_income_flag_consistency(
    result: GateResult, customer_data: dict[str, Any]
) -> None:
    """S3 -- declared foreign-income flag must match source declaration.

    If user ticks "I earn foreign income" but the source field is empty/blank,
    later screens (S4 connect-earnings + S5 deductions) will misroute.
    """
    foreign_flag = customer_data.get("earns_foreign_income")
    foreign_source = (customer_data.get("foreign_income_source") or "").strip()
    if foreign_flag is True and not foreign_source:
        result.add_warning(
            rule_id="S3-FOREIGN-INCOME-SOURCE-MISSING",
            message=(
                "You said you earn foreign income, but didn't say where from. "
                "We need a country/platform to apply remittance-basis rules."
            ),
            ira_section="section 71 (remittance basis for non-resident-source income)",
            recommendation="Add the country or platform (e.g. 'United States -- Upwork').",
        )
        result.trace(
            "S3-FOREIGN-INCOME-SOURCE-MISSING",
            True,
            "foreign_flag=True but source empty",
        )
    else:
        result.trace(
            "S3-FOREIGN-INCOME-SOURCE-MISSING",
            False,
            f"flag={foreign_flag}, source='{foreign_source}'",
        )


def _rule_S4_earnings_match_declared_sources(
    result: GateResult, customer_data: dict[str, Any]
) -> None:
    """S4 -- earnings statements must match declared source categories.

    If customer declared only foreign income at S3 but is uploading SL-bank
    statements with local-source patterns, flag for review.
    """
    declared_sources: list[str] = (
        customer_data.get("declared_income_sources") or []
    )
    statements: list[dict[str, Any]] = customer_data.get("statements") or []
    if not statements:
        result.trace("S4-EARNINGS-MATCH", False, "no statements yet")
        return

    declared_set = {str(s).lower() for s in declared_sources}
    has_declared_foreign = any(
        "foreign" in s or "overseas" in s or "abroad" in s for s in declared_set
    )
    has_declared_local = any(
        "local" in s or "sri" in s or "domestic" in s for s in declared_set
    )

    for stmt in statements:
        stmt_kind = str(stmt.get("kind") or "").lower()
        if "foreign" in stmt_kind and not has_declared_foreign:
            result.add_warning(
                rule_id="S4-EARNINGS-MISMATCH-FOREIGN",
                message=(
                    f"You uploaded a foreign-income statement ('{stmt.get('label', stmt_kind)}'), "
                    "but didn't declare a foreign source at the profile step."
                ),
                ira_section="section 71 + section 73 (foreign-source income disclosure)",
                recommendation="Either add 'foreign income' to your profile or recheck the upload.",
            )
            result.trace(
                "S4-EARNINGS-MISMATCH-FOREIGN",
                True,
                f"stmt={stmt_kind} but declared={declared_set}",
            )
            return
        if "local" in stmt_kind and not has_declared_local and has_declared_foreign:
            result.add_warning(
                rule_id="S4-EARNINGS-MISMATCH-LOCAL",
                message=(
                    f"You uploaded a local-source statement ('{stmt.get('label', stmt_kind)}'), "
                    "but only declared foreign income at the profile step."
                ),
                ira_section="section 5 + section 6 (Sri Lanka-source income)",
                recommendation="Add 'local income' to your profile if you earn from Sri Lankan clients too.",
            )
            result.trace(
                "S4-EARNINGS-MISMATCH-LOCAL",
                True,
                f"stmt={stmt_kind} but declared={declared_set}",
            )
            return

    result.trace("S4-EARNINGS-MATCH", False, "statements vs declarations consistent")


def _rule_S5_related_party_check(
    result: GateResult, customer_data: dict[str, Any]
) -> None:
    """S5 reduce-tax -- early section-195 signal check on Service Providers customer plans to add.

    Lazy-imports the section-195 detector from sister branch. If unavailable, falls
    back to a conservative string-similarity heuristic (same surname etc).
    """
    sps_planned: list[dict[str, Any]] = (
        customer_data.get("planned_service_providers") or []
    )
    if not sps_planned:
        result.trace("S5-RELATED-PARTY-PRECHECK", False, "no SPs planned yet")
        return

    customer_nic = (customer_data.get("nic") or "").strip().lower()
    customer_address = customer_data.get("address") or {}
    customer_bank = (customer_data.get("bank_account") or "").strip()
    customer_surname = (customer_data.get("full_name") or "").strip().split()[-1:]
    customer_surname_lc = customer_surname[0].lower() if customer_surname else ""

    # Lazy import -- sister branch ships this module.
    try:
        from fiesta.compliance.related_party import detect_related_party  # noqa
        detector_available = True
    except ImportError:
        detect_related_party = None  # type: ignore[assignment]
        detector_available = False

    flagged_any = False
    for sp in sps_planned:
        signals: list[str] = []
        if detector_available and detect_related_party is not None:
            try:
                rp_result = detect_related_party(
                    customer={
                        "nic": customer_nic,
                        "address": customer_address,
                        "bank_account": customer_bank,
                        "full_name": customer_data.get("full_name", ""),
                    },
                    service_provider=sp,
                )
                if getattr(rp_result, "should_default_on_disclosure", False) or getattr(rp_result, "is_related", False):
                    signals = [
                        (s.value if hasattr(s, "value") else (s.name if hasattr(s, "name") else str(s)))
                        for s in getattr(rp_result, "signals", [])
                    ]
            except Exception:  # noqa: BLE001 -- degrade gracefully
                signals = []
        if not signals:
            # Heuristic fallback: same surname or same address line.
            sp_name = (sp.get("full_name") or "").strip().lower()
            sp_addr = sp.get("address") or {}
            sp_addr_line1 = (
                (sp_addr.get("line1") or "")
                if isinstance(sp_addr, dict)
                else ""
            ).strip().lower()
            customer_addr_line1 = (
                customer_address.get("line1") or ""
                if isinstance(customer_address, dict)
                else ""
            ).strip().lower()
            if customer_surname_lc and customer_surname_lc in sp_name.split():
                signals.append("surname_match_heuristic")
            if (
                sp_addr_line1
                and customer_addr_line1
                and sp_addr_line1 == customer_addr_line1
            ):
                signals.append("address_match_heuristic")
            if (
                sp.get("nic")
                and customer_nic
                and str(sp.get("nic")).lower() == customer_nic
            ):
                signals.append("nic_exact_match")

        if signals:
            flagged_any = True
            sp_name_display = sp.get("full_name") or "(unnamed)"
            result.add_warning(
                rule_id="S5-RELATED-PARTY-PRECHECK",
                message=(
                    f"'{sp_name_display}' looks like it may be a related party "
                    f"({', '.join(signals)}). Related-party arrangements are allowed "
                    "but must be disclosed."
                ),
                ira_section="section 195 (related-party transaction disclosure)",
                recommendation=(
                    "We'll auto-enable section-195 disclosure on the service agreement "
                    "when you add this Service Provider. You don't need to do anything now."
                ),
            )

    result.trace(
        "S5-RELATED-PARTY-PRECHECK",
        flagged_any,
        f"detector_available={detector_available}; checked {len(sps_planned)} SPs",
    )


def _rule_S6_sp_fee_vs_qualification(
    result: GateResult, customer_data: dict[str, Any]
) -> None:
    """S6 -- Service Provider fee defensibility (above-market + below-qualification).

    IRA section 6 "wholly, exclusively, necessarily" -- a fee paid to a junior who
    doesn't justify the rate weakens the deductibility argument.
    """
    sp = customer_data.get("service_provider") or {}
    if not sp:
        result.trace("S6-SP-FEE-VS-QUAL", False, "no SP supplied")
        return

    qualification = (sp.get("qualification_tier") or "").strip().lower()
    monthly_fee = float(sp.get("monthly_fee_lkr") or 0)
    if not qualification or monthly_fee <= 0:
        result.trace(
            "S6-SP-FEE-VS-QUAL",
            False,
            f"missing qual='{qualification}' or fee={monthly_fee}",
        )
        return

    ceiling = SP_QUALIFICATION_TIERS.get(qualification)
    if ceiling is None:
        result.add_warning(
            rule_id="S6-SP-QUAL-UNKNOWN",
            message=(
                f"Qualification tier '{qualification}' isn't in our reference "
                "table -- we can't auto-check the fee defensibility."
            ),
            ira_section="section 6 (wholly, exclusively, necessarily)",
            recommendation="Use one of: junior, mid, senior, specialist.",
        )
        result.trace("S6-SP-FEE-VS-QUAL", True, f"unknown tier '{qualification}'")
        return

    if monthly_fee > ceiling * SP_RATE_MARKET_CEILING_MULTIPLIER:
        result.add_warning(
            rule_id="S6-SP-FEE-ABOVE-MARKET",
            message=(
                f"Rs {int(monthly_fee):,}/mo is above the typical ceiling "
                f"(Rs {int(ceiling):,}) for the '{qualification}' tier."
            ),
            ira_section="section 6 (wholly, exclusively, necessarily -- fee must be defensible)",
            recommendation=(
                "Consider documenting why the fee is justified (specialist skill, "
                "exclusive engagement, high volume) or move to a higher tier."
            ),
        )
        result.trace(
            "S6-SP-FEE-VS-QUAL",
            True,
            f"fee={monthly_fee} > {ceiling}*{SP_RATE_MARKET_CEILING_MULTIPLIER}",
        )
    else:
        result.trace(
            "S6-SP-FEE-VS-QUAL",
            False,
            f"fee={monthly_fee} <= ceiling={ceiling}*{SP_RATE_MARKET_CEILING_MULTIPLIER}",
        )


def _rule_S7_rental_market_rate(
    result: GateResult, customer_data: dict[str, Any]
) -> None:
    """S7 -- rental amount sanity-check vs CBSL housing index (fallback heuristic).

    A claimed rental of Rs 1M/month for a 500 sqft room is implausible and
    weakens the deductibility argument.
    """
    rental = customer_data.get("rental") or {}
    monthly_rent = float(rental.get("monthly_rent_lkr") or 0)
    sqft = float(rental.get("square_feet") or 0)
    if monthly_rent <= 0 or sqft <= 0:
        result.trace(
            "S7-RENTAL-MARKET-RATE",
            False,
            f"insufficient data (rent={monthly_rent}, sqft={sqft})",
        )
        return

    expected_max = sqft * RENT_INDEX_FALLBACK_LKR_PER_SQFT * 1.5  # 50% above index
    if monthly_rent > expected_max:
        result.add_warning(
            rule_id="S7-RENTAL-ABOVE-INDEX",
            message=(
                f"Rs {int(monthly_rent):,}/mo for {int(sqft)} sqft is above the "
                f"market index (~Rs {int(expected_max):,} max)."
            ),
            ira_section="section 6 (wholly, exclusively, necessarily) + section 60 (deduction caps)",
            recommendation=(
                "If the unit is in a premium area, save the listing comp as "
                "supporting evidence."
            ),
        )
        result.trace(
            "S7-RENTAL-MARKET-RATE",
            True,
            f"rent {monthly_rent} > {expected_max}",
        )
    else:
        result.trace(
            "S7-RENTAL-MARKET-RATE",
            False,
            f"rent {monthly_rent} within {expected_max}",
        )


def _rule_S8_section_195_disclosure(
    result: GateResult, customer_data: dict[str, Any]
) -> None:
    """S8 -- section-195 default-ON when related-party signals fired.

    This is the canonical Risk-B mitigation: if the section-195 detector returns
    is_related=True, the service agreement template MUST include the
    related-party disclosure block. Customer can NOT override this.
    """
    sp = customer_data.get("service_provider") or {}
    if not sp:
        result.trace("S8-RELATED-PARTY-DISCLOSURE", False, "no SP supplied")
        return

    # Lazy import
    try:
        from fiesta.compliance.related_party import detect_related_party
    except ImportError:
        detect_related_party = None  # type: ignore[assignment]

    is_related = False
    signal_summary = ""
    if detect_related_party is not None:
        try:
            rp = detect_related_party(
                customer={
                    "nic": customer_data.get("nic", ""),
                    "address": customer_data.get("address", {}),
                    "bank_account": customer_data.get("bank_account", ""),
                    "full_name": customer_data.get("full_name", ""),
                },
                service_provider=sp,
            )
            # Wave4 API: should_default_on_disclosure indicates related-party determination
            is_related = bool(
                getattr(rp, "should_default_on_disclosure", False)
                or getattr(rp, "is_related", False)
            )
            signals = getattr(rp, "signals", [])
            signal_summary = ", ".join(
                getattr(s, "value", None) or getattr(s, "name", str(s)) for s in signals
            )
        except Exception:  # noqa: BLE001
            is_related = False
    else:
        # Heuristic: same NIC or same address.
        cust_nic = (customer_data.get("nic") or "").lower()
        sp_nic = (sp.get("nic") or "").lower()
        cust_addr_obj = customer_data.get("address") or {}
        sp_addr_obj = sp.get("address") or {}
        cust_addr = (
            cust_addr_obj.get("line1", "") if isinstance(cust_addr_obj, dict) else ""
        ).lower()
        sp_addr = (
            sp_addr_obj.get("line1", "") if isinstance(sp_addr_obj, dict) else ""
        ).lower()
        if cust_nic and sp_nic and cust_nic == sp_nic:
            is_related = True
            signal_summary = "nic_exact_match (heuristic)"
        elif cust_addr and sp_addr and cust_addr == sp_addr:
            is_related = True
            signal_summary = "address_match (heuristic)"

    # Determine if disclosure flag is already set on the agreement.
    agreement = customer_data.get("agreement") or {}
    disclosure_enabled = bool(agreement.get("section_195_disclosure_enabled"))
    customer_attempted_override = bool(agreement.get("section_195_override_requested"))

    if is_related:
        if not disclosure_enabled:
            # Auto-enable (caller should write back); surface as warning so
            # the customer understands it's been enabled on their behalf.
            result.add_warning(
                rule_id="S8-SECTION-195-AUTO-ENABLED",
                message=(
                    "Related-party signals detected for this Service Provider "
                    f"({signal_summary or 'heuristic'}). We've enabled the "
                    "section-195 disclosure block on your agreement."
                ),
                ira_section="section 195 (related-party transaction disclosure -- DEFAULT ON)",
                recommendation=(
                    "This protects the deduction. The disclosure is a single "
                    "paragraph and does not change the fee or the work scope."
                ),
            )
        if customer_attempted_override:
            result.add_block(
                rule_id="S8-SECTION-195-OVERRIDE-DENIED",
                message=(
                    "Section-195 disclosure cannot be turned off when related-party "
                    "signals are present."
                ),
                ira_section="section 195",
                recommendation=(
                    "Book a consultant if you believe the signals are a false positive."
                ),
            )
    result.trace(
        "S8-SECTION-195-DISCLOSURE",
        is_related,
        f"is_related={is_related}; disclosure_enabled={disclosure_enabled}; override={customer_attempted_override}",
    )


def _rule_S9_rental_rate_vs_index(
    result: GateResult, customer_data: dict[str, Any]
) -> None:
    """S9 -- rental-agreement rate sanity-check (reuses S7 logic for the agreement)."""
    agreement = customer_data.get("agreement") or {}
    monthly_rent = float(agreement.get("monthly_rent_lkr") or 0)
    sqft = float(agreement.get("square_feet") or 0)
    if monthly_rent <= 0 or sqft <= 0:
        result.trace(
            "S9-RENTAL-RATE-VS-INDEX",
            False,
            f"insufficient data (rent={monthly_rent}, sqft={sqft})",
        )
        return
    expected_max = sqft * RENT_INDEX_FALLBACK_LKR_PER_SQFT * 1.5
    if monthly_rent > expected_max:
        result.add_warning(
            rule_id="S9-RENTAL-AGREEMENT-ABOVE-INDEX",
            message=(
                f"Agreement rent Rs {int(monthly_rent):,}/mo for {int(sqft)} sqft "
                f"exceeds the CBSL housing index (~Rs {int(expected_max):,} max)."
            ),
            ira_section="section 6 + section 60",
            recommendation=(
                "Attach a market comp (Lamudi / ikman.lk listing in the same area) "
                "to the agreement file."
            ),
        )
        result.trace(
            "S9-RENTAL-RATE-VS-INDEX",
            True,
            f"rent {monthly_rent} > {expected_max}",
        )
    else:
        result.trace(
            "S9-RENTAL-RATE-VS-INDEX",
            False,
            "rent within index",
        )


def _rule_S12_deduction_ratio(
    result: GateResult, customer_data: dict[str, Any]
) -> None:
    """S12 -- total deductions vs gross income ratio.

    >40% = yellow warning; >60% = red block (cannot proceed without consultant).
    Threshold tuning is a CEO decision (see X6_DESIGN.md "Design decisions for CEO").
    """
    gross = float(customer_data.get("gross_income_lkr") or 0)
    deductions = float(customer_data.get("total_deductions_lkr") or 0)
    if gross <= 0:
        result.trace(
            "S12-DEDUCTION-RATIO",
            False,
            f"gross income missing or zero ({gross})",
        )
        return
    ratio = deductions / gross
    if ratio > DEDUCTION_RATIO_BLOCK_THRESHOLD:
        result.add_block(
            rule_id="S12-DEDUCTION-RATIO-EXCESSIVE",
            message=(
                f"Your deductions ({int(ratio * 100)}% of gross income) exceed "
                f"the {int(DEDUCTION_RATIO_BLOCK_THRESHOLD * 100)}% ceiling that "
                "the IRD typically reviews."
            ),
            ira_section="section 6 (deductions must be defensible -- not exceed business reality)",
            recommendation=(
                "Book a 30-min consultant review (Rs 5,000) -- we can either "
                "find more legitimate deductions you're missing, or trim ones "
                "that aren't fully defensible."
            ),
        )
        result.trace(
            "S12-DEDUCTION-RATIO",
            True,
            f"ratio={ratio:.2%} > block threshold",
        )
    elif ratio > DEDUCTION_RATIO_WARN_THRESHOLD:
        result.add_warning(
            rule_id="S12-DEDUCTION-RATIO-HIGH",
            message=(
                f"Your deductions are {int(ratio * 100)}% of gross income -- "
                f"above the {int(DEDUCTION_RATIO_WARN_THRESHOLD * 100)}% level "
                "where IRD reviews become more likely."
            ),
            ira_section="section 6 (deductions must be wholly, exclusively, necessarily)",
            recommendation=(
                "Make sure each deduction has supporting documentation. "
                "Consider a consultant pre-review if any line item lacks invoices/agreements."
            ),
        )
        result.trace(
            "S12-DEDUCTION-RATIO",
            True,
            f"ratio={ratio:.2%} > warn threshold",
        )
    else:
        result.trace(
            "S12-DEDUCTION-RATIO",
            False,
            f"ratio={ratio:.2%} within thresholds",
        )


def _rule_S14_final_gate(
    result: GateResult, customer_data: dict[str, Any]
) -> None:
    """S14 submit -- final gate. All critical issues resolved + default-on disclosures present."""
    unresolved_warnings: list[str] = (
        customer_data.get("unresolved_prior_warnings") or []
    )
    if unresolved_warnings:
        result.add_warning(
            rule_id="S14-UNRESOLVED-WARNINGS",
            message=(
                f"{len(unresolved_warnings)} warning(s) from earlier screens "
                "are still un-acknowledged."
            ),
            ira_section="(workflow integrity)",
            recommendation=(
                "Click through each highlighted screen and confirm you've addressed it."
            ),
        )
        result.trace(
            "S14-UNRESOLVED-WARNINGS",
            True,
            f"unresolved: {unresolved_warnings}",
        )
    else:
        result.trace("S14-UNRESOLVED-WARNINGS", False, "no unresolved warnings")

    # Check section-195 disclosure consistency for ALL service agreements.
    agreements: list[dict[str, Any]] = customer_data.get("service_agreements") or []
    missing_disclosure: list[str] = []
    for ag in agreements:
        if ag.get("related_party_flag") and not ag.get("section_195_disclosure_enabled"):
            missing_disclosure.append(ag.get("id") or ag.get("reference_id") or "(unknown)")
    if missing_disclosure:
        result.add_block(
            rule_id="S14-SECTION-195-MISSING",
            message=(
                f"Section-195 disclosure is required on {len(missing_disclosure)} "
                "service agreement(s) where related-party signals fired but "
                "disclosure is currently off."
            ),
            ira_section="section 195 (DEFAULT-ON cannot be bypassed at submit)",
            recommendation=(
                "Return to the affected service agreement(s) and re-enable disclosure, "
                "or book a consultant to confirm the signals are false positives."
            ),
        )
        result.trace(
            "S14-SECTION-195-MISSING",
            True,
            f"missing on: {missing_disclosure}",
        )
    else:
        result.trace("S14-SECTION-195-MISSING", False, "all required disclosures present")

    # Final deduction-ratio recheck at submit (S12 may have been bypassed by a
    # CEO override; we still hard-block at submit if it's still over the limit).
    gross = float(customer_data.get("gross_income_lkr") or 0)
    deductions = float(customer_data.get("total_deductions_lkr") or 0)
    if gross > 0:
        ratio = deductions / gross
        if (
            ratio > DEDUCTION_RATIO_BLOCK_THRESHOLD
            and not customer_data.get("ceo_override_deduction_ratio")
        ):
            result.add_block(
                rule_id="S14-DEDUCTION-RATIO-FINAL",
                message=(
                    f"Deduction ratio {int(ratio * 100)}% still exceeds the "
                    f"{int(DEDUCTION_RATIO_BLOCK_THRESHOLD * 100)}% ceiling at submit."
                ),
                ira_section="section 6",
                recommendation="Required: consultant review before submission.",
            )
            result.trace(
                "S14-DEDUCTION-RATIO-FINAL",
                True,
                f"ratio={ratio:.2%}, no override",
            )
        else:
            result.trace(
                "S14-DEDUCTION-RATIO-FINAL",
                False,
                f"ratio={ratio:.2%}, override={customer_data.get('ceo_override_deduction_ratio')}",
            )


# ---------------------------------------------------------------------------
# Screen -> rule registry
# ---------------------------------------------------------------------------
_SCREEN_RULES: dict[str, list[Any]] = {
    "S2": [_rule_S2_email_format, _rule_S2_password_strength],
    "S3": [
        _rule_S3_nic_format,
        _rule_S3_address_completeness,
        _rule_S3_foreign_income_flag_consistency,
    ],
    "S4": [_rule_S4_earnings_match_declared_sources],
    "S5": [_rule_S5_related_party_check],
    "S6": [_rule_S6_sp_fee_vs_qualification],
    "S7": [_rule_S7_rental_market_rate],
    "S8": [_rule_S8_section_195_disclosure],
    "S9": [_rule_S9_rental_rate_vs_index],
    "S12": [_rule_S12_deduction_ratio],
    "S14": [_rule_S14_final_gate],
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def gate_check(
    screen_id: str,
    customer_data: dict[str, Any],
    action: str,
) -> GateResult:
    """Run all rules registered for `screen_id` against `customer_data`.

    Args:
        screen_id:     One of S2/S3/S4/S5/S6/S7/S8/S9/S12/S14. Unknown screens
                       return a pass-through result with a trace entry noting
                       "no rules registered" (so UI integration doesn't crash
                       when a new screen is added before rules ship).
        customer_data: Dict of facts about the customer's current state on this
                       screen. Shape varies by screen -- see _rule_S*_ functions
                       for fields each rule reads.
        action:        Free-form action label (e.g. "submit", "save_draft",
                       "edit_field"). Logged in the trace for analytics; does
                       not alter rule firing in v1.0 (reserved for v1.1 when
                       rules become action-specific).

    Returns:
        GateResult -- see class docstring.

    The function NEVER raises on rule-internal failure: each rule is wrapped
    in a try/except so a buggy rule degrades to a trace entry and the rest
    of the gate continues. This is a fail-OPEN posture per individual rule;
    the overall gate is fail-OPEN by design (we'd rather false-pass than
    block a valid customer due to a bug). Rules that detect real signal
    still fire normally.
    """
    result = GateResult()
    rules = _SCREEN_RULES.get(screen_id, [])
    if not rules:
        result.trace(
            f"{screen_id}-NO-RULES",
            False,
            f"no rules registered for screen '{screen_id}', action='{action}'",
        )
        return result

    for rule_fn in rules:
        try:
            rule_fn(result, customer_data)
        except Exception as exc:  # noqa: BLE001 -- fail-open per rule
            result.trace(
                getattr(rule_fn, "__name__", "unknown_rule"),
                False,
                f"rule raised, degraded to skip: {type(exc).__name__}: {exc}",
            )

    # Recommendations roll-up: when blocks fire, suggest consultant booking.
    if result.blocks:
        result.recommendations.append(
            "We recommend a 30-minute consultant review (Rs 5,000) before proceeding."
        )
    if not result.blocks and result.warnings:
        result.recommendations.append(
            "These are flags, not blockers -- you can address them and continue."
        )

    return result


__all__ = ["GateResult", "gate_check"]
