"""tests/tax_bill/test_audit_defensibility_b12.py
B12 F6.4 -- Evidence-required audit-defensibility scoring tests.

Three canonical cases:
    1. Zero-data user  -> low score + "Insufficient data" message
    2. Fully-prepared  -> score >= 90 / label Strong
    3. Partial         -> mid score (profile + income; no agreements or attestation)

Framework: pytest (same as the rest of tests/tax_bill/).

Run:
    cd C:/Users/mahes/fiesta_phase_a/Developsrilanka
    python -m pytest tests/tax_bill/test_audit_defensibility_b12.py -v
"""
from __future__ import annotations

import pathlib
import sys
from decimal import Decimal

import pytest

# Ensure repo root is importable regardless of where pytest is invoked from.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Helpers -- build minimal TaxInputs without touching the DB.
# Mirrors the pattern established in tests/tax_bill/test_s12.py (_make_inputs).
# ---------------------------------------------------------------------------


def _make_inputs(
    profile_complete: bool = False,
    income_entry_count: int = 0,
    income_total_lkr: Decimal = Decimal("0"),
    deductions_itemised=None,
    service_providers=None,
    rentals=None,
    user_id: int = 42,
    tax_year_s4: str = "2025-26",
    tax_year_s5: str = "2025/2026",
):
    from fiesta.tax_bill.aggregator import TaxInputs

    inputs = TaxInputs(
        user_id=user_id,
        tax_year_s4_format=tax_year_s4,
        tax_year_s5_format=tax_year_s5,
        profile_complete=profile_complete,
    )
    inputs.income_entry_count = income_entry_count
    inputs.income_total_lkr = income_total_lkr

    # Populate income_by_category so deductions_claimed helper can see home_office_rental.
    if income_total_lkr > 0:
        inputs.income_by_category_lkr = {"foreign_remittance": income_total_lkr}

    inputs.deductions_itemised = deductions_itemised or []
    inputs.deductions_total_lkr = sum(
        d.get("used_lkr", Decimal("0")) for d in inputs.deductions_itemised
    )
    inputs.deductions_with_evidence_count = sum(
        1 for d in inputs.deductions_itemised
        if d.get("evidence_status") in ("collected", "submitted")
    )
    inputs.deductions_pending_evidence_count = (
        len(inputs.deductions_itemised) - inputs.deductions_with_evidence_count
    )

    inputs.service_providers = service_providers or []
    inputs.sp_disclosure_required_count = sum(
        1 for sp in inputs.service_providers if sp.get("requires_disclosure")
    )
    inputs.sp_disclosure_applied_count = sum(
        1 for sp in inputs.service_providers
        if sp.get("requires_disclosure") and sp.get("disclosure_applied_in_agreement")
    )

    inputs.rentals = rentals or []
    inputs.rental_disclosure_required_count = sum(
        1 for r in inputs.rentals if r.get("requires_disclosure")
    )
    inputs.rental_disclosure_applied_count = sum(
        1 for r in inputs.rentals
        if r.get("requires_disclosure") and r.get("disclosure_applied_in_agreement")
    )

    # Build engine kwargs (required by compute_tax_bill even for pre_assembled).
    try:
        from fiesta.tax_bill.aggregator import _compose_engine_inputs
        _compose_engine_inputs(inputs)
    except Exception:
        pass

    return inputs


# ---------------------------------------------------------------------------
# Case 1 -- Zero-data user: low score + Insufficient-data message
# ---------------------------------------------------------------------------


def test_b12_case1_zero_data_low_score():
    """A user with no data at all must score below 30 with an Insufficient-data message."""
    from fiesta.tax_bill.audit_defensibility import score_audit_defensibility

    inputs = _make_inputs(
        profile_complete=False,
        income_entry_count=0,
        income_total_lkr=Decimal("0"),
        service_providers=[],
        rentals=[],
    )

    score, label, components = score_audit_defensibility(
        inputs,
        gross_income=Decimal("0"),
        total_deductions=Decimal("0"),
    )

    # Score must be low.
    assert score < 30, (
        f"Expected score < 30 for zero-data user, got {score}. "
        f"Components: {components}"
    )
    assert label == "At-Risk", f"Expected 'At-Risk', got '{label}'"

    # Routing nudge must be present.
    assert "empty_state_message" in components, (
        "Expected 'empty_state_message' key in components for low-score user."
    )
    assert "Insufficient data" in components["empty_state_message"], (
        f"Message did not contain 'Insufficient data': {components['empty_state_message']}"
    )

    # All component scores must be 0.
    for key in ("profile_complete", "income_logged", "deductions_claimed",
                "agreements_generated", "attestation_signed"):
        assert components[key]["score"] == 0, (
            f"Expected 0 for component '{key}', got {components[key]['score']}"
        )


