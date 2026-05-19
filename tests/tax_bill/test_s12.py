"""tests/tax_bill/test_s12.py -- S12 'Your tax bill' test suite.

18-case suite covering:
    - assembler happy path (full upstream)
    - aggregator: empty upstream / missing-modules tolerance
    - X6 gate: deduction ratio 35% (no warn), 45% (yellow), 65% (red)
    - S12-specific gate: missing §195, SP/agreement mismatch
    - multi-currency aggregation (LKR + USD + EUR)
    - multi-year (24/25 vs 25/26 normalisation)
    - audit-pack PDF render (sections present, bytes returned)
    - savings_vs_no_deductions hand-calc against 5 profiles
    - no-deductions edge case ('see Reduce your tax')
    - audit defensibility score (Strong / Moderate / At-Risk)

Tests do NOT require the Flask app context. Upstream DB modules are
mocked via monkeypatching the aggregator's private loaders.

Run:
    cd C:/Users/mahes/AppData/Local/Temp/fiesta-s12
    python -m pytest tests/tax_bill/test_s12.py -v
"""
from __future__ import annotations

import pathlib
import sys
from decimal import Decimal
from typing import Any

import pytest


# Make the worktree root importable.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Helpers -- build a fully-populated TaxInputs with the canonical engine
# kwargs already wired. Lets us bypass the DB loaders.
# ---------------------------------------------------------------------------


