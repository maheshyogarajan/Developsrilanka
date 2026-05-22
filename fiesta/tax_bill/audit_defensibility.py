"""fiesta.tax_bill.audit_defensibility -- Evidence-required audit defensibility scoring.

B12 F6.4 -- Fix audit-defensibility scoring to require evidence.

Prior behaviour (REPLACED): "no problems detected = full marks".
A zero-data user saw ~95/100 because the old scorer awarded full points
whenever a category had no violations. That logic is inverted: absence of
data is not the same as presence of evidence.

New behaviour: components start at 0 and accrue ONLY when positive evidence
is present. A fully-prepared filer scores 90+. A zero-data user scores low
(<= 15) with an "Insufficient data" message.

Scoring weights (B12 spec):
    profile_complete      : 15  -- NIC + contact info filled in S3
    income_logged         : 25  -- RemittanceEntry rows present + total > 0
    deductions_claimed    : 20  -- at least one SP (ServiceProvider count > 0)
                                   OR home-office rent claimed > 0
    agreements_generated  : 25  -- ServiceAgreementGenerated OR
                                   RentalAgreementGenerated rows present
    attestation_signed    : 15  -- Submission record status == 'attested'
                                   for the current tax year
                          -----
                   TOTAL  : 100

Buckets (unchanged from prior scorer -- template depends on these strings):
    >= 80 -- Strong    (defensible; routine IRD review ok)
    50-79 -- Moderate  (some flags; recommend evidence cleanup pre-submit)
    < 50  -- At-Risk   (block / consultant review)

Empty-state message:
    When the total score < 30 the returned components dict includes a top-level
    "empty_state_message" key that templates can surface as a routing card.

Integration contract:
    - Called from fiesta.tax_bill.compute._score_audit_defensibility_v2()
      which is a thin shim that feeds compute's existing return contract:
      tuple[int, str, dict[str, Any]].
    - The return shape is IDENTICAL to the old scorer so TaxBillReport fields
      (audit_defensibility_score, audit_defensibility_label,
      audit_score_components) and the breakdown JSON route need no changes.
    - The template (templates/tax_bill/index.html) reads only
      report.audit_defensibility_score and report.audit_defensibility_label;
      it will benefit from the corrected score with no template edits needed.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .aggregator import TaxInputs  # pragma: no cover

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Weight constants (B12 spec)
# ---------------------------------------------------------------------------

WEIGHT_PROFILE_COMPLETE: int = 15
WEIGHT_INCOME_LOGGED: int = 25
WEIGHT_DEDUCTIONS_CLAIMED: int = 20
WEIGHT_AGREEMENTS_GENERATED: int = 25
WEIGHT_ATTESTATION_SIGNED: int = 15
TOTAL_WEIGHT: int = 100  # sum of all weights

_EMPTY_STATE_THRESHOLD: int = 30  # scores below this get the routing nudge


# ---------------------------------------------------------------------------
# Per-component scorers
# ---------------------------------------------------------------------------


def _score_profile_complete(inputs: "TaxInputs") -> dict[str, Any]:
    """15 pts if S3 profile is marked complete (NIC + city + employment_type)."""
    if inputs.profile_complete:
        return {
            "score": WEIGHT_PROFILE_COMPLETE,
            "rationale": "S3 profile complete (NIC + contact details filled).",
        }
    return {
        "score": 0,
        "rationale": (
            "S3 profile not yet complete. Fill in your NIC, city, and "
            "employment type to earn these points."
        ),
    }


def _score_income_logged(inputs: "TaxInputs") -> dict[str, Any]:
    """25 pts if at least one RemittanceEntry exists and total_lkr > 0."""
    if inputs.income_entry_count > 0 and inputs.income_total_lkr > Decimal("0"):
        return {
            "score": WEIGHT_INCOME_LOGGED,
            "rationale": (
                f"{inputs.income_entry_count} income "
                f"entr{'y' if inputs.income_entry_count == 1 else 'ies'} "
                f"logged (Rs {inputs.income_total_lkr:,.2f} total)."
            ),
        }
    return {
        "score": 0,
        "rationale": (
            "No income entries logged for this tax year. Visit your "
            "Remittance Ledger to record your foreign income."
        ),
    }


def _score_deductions_claimed(inputs: "TaxInputs") -> dict[str, Any]:
    """20 pts if at least one SP is present OR home-office rent > 0.

    'Claimed' means the customer has taken action to identify deductible
    expenditure — logging a service provider or claiming home-office rent.
    The deductions_itemised list is the source for the home-office rent check;
    service_providers drives the SP count.
    """
    sp_count = len(inputs.service_providers)

    # Check for any home-office rental deduction > 0.
    home_office_rent_lkr = Decimal("0")
    for d in inputs.deductions_itemised:
        if d.get("category_id") == "home_office_rental":
            home_office_rent_lkr += d.get("used_lkr") or Decimal("0")

    if sp_count > 0 or home_office_rent_lkr > 0:
        parts = []
        if sp_count > 0:
            parts.append(
                f"{sp_count} service provider"
                f"{'s' if sp_count > 1 else ''} recorded"
            )
        if home_office_rent_lkr > 0:
            parts.append(
                f"home-office rent Rs {home_office_rent_lkr:,.2f} claimed"
            )
        return {
            "score": WEIGHT_DEDUCTIONS_CLAIMED,
            "rationale": "; ".join(parts) + ".",
        }
    return {
        "score": 0,
        "rationale": (
            "No deductions identified yet. Add a Service Provider (S6) or "
            "claim your home-office rent (S7) to earn these points."
        ),
    }


def _score_agreements_generated(inputs: "TaxInputs") -> dict[str, Any]:
    """25 pts if at least one ServiceAgreementGenerated or RentalAgreementGenerated
    row exists (indicated by agreement_reference_id being present on any SP or
    rental in the inputs snapshot).

    The aggregator already resolves these: ServiceProvider.has_agreement + the
    rental dict agreement_reference_id field capture the generated-agreement
    state without requiring a fresh DB hit here.
    """
    sp_agreements = sum(
        1 for sp in inputs.service_providers
        if sp.get("has_agreement") and sp.get("agreement_reference_id")
    )
    rental_agreements = sum(
        1 for r in inputs.rentals
        if r.get("agreement_reference_id")
    )
    total_agreements = sp_agreements + rental_agreements

    if total_agreements > 0:
        parts = []
        if sp_agreements > 0:
            parts.append(
                f"{sp_agreements} service agreement"
                f"{'s' if sp_agreements > 1 else ''}"
            )
        if rental_agreements > 0:
            parts.append(
                f"{rental_agreements} rental agreement"
                f"{'s' if rental_agreements > 1 else ''}"
            )
        return {
            "score": WEIGHT_AGREEMENTS_GENERATED,
            "rationale": (
                "Generated documents on file: " + ", ".join(parts) + ". "
                "These establish your deduction basis with the IRD."
            ),
        }
    return {
        "score": 0,
        "rationale": (
            "No service or rental agreements generated yet. Generate agreements "
            "from your SP (S8) and property (S9) screens to secure this score."
        ),
    }


def _score_attestation_signed(
    inputs: "TaxInputs",
) -> dict[str, Any]:
    """15 pts if a Submission record in 'attested' or later state exists for
    the current tax year.

    Uses a defensive import of fiesta.submit.models.Submission so this scorer
    does NOT raise in headless/test environments where the DB is unavailable.
    Returns 0 with a clear rationale if the model cannot be queried.
    """
    tax_year_s4 = inputs.tax_year_s4_format

    try:
        from fiesta.submit.models import Submission  # type: ignore[import]
    except Exception:
        # submit module not available (e.g. minimal test environment).
        return {
            "score": 0,
            "rationale": (
                "Attestation status could not be checked "
                "(submit module unavailable)."
            ),
        }

    try:
        row = (
            Submission.query
            .filter_by(user_id=inputs.user_id, tax_year=tax_year_s4)
            .first()
        )
    except Exception as exc:
        logger.warning("attestation lookup failed: %s", exc)
        return {
            "score": 0,
            "rationale": "Attestation status could not be retrieved.",
        }

    attested_statuses = {"attested", "export-generated", "customer-filed-on-ird"}
    if row is not None and row.status in attested_statuses:
        return {
            "score": WEIGHT_ATTESTATION_SIGNED,
            "rationale": (
                f"Tax return attested for {inputs.tax_year_s5_format} "
                f"(status: {row.status})."
            ),
        }
    return {
        "score": 0,
        "rationale": (
            "Attestation not yet signed for this tax year. Complete S14 "
            "(Submit) to earn these points."
        ),
    }


# ---------------------------------------------------------------------------
# Public scoring function
# ---------------------------------------------------------------------------


def score_audit_defensibility(
    inputs: "TaxInputs",
    gross_income: Decimal,
    total_deductions: Decimal,
) -> tuple[int, str, dict[str, Any]]:
    """Compute evidence-required 0-100 audit-defensibility score.

    Each component starts at 0 and accrues only when positive evidence is
    present. A zero-data user scores <= 15 (profile-only if complete). A
    fully-prepared filer scores 90+ (all components except attestation which
    requires S14 completion; a filed return scores 100).

    Args:
        inputs:            TaxInputs snapshot from the aggregator.
        gross_income:      Gross income in LKR (Decimal). Used for context in
                           rationale strings; not a scoring input in B12.
        total_deductions:  Total deductions in LKR (Decimal). Same.

    Returns:
        (score, label, components)
            score:      int 0-100
            label:      "Strong" | "Moderate" | "At-Risk"
            components: dict keyed by component name, each with "score" +
                        "rationale". May also carry "empty_state_message" at
                        top level when score < _EMPTY_STATE_THRESHOLD.
    """
    c_profile = _score_profile_complete(inputs)
    c_income = _score_income_logged(inputs)
    c_deductions = _score_deductions_claimed(inputs)
    c_agreements = _score_agreements_generated(inputs)
    c_attestation = _score_attestation_signed(inputs)

    components: dict[str, Any] = {
        "profile_complete": c_profile,
        "income_logged": c_income,
        "deductions_claimed": c_deductions,
        "agreements_generated": c_agreements,
        "attestation_signed": c_attestation,
    }

    total = (
        c_profile["score"]
        + c_income["score"]
        + c_deductions["score"]
        + c_agreements["score"]
        + c_attestation["score"]
    )
    total = max(0, min(TOTAL_WEIGHT, total))

    # Empty-state nudge: surfaces a routing message when data is sparse.
    if total < _EMPTY_STATE_THRESHOLD:
        components["empty_state_message"] = (
            "Insufficient data — log your income to build defensibility. "
            "Visit your Remittance Ledger to start."
        )

    if total >= 80:
        label = "Strong"
    elif total >= 50:
        label = "Moderate"
    else:
        label = "At-Risk"

    return total, label, components


__all__ = ["score_audit_defensibility"]