# ---------------------------------------------------------------------------
# Case 2 -- Fully-prepared filer: score >= 90 / Strong
#
# Profile complete + income logged + SPs with agreements + home-office rent.
# Attestation is intentionally left out (requires live DB + Submission model)
# so we verify the max achievable without attestation is 85 (i.e. >= 85 < 90
# without attestation, but >=90 with it). This test mocks the attestation
# component to simulate a signed attestation.
# ---------------------------------------------------------------------------


def test_b12_case2_fully_prepared_high_score():
    """Fully-prepared filer (profile + income + deductions + agreements + attestation)
    must score >= 90 / Strong."""
    from fiesta.tax_bill.audit_defensibility import (
        score_audit_defensibility,
        _score_attestation_signed,
    )
    from unittest.mock import patch

    inputs = _make_inputs(
        profile_complete=True,
        income_entry_count=8,
        income_total_lkr=Decimal("5000000"),
        deductions_itemised=[
            {
                "category_id": "home_office_rental",
                "name": "Home office rent",
                "used_lkr": Decimal("192000"),
                "evidence_status": "collected",
            },
            {
                "category_id": "internet_telecom",
                "name": "Internet",
                "used_lkr": Decimal("180000"),
                "evidence_status": "submitted",
            },
        ],
        service_providers=[
            {
                "id": 1,
                "name": "Acme Consulting",
                "service_type": "professional_accountant",
                "monthly_rate_lkr": Decimal("50000"),
                "requires_disclosure": False,
                "has_agreement": True,
                "agreement_status": "signed",
                "agreement_reference_id": "FIESTA-SA-ABC1",
                "agreement_monthly_fee_lkr": Decimal("50000"),
                "disclosure_applied_in_agreement": False,
            }
        ],
        rentals=[
            {
                "rental_id": 1,
                "property_address": "12 Test Rd, Colombo",
                "landlord_name": "Test Landlord",
                "landlord_relationship": "arm's-length",
                "monthly_rent_lkr": Decimal("80000"),
                "home_office_portion_monthly_lkr": Decimal("16000"),
                "requires_disclosure": False,
                "agreement_reference_id": "FIESTA-RA-XYZ1",
                "stamp_duty_chargeable": False,
                "stamp_duty_lkr": Decimal("0"),
            }
        ],
    )

    # Mock attestation component to return 15 pts (simulate a signed attestation
    # without requiring a live DB + Submission row).
    attested_component = {
        "score": 15,
        "rationale": "Tax return attested for 2025/2026 (status: attested). [mock]",
    }

    with patch(
        "fiesta.tax_bill.audit_defensibility._score_attestation_signed",
        return_value=attested_component,
    ):
        score, label, components = score_audit_defensibility(
            inputs,
            gross_income=Decimal("5000000"),
            total_deductions=Decimal("372000"),
        )

    assert score >= 90, (
        f"Expected score >= 90 for fully-prepared filer, got {score}. "
        f"Components: {components}"
    )
    assert label == "Strong", f"Expected 'Strong', got '{label}'"
    assert "empty_state_message" not in components, (
        "Fully-prepared filer should not receive an empty-state message."
    )

    # Verify individual component scores.
    assert components["profile_complete"]["score"] == 15
    assert components["income_logged"]["score"] == 25
    assert components["deductions_claimed"]["score"] == 20
    assert components["agreements_generated"]["score"] == 25
    assert components["attestation_signed"]["score"] == 15


# ---------------------------------------------------------------------------
# Case 3 -- Partial: profile complete + income, but no deductions or agreements
# -> mid score (15 + 25 = 40, "At-Risk")
# ---------------------------------------------------------------------------


def test_b12_case3_partial_profile_and_income_mid_score():
    """User with profile + income but no SPs, no agreements, no attestation.
    Scores 40 (15 + 25 = 40) which is 'At-Risk' (< 50).
    No empty_state_message because score >= 30.
    """
    from fiesta.tax_bill.audit_defensibility import score_audit_defensibility

    inputs = _make_inputs(
        profile_complete=True,
        income_entry_count=3,
        income_total_lkr=Decimal("3000000"),
        deductions_itemised=[],
        service_providers=[],
        rentals=[],
    )

    score, label, components = score_audit_defensibility(
        inputs,
        gross_income=Decimal("3000000"),
        total_deductions=Decimal("0"),
    )

    # Profile (15) + income (25) = 40; deductions, agreements, attestation = 0.
    assert score == 40, (
        f"Expected score 40 (profile=15 + income=25), got {score}. "
        f"Components: {components}"
    )
    # 40 < 50 -> At-Risk (not Moderate).
    assert label == "At-Risk", f"Expected 'At-Risk', got '{label}'"

    # No empty-state message (40 >= 30 threshold).
    assert "empty_state_message" not in components, (
        "Score 40 >= 30, so empty_state_message should not appear."
    )

    # Spot-check components.
    assert components["profile_complete"]["score"] == 15
    assert components["income_logged"]["score"] == 25
    assert components["deductions_claimed"]["score"] == 0
    assert components["agreements_generated"]["score"] == 0
    assert components["attestation_signed"]["score"] == 0
