"""fiesta.submit.final_gate -- S14-specific wrapper around X6 gate_check.

Why a wrapper
-------------
The X6 gate (`fiesta.compliance.gate.gate_check`) is the upstream cross-screen
gate. It already has an `S14` rule registered (commit 7397e8c, X6 design).
The wrapper here:

  1. Tries to call X6 first (the SOURCE OF TRUTH for cross-screen rules).
  2. ALWAYS runs S14-LOCAL rules (missing-attestation -- not in X6 because
     attestation is S14-internal state). This protects against accidental
     S14 launch with no attestation flow wired.
  3. Merges results into a single GateResult-shaped dict the routes can render.

The wrapper is also fail-OPEN per the X6 design (a buggy rule degrades, the
gate still completes), but the S14-LOCAL rules are FAIL-CLOSED because they
gate the launch-critical attestation -- there is no "best-effort" path here.

Tuning constants
----------------
These mirror X6's thresholds. They are duplicated here ONLY for the local
fallback path; when X6 is wired the upstream values win.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# X6 mirror constants (see fiesta/compliance/gate.py)
DEDUCTION_RATIO_BLOCK_THRESHOLD = 0.60


class GateOutcome:
    """Lightweight gate-result wrapper (mirror of X6's GateResult).

    We use a plain class here (not Pydantic) to keep the submit module
    Pydantic-free -- only the X6 upstream needs it. Tests inspect the
    same field shape (passed / warnings / blocks / recommendations /
    reasoning_trace).
    """

    def __init__(self) -> None:
        self.passed: bool = True
        self.warnings: list[dict[str, Any]] = []
        self.blocks: list[dict[str, Any]] = []
        self.recommendations: list[str] = []
        self.reasoning_trace: list[dict[str, Any]] = []

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

    def merge_from(self, other: Any) -> None:
        """Merge fields from another GateResult-like object (e.g. X6's)."""
        if other is None:
            return
        for w in getattr(other, "warnings", []) or []:
            if w not in self.warnings:
                self.warnings.append(w)
        for b in getattr(other, "blocks", []) or []:
            if b not in self.blocks:
                self.blocks.append(b)
        for r in getattr(other, "recommendations", []) or []:
            if r not in self.recommendations:
                self.recommendations.append(r)
        for t in getattr(other, "reasoning_trace", []) or []:
            self.reasoning_trace.append(t)
        if self.warnings or self.blocks:
            self.passed = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "warnings": list(self.warnings),
            "blocks": list(self.blocks),
            "recommendations": list(self.recommendations),
            "reasoning_trace": list(self.reasoning_trace),
        }


# ---------------------------------------------------------------------------
# S14-LOCAL rules -- these MUST always run, even if X6 is unavailable.
# ---------------------------------------------------------------------------
def _rule_S14_missing_attestation(
    result: GateOutcome, customer_data: dict[str, Any]
) -> None:
    """S14 -- attestation must be present before export/walkthrough access."""
    action = customer_data.get("_action", "")
    has_attestation = bool(customer_data.get("attestation_signed_at"))
    # Missing-attestation only blocks the post-attestation actions, not the
    # initial render of S14 itself (otherwise the customer can never SEE the
    # screen that asks them to attest).
    if action in {"export", "walkthrough", "mark-filed"} and not has_attestation:
        result.add_block(
            rule_id="S14-MISSING-ATTESTATION",
            message=(
                "Attestation is required before generating the IRD export "
                "or accessing the walkthrough."
            ),
            ira_section=(
                "section 195 (responsible-filer declaration) + "
                "Electronic Transactions Act 19 of 2006"
            ),
            recommendation=(
                "Return to /submit, review the declaration, and sign it."
            ),
        )
        result.trace(
            "S14-MISSING-ATTESTATION",
            True,
            f"action={action!r} requires attestation but none signed",
        )
    else:
        result.trace(
            "S14-MISSING-ATTESTATION",
            False,
            f"action={action!r}, has_attestation={has_attestation}",
        )


def _rule_S14_deduction_ratio_fallback(
    result: GateOutcome, customer_data: dict[str, Any]
) -> None:
    """Local fallback for the deduction-ratio block.

    Runs ONLY if X6 didn't already log a trace entry for the same rule (we
    detect this by checking the reasoning_trace before adding our own). This
    is belt-and-braces: when X6 is wired, its version wins; when it isn't,
    we still catch the 60% block.
    """
    already_logged = any(
        t.get("rule_id") == "S14-DEDUCTION-RATIO-FINAL"
        for t in result.reasoning_trace
    )
    if already_logged:
        return

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
                    f"{int(DEDUCTION_RATIO_BLOCK_THRESHOLD * 100)}% ceiling "
                    "at submit."
                ),
                ira_section="section 6",
                recommendation="Required: consultant review before submission.",
            )
            result.trace(
                "S14-DEDUCTION-RATIO-FINAL",
                True,
                f"ratio={ratio:.2%}, no override (local fallback)",
            )
        else:
            result.trace(
                "S14-DEDUCTION-RATIO-FINAL",
                False,
                f"ratio={ratio:.2%}, override={customer_data.get('ceo_override_deduction_ratio')} "
                "(local fallback)",
            )