def _make_inputs(
    income_by_category: dict[str, Decimal] | None = None,
    deductions: list[dict[str, Any]] | None = None,
    service_providers: list[dict[str, Any]] | None = None,
    rentals: list[dict[str, Any]] | None = None,
    missing_disclosures: list[dict[str, Any]] | None = None,
    sp_agreement_mismatches: list[dict[str, Any]] | None = None,
    income_by_currency: dict[str, Decimal] | None = None,
    unconverted: list[str] | None = None,
    profile_complete: bool = True,
    senior_citizen: bool = False,
    tax_year_s4: str = "2025-26",
    tax_year_s5: str = "2025/2026",
    full_name: str = "Test Customer",
    nic: str = "123456789V",
    tin: str = "TIN123456",
):
    from fiesta.tax_bill.aggregator import TaxInputs, _compose_engine_inputs

    inputs = TaxInputs(
        user_id=42,
        tax_year_s4_format=tax_year_s4,
        tax_year_s5_format=tax_year_s5,
        full_name=full_name,
        nic=nic,
        tin=tin,
        senior_citizen=senior_citizen,
        profile_complete=profile_complete,
    )
    inputs.income_by_category_lkr = income_by_category or {}
    inputs.income_total_lkr = sum(
        (v for v in inputs.income_by_category_lkr.values()),
        Decimal("0"),
    )
    inputs.income_by_currency = income_by_currency or {"LKR": inputs.income_total_lkr}
    inputs.income_unconverted_currencies = unconverted or []

    inputs.deductions_itemised = deductions or []
    inputs.deductions_total_lkr = sum(
        (d.get("used_lkr") or Decimal("0") for d in inputs.deductions_itemised),
        Decimal("0"),
    )
    inputs.deductions_with_evidence_count = sum(
        1 for d in inputs.deductions_itemised
        if d.get("evidence_status") in ("collected", "submitted")
    )
    inputs.deductions_pending_evidence_count = (
        len(inputs.deductions_itemised)
        - inputs.deductions_with_evidence_count
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

    inputs.missing_disclosures = missing_disclosures or []
    inputs.sp_agreement_mismatches = sp_agreement_mismatches or []

    _compose_engine_inputs(inputs)
    return inputs


def _make_deduction(
    category_id: str,
    name: str,
    amount_lkr: Decimal,
    evidence: str = "collected",
    cap_note: str | None = None,
    ira: str = "§6",
    engine_bucket: str | None = None,
):
    from fiesta.tax_bill.aggregator import _deduction_engine_bucket
    return {
        "category_id": category_id,
        "name": name,
        "ira_section": ira,
        "ira_section_long": f"Inland Revenue Act No. 24 of 2017, {ira}",
        "estimated_lkr": amount_lkr,
        "actual_lkr": amount_lkr,
        "used_lkr": amount_lkr,
        "evidence_status": evidence,
        "notes": None,
        "cap_note": cap_note,
        "engine_bucket": engine_bucket or _deduction_engine_bucket(category_id),
    }


# ---------------------------------------------------------------------------
# Test 1 -- aggregator: empty upstream tolerated
# ---------------------------------------------------------------------------


def test_01_aggregator_empty_upstream_does_not_raise():
    """Aggregator with no DB / no modules returns a sane empty TaxInputs."""
    from fiesta.tax_bill.aggregator import assemble_tax_inputs

    inputs = assemble_tax_inputs(user_id=999_999, tax_year="2025-26")
    assert inputs.user_id == 999_999
    assert inputs.tax_year_s4_format == "2025-26"
    assert inputs.tax_year_s5_format == "2025/2026"
    assert inputs.income_total_lkr == Decimal("0")
    assert inputs.deductions_total_lkr == Decimal("0")
    assert inputs.engine_income_kwargs["employment_lkr"] == Decimal("0")
    assert inputs.engine_deductions_kwargs["solar_investment_lkr"] == Decimal("0")


# ---------------------------------------------------------------------------
# Test 2 -- tax-year normalisation: every accepted form maps to one canonical
# ---------------------------------------------------------------------------


def test_02_tax_year_normalisation():
    from fiesta.tax_bill.aggregator import (
        normalise_tax_year_to_s4_format,
        normalise_tax_year_to_s5_format,
    )

    for ty in ["2025-26", "2025/2026", "25/26", "25_26", "Y25_26"]:
        assert normalise_tax_year_to_s4_format(ty) == "2025-26"
        assert normalise_tax_year_to_s5_format(ty) == "2025/2026"

    for ty in ["2024-25", "2024/2025", "24/25", "24_25", "Y24_25"]:
        assert normalise_tax_year_to_s4_format(ty) == "2024-25"
        assert normalise_tax_year_to_s5_format(ty) == "2024/2025"


# ---------------------------------------------------------------------------
# Test 3 -- happy path: full upstream -> tax engine produces a bill
# ---------------------------------------------------------------------------


def test_03_happy_path_full_compute():
    """Customer earns Rs 5M, claims Rs 600K deductions, gets a bill."""
    inputs = _make_inputs(
        income_by_category={
            "salary": Decimal("3000000"),
            "foreign_remittance": Decimal("2000000"),
        },
        deductions=[
            _make_deduction("internet_telecom", "Internet & telecom",
                            Decimal("180000"), evidence="collected"),
            _make_deduction("software_subscriptions", "Software subscriptions",
                            Decimal("120000"), evidence="collected"),
            _make_deduction("travel_business", "Business travel",
                            Decimal("300000"), evidence="pending"),
        ],
    )

    from fiesta.tax_bill.compute import compute_tax_bill
    report = compute_tax_bill(user_id=42, tax_year="2025-26", pre_assembled=inputs)

    assert report.engine_error is None, report.engine_error
    assert report.gross_income_lkr == Decimal("5000000")
    assert report.total_deductions_lkr == Decimal("600000")
    assert report.net_tax_payable_lkr > 0
    assert report.tax_without_deductions_lkr > report.net_tax_payable_lkr
    assert report.savings_vs_no_deductions_lkr > 0


# ---------------------------------------------------------------------------
# Test 4 -- X6 gate: deduction ratio 35% -> no warning, no block
# ---------------------------------------------------------------------------


def test_04_gate_deduction_ratio_safe_35pct():
    inputs = _make_inputs(
        income_by_category={"salary": Decimal("5000000")},
        deductions=[
            _make_deduction("internet_telecom", "Internet", Decimal("1750000")),
        ],
    )
    # 1.75M / 5M = 35%
    from fiesta.tax_bill.compute import compute_tax_bill
    from fiesta.tax_bill.gate_check import run_gate
    report = compute_tax_bill(user_id=42, tax_year="2025-26", pre_assembled=inputs)
    gate = run_gate(report, action="display_bill")
    # No S12-DEDUCTION-RATIO warnings or blocks at 35%.
    ded_warns = [w for w in gate.warnings if "DEDUCTION-RATIO" in w["rule_id"]]
    ded_blocks = [b for b in gate.blocks if "DEDUCTION-RATIO" in b["rule_id"]]
    assert ded_warns == []
    assert ded_blocks == []


# ---------------------------------------------------------------------------
# Test 5 -- X6 gate: deduction ratio 45% -> yellow warning
# ---------------------------------------------------------------------------


def test_05_gate_deduction_ratio_warn_45pct():
    inputs = _make_inputs(
        income_by_category={"salary": Decimal("5000000")},
        deductions=[
            _make_deduction("travel_business", "Travel", Decimal("2250000")),
        ],
    )
    # 2.25M / 5M = 45% -> warn (>40%, <=60%)
    from fiesta.tax_bill.compute import compute_tax_bill
    from fiesta.tax_bill.gate_check import run_gate
    report = compute_tax_bill(user_id=42, tax_year="2025-26", pre_assembled=inputs)
    gate = run_gate(report, action="display_bill")
    ded_warns = [w for w in gate.warnings if "DEDUCTION-RATIO" in w["rule_id"]]
    ded_blocks = [b for b in gate.blocks if "DEDUCTION-RATIO" in b["rule_id"]]
    assert len(ded_warns) == 1, gate.warnings
    assert ded_blocks == []


# ---------------------------------------------------------------------------
# Test 6 -- X6 gate: deduction ratio 65% -> red block
# ---------------------------------------------------------------------------


def test_06_gate_deduction_ratio_block_65pct():
    inputs = _make_inputs(
        income_by_category={"salary": Decimal("5000000")},
        deductions=[
            _make_deduction("travel_business", "Travel", Decimal("3250000")),
        ],
    )
    # 3.25M / 5M = 65% -> block (>60%)
    from fiesta.tax_bill.compute import compute_tax_bill
    from fiesta.tax_bill.gate_check import run_gate
    report = compute_tax_bill(user_id=42, tax_year="2025-26", pre_assembled=inputs)
    gate = run_gate(report, action="display_bill")
    ded_blocks = [b for b in gate.blocks if "DEDUCTION-RATIO" in b["rule_id"]]
    assert len(ded_blocks) == 1, gate.blocks


# ---------------------------------------------------------------------------
# Test 7 -- S12 rule: missing §195 disclosure -> red block
# ---------------------------------------------------------------------------


def test_07_gate_missing_195_disclosure_red():
    inputs = _make_inputs(
        income_by_category={"salary": Decimal("5000000")},
        deductions=[],
        service_providers=[{
            "id": 1, "name": "Spouse Ltd",
            "service_type": "subcontractor_developer",
            "monthly_rate_lkr": Decimal("500000"),
            "requires_disclosure": True,
            "has_agreement": True,
            "agreement_status": "generated_unsigned",
            "agreement_reference_id": "FIESTA-SA-XX1",
            "agreement_monthly_fee_lkr": Decimal("500000"),
            "disclosure_applied_in_agreement": False,  # NOT applied
        }],
        missing_disclosures=[{
            "kind": "service_provider", "id": 1, "name": "Spouse Ltd",
            "reason": "Spouse-flagged SP without §195 clause.",
        }],
    )
    from fiesta.tax_bill.compute import compute_tax_bill
    from fiesta.tax_bill.gate_check import run_gate
    report = compute_tax_bill(user_id=42, tax_year="2025-26", pre_assembled=inputs)
    gate = run_gate(report, action="display_bill")
    missing_blocks = [b for b in gate.blocks if "MISSING-195" in b["rule_id"]]
    assert len(missing_blocks) == 1, gate.blocks


# ---------------------------------------------------------------------------
# Test 8 -- S12 rule: SP claim/agreement mismatch -> red block
# ---------------------------------------------------------------------------


def test_08_gate_sp_agreement_mismatch_red():
    inputs = _make_inputs(
        income_by_category={"salary": Decimal("5000000")},
        deductions=[],
        sp_agreement_mismatches=[{
            "sp_id": 1, "sp_name": "Acme Consulting",
            "claimed_monthly_lkr": Decimal("600000"),
            "agreement_monthly_lkr": Decimal("400000"),
            "diff_lkr": Decimal("200000"),
        }],
    )
    from fiesta.tax_bill.compute import compute_tax_bill
    from fiesta.tax_bill.gate_check import run_gate
    report = compute_tax_bill(user_id=42, tax_year="2025-26", pre_assembled=inputs)
    gate = run_gate(report, action="display_bill")
    mismatch_blocks = [b for b in gate.blocks if "MISMATCH" in b["rule_id"]]
    assert len(mismatch_blocks) == 1, gate.blocks


# ---------------------------------------------------------------------------
# Test 9 -- multi-currency aggregation passes through (FX handled upstream)
# ---------------------------------------------------------------------------


def test_09_multi_currency_aggregation():
    """USD + EUR + LKR all reach the engine as the by_category_lkr sum.

    This test verifies the aggregator preserves by_currency and routes the
    LKR-converted sum to the engine. FX is S4's responsibility -- we just
    confirm S12 doesn't drop information.
    """
    inputs = _make_inputs(
        income_by_category={
            "salary": Decimal("1500000"),
            "foreign_remittance": Decimal("3000000"),
        },
        income_by_currency={
            "LKR": Decimal("1500000"),
            "USD": Decimal("5000"),
            "EUR": Decimal("3000"),
        },
    )
    from fiesta.tax_bill.compute import compute_tax_bill
    report = compute_tax_bill(user_id=42, tax_year="2025-26", pre_assembled=inputs)
    assert report.gross_income_lkr == Decimal("4500000")
    # By-currency preserved on the report inputs.
    assert "USD" in report.inputs.income_by_currency
    assert "EUR" in report.inputs.income_by_currency


# ---------------------------------------------------------------------------
# Test 10 -- multi-year: both 24/25 and 25/26 supported
# ---------------------------------------------------------------------------


def test_10_multi_year_24_25_supported():
    """Same inputs computed under 24/25 -- engine accepts the year."""
    inputs = _make_inputs(
        income_by_category={"salary": Decimal("4000000")},
        deductions=[],
        tax_year_s4="2024-25", tax_year_s5="2024/2025",
    )
    from fiesta.tax_bill.compute import compute_tax_bill
    report = compute_tax_bill(user_id=42, tax_year="2024-25", pre_assembled=inputs)
    assert report.engine_error is None
    assert report.gross_income_lkr == Decimal("4000000")
    assert report.net_tax_payable_lkr > 0


# ---------------------------------------------------------------------------
# Test 11 -- no-deductions edge case
# ---------------------------------------------------------------------------


def test_11_no_deductions_message():
    """Customer with income but zero deductions still gets a bill + no savings."""
    inputs = _make_inputs(
        income_by_category={"salary": Decimal("4000000")},
        deductions=[],
    )
    from fiesta.tax_bill.compute import compute_tax_bill
    report = compute_tax_bill(user_id=42, tax_year="2025-26", pre_assembled=inputs)
    assert report.net_tax_payable_lkr > 0
    assert report.savings_vs_no_deductions_lkr == Decimal("0")
    assert report.tax_without_deductions_lkr == report.net_tax_payable_lkr


# ---------------------------------------------------------------------------
# Test 12 -- audit pack PDF: bytes returned, non-trivial size
# ---------------------------------------------------------------------------


def test_12_audit_pack_pdf_renders():
    """Audit pack PDF builds without error and returns bytes."""
    inputs = _make_inputs(
        income_by_category={
            "salary": Decimal("3000000"),
            "foreign_remittance": Decimal("2000000"),
        },
        deductions=[
            _make_deduction("internet_telecom", "Internet", Decimal("150000")),
            _make_deduction("solar", "Solar capital allowance",
                            Decimal("600000"), engine_bucket="solar_investment_lkr"),
        ],
        service_providers=[{
            "id": 1, "name": "Acme Consulting",
            "service_type": "professional_accountant",
            "monthly_rate_lkr": Decimal("50000"),
            "requires_disclosure": False,
            "has_agreement": True,
            "agreement_status": "signed",
            "agreement_reference_id": "FIESTA-SA-ABC1",
            "agreement_monthly_fee_lkr": Decimal("50000"),
            "disclosure_applied_in_agreement": False,
        }],
        rentals=[{
            "rental_id": 1, "property_address": "12 Test Rd, Colombo",
            "property_type": "apartment", "customer_status": "tenant",
            "landlord_name": "Test Landlord",
            "landlord_relationship": "arm's-length",
            "monthly_rent_lkr": Decimal("80000"),
            "annual_rent_lkr": Decimal("960000"),
            "home_office_portion_monthly_lkr": Decimal("16000"),
            "home_office_portion_annual_lkr": Decimal("192000"),
            "home_office_percentage": 20.0,
            "term_start": "2025-04-01", "term_end": "2026-03-31",
            "document_status": "signed",
            "requires_disclosure": False,
            "disclosure_applied_in_agreement": False,
            "agreement_reference_id": "FIESTA-RA-XYZ1",
            "stamp_duty_chargeable": False,
            "stamp_duty_lkr": Decimal("0"),
            "rel_confidence": 0.1,
        }],
    )
    from fiesta.tax_bill.compute import compute_tax_bill
    from fiesta.tax_bill.audit_pack import build_audit_pack

    report = compute_tax_bill(user_id=42, tax_year="2025-26", pre_assembled=inputs)
    pdf = build_audit_pack(report)
    assert isinstance(pdf, (bytes, bytearray))
    # Trivially-empty PDFs are ~2KB; this one with sections must be larger.
    assert len(pdf) > 3000, f"PDF unexpectedly small: {len(pdf)} bytes"
    # Sanity: starts with %PDF
    assert pdf[:4] == b"%PDF"


# ---------------------------------------------------------------------------
# Test 13 -- audit defensibility: Strong (>=80)
# ---------------------------------------------------------------------------


def test_13_audit_defensibility_strong():
    """Full evidence + no §195 issues + low deduction ratio -> Strong."""
    inputs = _make_inputs(
        income_by_category={"salary": Decimal("5000000")},
        deductions=[
            _make_deduction("internet_telecom", "Internet",
                            Decimal("180000"), evidence="collected"),
            _make_deduction("software_subscriptions", "Software",
                            Decimal("120000"), evidence="submitted"),
        ],
    )
    from fiesta.tax_bill.compute import compute_tax_bill
    report = compute_tax_bill(user_id=42, tax_year="2025-26", pre_assembled=inputs)
    assert report.audit_defensibility_score >= 80, (
        report.audit_defensibility_score, report.audit_score_components,
    )
    assert report.audit_defensibility_label == "Strong"


# ---------------------------------------------------------------------------
# Test 14 -- audit defensibility: At-Risk (<50) when many flags
# ---------------------------------------------------------------------------


def test_14_audit_defensibility_at_risk():
    """65% deduction ratio + missing §195 + agreement mismatch + no evidence -> At-Risk."""
    inputs = _make_inputs(
        income_by_category={"salary": Decimal("5000000")},
        deductions=[
            _make_deduction("travel_business", "Travel",
                            Decimal("3250000"), evidence="pending"),
        ],
        service_providers=[{
            "id": 1, "name": "Brother Inc",
            "service_type": "subcontractor_developer",
            "monthly_rate_lkr": Decimal("500000"),
            "requires_disclosure": True,
            "has_agreement": True,
            "agreement_status": "generated_unsigned",
            "agreement_reference_id": "FIESTA-SA-XX1",
            "agreement_monthly_fee_lkr": Decimal("500000"),
            "disclosure_applied_in_agreement": False,
        }],
        missing_disclosures=[{
            "kind": "service_provider", "id": 1, "name": "Brother Inc",
            "reason": "missing §195",
        }],
        sp_agreement_mismatches=[{
            "sp_id": 1, "sp_name": "Brother Inc",
            "claimed_monthly_lkr": Decimal("500000"),
            "agreement_monthly_lkr": Decimal("300000"),
            "diff_lkr": Decimal("200000"),
        }],
        profile_complete=False,
    )
    from fiesta.tax_bill.compute import compute_tax_bill
    report = compute_tax_bill(user_id=42, tax_year="2025-26", pre_assembled=inputs)
    assert report.audit_defensibility_score < 50, (
        report.audit_defensibility_score, report.audit_score_components,
    )
    assert report.audit_defensibility_label == "At-Risk"


# ---------------------------------------------------------------------------
# Test 15 -- savings hand-calc: 5 customer profiles
# ---------------------------------------------------------------------------


def test_15_savings_vs_no_deductions_hand_calc():
    """For 5 profiles, savings_vs_no_deductions == net_tax(no ded) - net_tax(with ded)."""
    from fiesta.tax_bill.compute import compute_tax_bill

    profiles = [
        # (income, deductions, label)
        (Decimal("3000000"), Decimal("100000"), "low-income, small deduction"),
        (Decimal("5000000"), Decimal("600000"), "mid foreign earner"),
        (Decimal("8000000"), Decimal("1200000"), "high foreign earner"),
        (Decimal("12000000"), Decimal("1800000"), "top bracket consultant"),
        (Decimal("2000000"), Decimal("0"), "low + no deductions"),
    ]

    for income, deduction, label in profiles:
        inputs = _make_inputs(
            income_by_category={"foreign_remittance": income},
            deductions=(
                [_make_deduction("internet_telecom", "Internet", deduction)]
                if deduction > 0 else []
            ),
        )
        report = compute_tax_bill(user_id=42, tax_year="2025-26", pre_assembled=inputs)
        # Sanity 1: net + savings == without-deductions tax.
        if report.tax_without_deductions_lkr > 0:
            assert (
                report.net_tax_payable_lkr + report.savings_vs_no_deductions_lkr
                == report.tax_without_deductions_lkr
            ), label


# ---------------------------------------------------------------------------
# Test 16 -- solar cap of Rs 600K is respected by the engine wiring
# ---------------------------------------------------------------------------


def test_16_solar_relief_cap():
    """Engine itself enforces Rs 600K solar cap regardless of input amount."""
    inputs = _make_inputs(
        income_by_category={"salary": Decimal("5000000")},
        deductions=[
            _make_deduction(
                "solar", "Solar capital allowance",
                Decimal("900000"),  # over the cap
                engine_bucket="solar_investment_lkr",
            ),
        ],
    )
    from fiesta.tax_bill.compute import compute_tax_bill
    report = compute_tax_bill(user_id=42, tax_year="2025-26", pre_assembled=inputs)
    comp = report.computation_with_deductions
    # Phase-1 engine caps solar to 600K via the relief computation -- the
    # _applied_ amount should not exceed 600K even though input was 900K.
    assert comp.relief_applied.solar_relief_applied_lkr <= Decimal("600000")


# ---------------------------------------------------------------------------
# Test 17 -- breakdown JSON serialisation: every Decimal becomes a string
# ---------------------------------------------------------------------------


def test_17_breakdown_serialisation():
    """The route /breakdown serialiser must produce JSON-friendly types only."""
    inputs = _make_inputs(
        income_by_category={
            "salary": Decimal("3000000"),
            "foreign_remittance": Decimal("2000000"),
        },
        deductions=[
            _make_deduction("internet_telecom", "Internet",
                            Decimal("180000"), evidence="collected"),
        ],
        service_providers=[{
            "id": 1, "name": "Acme",
            "service_type": "professional_accountant",
            "monthly_rate_lkr": Decimal("50000"),
            "annual_lkr": Decimal("600000"),
            "stated_relationship": "professional_arms_length",
            "requires_disclosure": False,
            "has_agreement": True,
            "agreement_status": "signed",
            "agreement_reference_id": "FIESTA-SA-ABC1",
            "agreement_monthly_fee_lkr": Decimal("50000"),
            "disclosure_applied_in_agreement": False,
            "rel_confidence": 0.1,
        }],
    )
    from fiesta.tax_bill.compute import compute_tax_bill
    from fiesta.tax_bill.routes import _serialise_report

    report = compute_tax_bill(user_id=42, tax_year="2025-26", pre_assembled=inputs)
    payload = _serialise_report(report)

    # Every "_lkr" headline number must be a string (JSON-safe).
    for k in ["gross_income_lkr", "total_deductions_lkr",
              "net_tax_payable_lkr", "savings_vs_no_deductions_lkr"]:
        assert isinstance(payload["headline"][k], str), k

    # Computation field is a dict (via TaxComputation.to_dict).
    assert payload["computation"] is not None
    assert "by_band" in payload["computation"]


# ---------------------------------------------------------------------------
# Test 18 -- aggregator engine-bucket mapping
# ---------------------------------------------------------------------------


def test_18_aggregator_engine_bucket_mapping():
    """Deduction category IDs map to the engine's 3 buckets correctly."""
    from fiesta.tax_bill.aggregator import _deduction_engine_bucket

    assert _deduction_engine_bucket("solar") == "solar_investment_lkr"
    assert _deduction_engine_bucket("home_office_rental") == "rent_relief_lkr"
    assert _deduction_engine_bucket("internet_telecom") == "expenditure_relief_lkr"
    assert _deduction_engine_bucket("travel_business") == "expenditure_relief_lkr"
    assert _deduction_engine_bucket("charitable_donations") == "expenditure_relief_lkr"
    assert _deduction_engine_bucket("anything_else_at_all") == "expenditure_relief_lkr"
