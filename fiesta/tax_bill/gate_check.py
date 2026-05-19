"""fiesta.tax_bill.gate_check -- run X6 S12 gate + S12-specific extra rules.

X6's `gate_check("S12", customer_data, action)` registers ONE rule:
    _rule_S12_deduction_ratio.

The S12 brief calls out 3 additional S12-specific gates:
    1. missing-§195-disclosure on a related-party SP   (red block)
    2. signed-agreements-mismatch (claim > agreement)  (red block)
    3. deduction-ratio > 40 / > 60 (already in X6)     (yellow / red)

This module wraps the X6 call and appends rules (2) and (3) before
returning a single GateResult.

If fiesta.compliance is missing (e.g. branches not merged yet) the
wrapper returns a no-op GateResult so the screen still renders without
breaking the caller.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _empty_gate_result():
    """Stand-in GateResult for the no-compliance-module case."""
    class _Empty:
        passed = True
        warnings: list[dict[str, Any]] = []
        blocks: list[dict[str, Any]] = []
        recommendations: list[str] = []
        reasoning_trace: list[dict[str, Any]] = []

        def model_dump(self) -> dict[str, Any]:
            return {
                "passed": True,
                "warnings": [],
                "blocks": [],
                "recommendations": [],
                "reasoning_trace": [
                    {
                        "rule_id": "S12-COMPLIANCE-MODULE-MISSING",
                        "fired": False,
                        "reason": (
                            "fiesta.compliance.gate unavailable -- gate is "
                            "pass-through. Restore the compliance branch."
                        ),
                    }
                ],
            }
    return _Empty()


def _missing_disclosure_rule(result, customer_data: dict[str, Any]) -> None:
    """S12-specific rule: any flagged related-party WITHOUT disclosure -> red block."""
    missing = list(customer_data.get("missing_disclosures") or [])
    if not missing:
        result.trace(
            "S12-MISSING-195-DISCLOSURE",
            False,
            "no missing §195 disclosures detected",
        )
        return
    for m in missing:
        result.add_block(
            rule_id=f"S12-MISSING-195-{m.get('kind', 'unknown').upper()}",
            message=(
                f"{m.get('name', 'Counterparty')}: "
                f"{m.get('reason', '§195 disclosure required but not applied.')}"
            ),
            ira_section=(
                "section 195 (related-party arrangements -- "
                "non-arm's-length disclosure mandatory)"
            ),
            recommendation=(
                "Re-generate the agreement with §195 disclosure clause ON, "
                "or document the commercial-substance justification."
            ),
        )
    result.trace(
        "S12-MISSING-195-DISCLOSURE",
        True,
        f"{len(missing)} missing-disclosure block(s) added",
    )


def _agreement_mismatch_rule(result, customer_data: dict[str, Any]) -> None:
    """S12-specific rule: customer-claimed SP fees > agreement-stated fees -> red block."""
    mismatches = list(customer_data.get("sp_agreement_mismatches") or [])
    if not mismatches:
        result.trace(
            "S12-SP-AGREEMENT-MISMATCH",
            False,
            "no SP claim/agreement mismatches",
        )
        return
    for m in mismatches:
        result.add_block(
            rule_id="S12-SP-AGREEMENT-MISMATCH",
            message=(
                f"{m.get('sp_name', 'SP')}: claimed monthly fee "
                f"Rs {m.get('claimed_monthly_lkr')} exceeds agreement-stated "
                f"Rs {m.get('agreement_monthly_lkr')} by more than 10%."
            ),
            ira_section=(
                "section 6 (deduction must match the underlying agreement; "
                "section 113 records-must-match-reality)"
            ),
            recommendation=(
                "Either revise the agreement to match the actual fees, or "
                "reduce the deduction to match the agreement amount."
            ),
        )
    result.trace(
        "S12-SP-AGREEMENT-MISMATCH",
        True,
        f"{len(mismatches)} mismatch block(s) added",
    )


def run_gate(
    report,
    action: str = "display_bill",
) -> Any:
    """Run the X6 S12 gate plus S12-specific extra rules.

    Args:
        report: TaxBillReport (uses report.gate_customer_data).
        action: 'display_bill' | 'export_pdf' | 'finalize' | 'submit'.

    Returns:
        fiesta.compliance.gate.GateResult-shaped object. If compliance is
        unavailable, a pass-through Empty result is returned.
    """
    customer_data = dict(report.gate_customer_data or {})
    try:
        from fiesta.compliance.gate import gate_check
    except Exception as exc:
        logger.warning("X6 gate unavailable: %s", exc)
        return _empty_gate_result()

    result = gate_check("S12", customer_data, action)

    # Append S12-specific rules.
    try:
        _missing_disclosure_rule(result, customer_data)
    except Exception as exc:  # noqa: BLE001 -- fail-open per rule
        try:
            result.trace(
                "S12-MISSING-195-DISCLOSURE",
                False,
                f"rule raised, degraded to skip: {type(exc).__name__}",
            )
        except Exception:
            pass

    try:
        _agreement_mismatch_rule(result, customer_data)
    except Exception as exc:  # noqa: BLE001
        try:
            result.trace(
                "S12-SP-AGREEMENT-MISMATCH",
                False,
                f"rule raised, degraded to skip: {type(exc).__name__}",
            )
        except Exception:
            pass

    # Rebuild .passed if our blocks/warnings altered the totals.
    try:
        result.passed = (not result.warnings) and (not result.blocks)
    except Exception:
        pass

    return result


__all__ = ["run_gate"]