def _rule_S14_section_195_missing_fallback(
    result: GateOutcome, customer_data: dict[str, Any]
) -> None:
    """Local fallback for the §195-missing block."""
    already_logged = any(
        t.get("rule_id") == "S14-SECTION-195-MISSING"
        for t in result.reasoning_trace
    )
    if already_logged:
        return

    agreements: list[dict[str, Any]] = customer_data.get("service_agreements") or []
    missing_disclosure: list[str] = []
    for ag in agreements:
        if ag.get("related_party_flag") and not ag.get(
            "section_195_disclosure_enabled"
        ):
            missing_disclosure.append(
                ag.get("id") or ag.get("reference_id") or "(unknown)"
            )
    if missing_disclosure:
        result.add_block(
            rule_id="S14-SECTION-195-MISSING",
            message=(
                f"Section-195 disclosure is required on "
                f"{len(missing_disclosure)} service agreement(s) where "
                "related-party signals fired but disclosure is currently off."
            ),
            ira_section="section 195 (DEFAULT-ON cannot be bypassed at submit)",
            recommendation=(
                "Return to the affected service agreement(s) and re-enable "
                "disclosure, or book a consultant."
            ),
        )
        result.trace(
            "S14-SECTION-195-MISSING",
            True,
            f"missing on: {missing_disclosure} (local fallback)",
        )
    else:
        result.trace(
            "S14-SECTION-195-MISSING",
            False,
            "all required disclosures present (local fallback)",
        )


def _rule_S14_unresolved_warnings_fallback(
    result: GateOutcome, customer_data: dict[str, Any]
) -> None:
    """Local fallback for unresolved upstream warnings (yellow)."""
    already_logged = any(
        t.get("rule_id") == "S14-UNRESOLVED-WARNINGS"
        for t in result.reasoning_trace
    )
    if already_logged:
        return

    unresolved = customer_data.get("unresolved_prior_warnings") or []
    if unresolved:
        result.add_warning(
            rule_id="S14-UNRESOLVED-WARNINGS",
            message=(
                f"{len(unresolved)} warning(s) from earlier screens are still "
                "un-acknowledged."
            ),
            ira_section="(workflow integrity)",
            recommendation=(
                "Click through each highlighted screen and confirm you've "
                "addressed it."
            ),
        )
        result.trace(
            "S14-UNRESOLVED-WARNINGS",
            True,
            f"unresolved: {unresolved} (local fallback)",
        )
    else:
        result.trace(
            "S14-UNRESOLVED-WARNINGS",
            False,
            "no unresolved warnings (local fallback)",
        )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def run_final_gate(
    customer_data: dict[str, Any], action: str = "submit"
) -> GateOutcome:
    """Run the S14 final gate.

    Args:
        customer_data: Dict of facts about the customer's current state. Shape:
            - unresolved_prior_warnings: list[str] (X6 sets this)
            - service_agreements: list[dict] (each must have related_party_flag
              + section_195_disclosure_enabled keys)
            - gross_income_lkr: float
            - total_deductions_lkr: float
            - ceo_override_deduction_ratio: bool (CEO escalation hook)
            - attestation_signed_at: datetime|None
        action: One of 'submit', 'export', 'walkthrough', 'mark-filed'. The
            attestation-required check ONLY fires for the post-attestation
            actions (not the initial /submit render).

    Returns:
        GateOutcome with passed/warnings/blocks/recommendations/reasoning_trace.

    The caller writes a compliance_gate_event row via
    fiesta.compliance.events.log_gate_check (when X6 is wired). We don't
    write directly here -- pure function.
    """
    result = GateOutcome()
    # Make `action` introspectable by rules without leaking it into the rule
    # signatures (the X6 upstream rules don't take action).
    customer_data = dict(customer_data)
    customer_data["_action"] = action

    # 1. Try X6 upstream (source of truth for cross-screen rules).
    try:
        from fiesta.compliance.gate import gate_check as _x6_gate_check  # noqa: WPS433

        x6_result = _x6_gate_check("S14", customer_data, action)
        result.merge_from(x6_result)
        logger.debug(
            "S14 final_gate: X6 upstream contributed %s warnings, %s blocks",
            len(getattr(x6_result, "warnings", []) or []),
            len(getattr(x6_result, "blocks", []) or []),
        )
    except ImportError:
        logger.info(
            "S14 final_gate: X6 upstream not available on this branch; "
            "using local fallback rules only"
        )
    except Exception as exc:  # noqa: BLE001 -- fail-open on X6 errors
        logger.warning(
            "S14 final_gate: X6 upstream raised %s: %s -- using local fallback",
            type(exc).__name__,
            exc,
        )

    # 2. Always run S14-LOCAL fallback rules (idempotent if X6 fired same id).
    _rule_S14_unresolved_warnings_fallback(result, customer_data)
    _rule_S14_section_195_missing_fallback(result, customer_data)
    _rule_S14_deduction_ratio_fallback(result, customer_data)

    # 3. S14-INTERNAL: attestation-required is ONLY here, not in X6.
    _rule_S14_missing_attestation(result, customer_data)

    # Recommendations roll-up (mirror X6 behaviour).
    if result.blocks and not any(
        "consultant" in r.lower() for r in result.recommendations
    ):
        result.recommendations.append(
            "We recommend a 30-minute consultant review (Rs 5,000) "
            "before proceeding."
        )
    if not result.blocks and result.warnings:
        result.recommendations.append(
            "These are flags, not blockers -- you can address them and continue."
        )

    return result


__all__ = ["GateOutcome", "run_final_gate"]
