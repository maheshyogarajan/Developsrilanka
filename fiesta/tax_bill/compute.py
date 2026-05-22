"""fiesta.tax_bill.compute -- wrap fiesta.tax.engine for the S12 view.

compute_tax_bill(user_id, tax_year) -> TaxBillReport

Two engine calls:
    1. With customer's actual deductions -> the final bill.
    2. With deductions zeroed            -> the "without FIESTA" bill.
The delta is the savings_vs_no_deductions headline number.

Audit-defensibility score
-------------------------
A 0-100 numeric + bucket label ("Strong" / "Moderate" / "At-Risk") computed
from:
    - % of deductions with collected/submitted evidence
    - whether SP §195 disclosures are applied where required
    - whether rental §195 disclosures are applied where required
    - whether SP claim ↔ agreement amounts match (no mismatches)
    - whether deduction ratio falls under the X6 warn / block thresholds
    - whether the FX module had unconverted-currency warnings

Scoring weights are CEO decisions -- see docstring on
`_score_audit_defensibility` for the rationale.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional

from .aggregator import (
    TaxInputs,
    assemble_tax_inputs,
    canonical_tax_year_enum,
)
from .audit_defensibility import score_audit_defensibility as _score_audit_defensibility_v2

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class TaxBillReport:
    """Full S12 outcome.

    Carries:
        - the TaxInputs snapshot (verbatim, for the breakdown UI + audit pack)
        - the two engine computations (with + without deductions)
        - the headline FIESTA savings number
        - the audit-defensibility score
        - the X6 gate-input dict (pre-computed for run_gate())
    """

    user_id: int
    tax_year_s4_format: str
    tax_year_s5_format: str

    # The aggregator snapshot.
    inputs: TaxInputs = field(default_factory=lambda: None)  # type: ignore[assignment]

    # Engine outputs (TaxComputation pydantic objects from fiesta.tax).
    computation_with_deductions: Any = None
    computation_without_deductions: Any = None

    # Roll-up numbers used by templates + PDF.
    gross_income_lkr: Decimal = Decimal("0")
    total_deductions_lkr: Decimal = Decimal("0")
    taxable_income_lkr: Decimal = Decimal("0")
    gross_tax_payable_lkr: Decimal = Decimal("0")
    net_tax_payable_lkr: Decimal = Decimal("0")

    # "What would I pay without deductions?" delta -- the FIESTA value number.
    tax_without_deductions_lkr: Decimal = Decimal("0")
    savings_vs_no_deductions_lkr: Decimal = Decimal("0")

    # Audit defensibility.
    audit_defensibility_score: int = 0          # 0..100
    audit_defensibility_label: str = "At-Risk"  # Strong / Moderate / At-Risk
    audit_score_components: dict[str, Any] = field(default_factory=dict)

    # X6 gate kwargs prebuilt -- routes call run_gate(report) -> GateResult.
    gate_customer_data: dict[str, Any] = field(default_factory=dict)

    # Finalize state -- writable by /finalize endpoint.
    is_finalized: bool = False

    # Computation engine error (if any) for graceful UI degradation.
    engine_error: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _zeroed_deductions_kwargs() -> dict[str, Decimal]:
    return {
        "solar_investment_lkr": Decimal("0"),
        "rent_relief_lkr": Decimal("0"),
        "expenditure_relief_lkr": Decimal("0"),
    }


def _run_engine(
    income_kwargs: dict[str, Decimal],
    deductions_kwargs: dict[str, Decimal],
    tax_year_enum: Any,
    senior_citizen: bool,
):
    """Call into fiesta.tax.engine. Returns TaxComputation or raises."""
    from fiesta.tax import Income, Deductions, compute_tax
    income = Income(**{k: v for k, v in income_kwargs.items()})
    deductions = Deductions(**{k: v for k, v in deductions_kwargs.items()})
    return compute_tax(
        income=income,
        deductions=deductions,
        year=tax_year_enum,
        senior_citizen=senior_citizen,
    )


# ---------------------------------------------------------------------------
# Audit-defensibility scoring
# ---------------------------------------------------------------------------


def _score_audit_defensibility(
    inputs: TaxInputs,
    gross_income: Decimal,
    total_deductions: Decimal,
) -> tuple[int, str, dict[str, Any]]:
    """Compute 0-100 score + bucket label.

    Weights (CEO decision -- see decision notes at end of dispatch):
        Evidence coverage         : 30
        SP §195 disclosure        : 15
        Rental §195 disclosure    : 15
        SP claim/agreement match  : 10
        Deduction ratio within thresholds : 15
        FX conversion clean       : 5
        Profile completeness      : 5
        Stamp duty current        : 5
                          TOTAL    100

    Buckets:
        >= 80 -- Strong   (defensible position; routine IRD review ok)
        50-79 -- Moderate (some flags; recommend evidence cleanup pre-submit)
        < 50  -- At-Risk  (block / consultant review)
    """
    components: dict[str, Any] = {}

    # 1. Evidence coverage (30 pts) ----------------------------------------
    total_ded_count = len(inputs.deductions_itemised)
    if total_ded_count == 0:
        evidence_pts = 30  # nothing claimed = nothing to fail
        components["evidence_coverage"] = {
            "score": 30, "rationale": "No deductions claimed."
        }
    else:
        ratio = inputs.deductions_with_evidence_count / total_ded_count
        evidence_pts = int(round(ratio * 30))
        components["evidence_coverage"] = {
            "score": evidence_pts,
            "rationale": (
                f"{inputs.deductions_with_evidence_count} of {total_ded_count} "
                f"deductions have evidence collected/submitted "
                f"({int(ratio * 100)}%)."
            ),
        }

    # 2. SP §195 disclosure (15 pts) ---------------------------------------
    if inputs.sp_disclosure_required_count == 0:
        sp195_pts = 15
        components["sp_195_disclosure"] = {
            "score": 15, "rationale": "No related-party SPs detected."
        }
    elif inputs.sp_disclosure_applied_count == inputs.sp_disclosure_required_count:
        sp195_pts = 15
        components["sp_195_disclosure"] = {
            "score": 15,
            "rationale": (
                f"§195 disclosure applied on all "
                f"{inputs.sp_disclosure_required_count} flagged SPs."
            ),
        }
    else:
        applied_ratio = (
            inputs.sp_disclosure_applied_count
            / inputs.sp_disclosure_required_count
        )
        sp195_pts = int(round(applied_ratio * 15))
        components["sp_195_disclosure"] = {
            "score": sp195_pts,
            "rationale": (
                f"§195 disclosure applied on {inputs.sp_disclosure_applied_count} "
                f"of {inputs.sp_disclosure_required_count} flagged SPs."
            ),
        }

    # 3. Rental §195 disclosure (15 pts) -----------------------------------
    if inputs.rental_disclosure_required_count == 0:
        rental195_pts = 15
        components["rental_195_disclosure"] = {
            "score": 15, "rationale": "No related-party rental arrangements."
        }
    elif (
        inputs.rental_disclosure_applied_count
        == inputs.rental_disclosure_required_count
    ):
        rental195_pts = 15
        components["rental_195_disclosure"] = {
            "score": 15,
            "rationale": (
                f"§195 disclosure applied on all "
                f"{inputs.rental_disclosure_required_count} flagged rentals."
            ),
        }
    else:
        ratio = (
            inputs.rental_disclosure_applied_count
            / inputs.rental_disclosure_required_count
        )
        rental195_pts = int(round(ratio * 15))
        components["rental_195_disclosure"] = {
            "score": rental195_pts,
            "rationale": (
                f"§195 disclosure applied on "
                f"{inputs.rental_disclosure_applied_count} of "
                f"{inputs.rental_disclosure_required_count} flagged rentals."
            ),
        }

    # 4. SP claim/agreement mismatch (10 pts) ------------------------------
    if not inputs.sp_agreement_mismatches:
        mismatch_pts = 10
        components["sp_mismatch"] = {
            "score": 10, "rationale": "No SP claim/agreement mismatches."
        }
    else:
        mismatch_pts = 0
        components["sp_mismatch"] = {
            "score": 0,
            "rationale": (
                f"{len(inputs.sp_agreement_mismatches)} SP(s) where claimed "
                "fees exceed agreement-stated fees by >10%."
            ),
        }

    # 5. Deduction ratio (15 pts) ------------------------------------------
    if gross_income > 0:
        ratio = float(total_deductions) / float(gross_income)
        if ratio <= 0.40:
            ded_ratio_pts = 15
            label = f"{int(ratio * 100)}% (within safe range)."
        elif ratio <= 0.60:
            ded_ratio_pts = 8
            label = f"{int(ratio * 100)}% (warning band — IRD reviews more likely)."
        else:
            ded_ratio_pts = 0
            label = f"{int(ratio * 100)}% (block threshold — consultant required)."
    else:
        ded_ratio_pts = 15
        label = "No gross income reported yet."
    components["deduction_ratio"] = {"score": ded_ratio_pts, "rationale": label}

    # 6. FX clean (5 pts) --------------------------------------------------
    if not inputs.income_unconverted_currencies:
        fx_pts = 5
        components["fx_clean"] = {
            "score": 5, "rationale": "All income converted to LKR."
        }
    else:
        fx_pts = 0
        components["fx_clean"] = {
            "score": 0,
            "rationale": (
                f"Unconverted currencies remain: "
                f"{', '.join(inputs.income_unconverted_currencies)}."
            ),
        }

    # 7. Profile complete (5 pts) ------------------------------------------
    profile_pts = 5 if inputs.profile_complete else 0
    components["profile_complete"] = {
        "score": profile_pts,
        "rationale": (
            "S3 profile complete." if inputs.profile_complete
            else "S3 profile not yet complete — finish before submission."
        ),
    }

    # 8. Stamp duty current (5 pts) ----------------------------------------
    if inputs.rental_stamp_duty_outstanding_count == 0:
        stamp_pts = 5
        components["stamp_duty"] = {
            "score": 5, "rationale": "Stamp duty not outstanding."
        }
    else:
        stamp_pts = 0
        components["stamp_duty"] = {
            "score": 0,
            "rationale": (
                f"{inputs.rental_stamp_duty_outstanding_count} rental "
                "agreement(s) have outstanding stamp duty."
            ),
        }

    total = (
        evidence_pts
        + sp195_pts
        + rental195_pts
        + mismatch_pts
        + ded_ratio_pts
        + fx_pts
        + profile_pts
        + stamp_pts
    )
    total = max(0, min(100, total))

    if total >= 80:
        label_str = "Strong"
    elif total >= 50:
        label_str = "Moderate"
    else:
        label_str = "At-Risk"

    return total, label_str, components


# ---------------------------------------------------------------------------
# X6 gate-input prep
# ---------------------------------------------------------------------------


def _build_gate_customer_data(
    inputs: TaxInputs,
    gross_income: Decimal,
    total_deductions: Decimal,
) -> dict[str, Any]:
    """Build the dict that fiesta.compliance.gate_check expects for S12.

    The S12 rule reads:
        - gross_income_lkr
        - total_deductions_lkr

    We additionally carry our own facts so the S12 / S14 rules and any
    future per-screen rules can read them:
        - related_party_service_providers (count)
        - related_party_rentals (count)
        - missing_disclosure_count
        - sp_agreement_mismatch_count
        - profile_complete
        - has_unconverted_currencies
    """
    return {
        # Required by X6 S12 rule.
        "gross_income_lkr": float(gross_income),
        "total_deductions_lkr": float(total_deductions),
        # Extra context (consumed by S12-specific rules in gate_check.py).
        "related_party_service_providers": inputs.sp_disclosure_required_count,
        "related_party_rentals": inputs.rental_disclosure_required_count,
        "missing_disclosure_count": len(inputs.missing_disclosures),
        "missing_disclosures": list(inputs.missing_disclosures),
        "sp_agreement_mismatch_count": len(inputs.sp_agreement_mismatches),
        "sp_agreement_mismatches": list(inputs.sp_agreement_mismatches),
        "profile_complete": inputs.profile_complete,
        "has_unconverted_currencies": bool(inputs.income_unconverted_currencies),
        "unconverted_currencies": list(inputs.income_unconverted_currencies),
        "deductions_with_evidence_count": inputs.deductions_with_evidence_count,
        "deductions_pending_evidence_count": (
            inputs.deductions_pending_evidence_count
        ),
        "tax_year": inputs.tax_year_s5_format,
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def compute_tax_bill(
    user_id: int,
    tax_year: str,
    pre_assembled: Optional[TaxInputs] = None,
) -> TaxBillReport:
    """Compute the full S12 outcome for one user, one tax year.

    Args:
        user_id:        FIESTA user id.
        tax_year:       any accepted form (see aggregator.normalise_tax_year).
        pre_assembled:  optional pre-built TaxInputs (test/route fast-path).

    Returns:
        TaxBillReport. If the tax engine fails to import or compute,
        `engine_error` is populated and the engine-derived fields stay at
        their defaults so the UI can still render a partial breakdown.
    """
    if pre_assembled is not None:
        inputs = pre_assembled
    else:
        inputs = assemble_tax_inputs(user_id, tax_year)

    report = TaxBillReport(
        user_id=int(user_id),
        tax_year_s4_format=inputs.tax_year_s4_format,
        tax_year_s5_format=inputs.tax_year_s5_format,
        inputs=inputs,
    )

    ty_enum = canonical_tax_year_enum(tax_year)
    if ty_enum is None:
        report.engine_error = "Tax engine import failed or unsupported tax year."
        return report

    try:
        comp_with = _run_engine(
            inputs.engine_income_kwargs,
            inputs.engine_deductions_kwargs,
            ty_enum,
            inputs.senior_citizen,
        )
    except Exception as exc:
        logger.exception("engine (with deductions) failed: %s", exc)
        report.engine_error = f"Engine failed: {type(exc).__name__}: {exc}"
        return report

    try:
        comp_without = _run_engine(
            inputs.engine_income_kwargs,
            _zeroed_deductions_kwargs(),
            ty_enum,
            inputs.senior_citizen,
        )
    except Exception as exc:
        logger.exception("engine (no deductions) failed: %s", exc)
        # Non-fatal: surface the headline bill, just no "savings" number.
        comp_without = None

    report.computation_with_deductions = comp_with
    report.computation_without_deductions = comp_without

    report.gross_income_lkr = comp_with.gross_income_lkr
    report.total_deductions_lkr = comp_with.deductions_input_lkr
    report.taxable_income_lkr = comp_with.taxable_income_lkr
    report.gross_tax_payable_lkr = comp_with.gross_tax_lkr
    report.net_tax_payable_lkr = comp_with.net_tax_due_lkr

    if comp_without is not None:
        report.tax_without_deductions_lkr = comp_without.net_tax_due_lkr
        report.savings_vs_no_deductions_lkr = (
            comp_without.net_tax_due_lkr - comp_with.net_tax_due_lkr
        )

    # Audit defensibility scoring.
    # B12 F6.4: replaced old "no-problems = full marks" scorer with
    # evidence-required scorer from fiesta.tax_bill.audit_defensibility.
    score, label, components = _score_audit_defensibility_v2(
        inputs,
        gross_income=report.gross_income_lkr,
        total_deductions=report.total_deductions_lkr,
    )
    report.audit_defensibility_score = score
    report.audit_defensibility_label = label
    report.audit_score_components = components

    # X6 gate input.
    report.gate_customer_data = _build_gate_customer_data(
        inputs,
        gross_income=report.gross_income_lkr,
        total_deductions=report.total_deductions_lkr,
    )

    return report


__all__ = [
    "TaxBillReport",
    "compute_tax_bill",
]
