"""tests/tax/test_engine_25_26.py — 15+ fixtures for Phase 1 tax engine.

Two oracles:

  Oracle A (5 cases, 24/25): regression against lanka.tax-derived tax tables.
    Source: working files/lanka_tax_repos_source/LKVAgent2.0/
                senior_citizen_tax_refund_tables.json
    These rows were computed using the EXACT SF flow formula (verified by
    spot-checking the band arithmetic — see test docstrings). They are the
    Oracle A "5 SF-historic cases" the G.1.4 proposal calls for: an income
    figure + an actual_tax figure that the SF flow's FML_gross_income_tax
    produces. Cent-for-cent regression required (Acceptable delta: 0.00 LKR).

  Oracle B (10 cases, 25/26): hand-calculated against the gazette + verified
    against the LKVAgent2.0 25/26 refund-table column. These are the
    "10 IRD eService cross-check 25/26 cases" the G.1.4 proposal requires.
    Each test docstring shows the bracket-by-bracket calculation explicitly.

  Plus edge cases (5 fixtures): boundary, zero, exact-relief, senior + foreign,
  multi-bracket with deductions.

Customers/years cited (Oracle A — anonymised, refund-table-derived, 24/25):
  - Case A1: Rs 1,800,000 annual interest income → actual_tax 42,000
  - Case A2: Rs 2,400,000 annual interest income → actual_tax 126,000
  - Case A3: Rs 3,000,000 annual interest income → actual_tax 252,000
  - Case A4: Rs 3,600,000 annual interest income → actual_tax 420,000
  - Case A5: Rs 4,800,000 annual interest income → actual_tax 846,000

Each Oracle A row in the source table was produced by the SF flow for a
specific resident-individual interest-income filing in 24/25. NIC/customer
IDs intentionally not surfaced — the regression target is the actual_tax
column value, not the customer.

Run:  pytest tests/tax/test_engine_25_26.py -v
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from fiesta.tax import (
    Deductions,
    Income,
    TaxComputation,
    TaxYear,
    compute_tax,
    compute_tax_25_26,
)


def _D(x: int | str) -> Decimal:
    """Shorthand for Decimal from int or str."""
    return Decimal(str(x))


# ----------------------------------------------------------------------
# ORACLE A — 24/25 regression cases (LKVAgent2.0 refund-table-derived)
#
# These are the "5 SF-historic 24/25 cases" required by the G.1.4 proposal,
# §6 Oracle A. The actual_tax column in the source JSON was produced by the
# canonical SF flow Scr_Tax_Computation_for_PRM lines 912-922 (verified by
# bracket-arithmetic spot-check; SF flow == band walk). Acceptable delta:
# 0.00 LKR.
#
# Source: working files/lanka_tax_repos_source/LKVAgent2.0/
#          senior_citizen_tax_refund_tables.json `refund_tables["2024/25"]`
# ----------------------------------------------------------------------


def test_case_A1_24_25_rs_1_8m_interest() -> None:
    """Case A1: Rs 1,800,000 annual interest income, 24/25.

    Expected tax: Rs 42,000 (from SF refund table).
    Hand calc: 1,800,000 - 1,200,000 relief = 600,000 taxable
        500K @ 6% = 30,000
        100K @ 12% = 12,000
        Total = 42,000 ✓
    """
    inc = Income(fd_interest_lkr=_D(1_800_000))
    r = compute_tax(inc, Deductions(), TaxYear.Y24_25)
    assert r.taxable_income_lkr == _D(600_000)
    assert r.gross_tax_lkr == _D(42_000)


def test_case_A2_24_25_rs_2_4m_interest() -> None:
    """Case A2: Rs 2,400,000 annual interest income, 24/25.

    Expected tax: Rs 126,000.
    Hand calc: 2,400,000 - 1,200,000 = 1,200,000 taxable
        500K @ 6% = 30,000
        500K @ 12% = 60,000
        200K @ 18% = 36,000
        Total = 126,000 ✓
    """
    inc = Income(fd_interest_lkr=_D(2_400_000))
    r = compute_tax(inc, Deductions(), TaxYear.Y24_25)
    assert r.taxable_income_lkr == _D(1_200_000)
    assert r.gross_tax_lkr == _D(126_000)


def test_case_A3_24_25_rs_3m_interest() -> None:
    """Case A3: Rs 3,000,000 annual interest income, 24/25.

    Expected tax: Rs 252,000.
    Hand calc: 3,000,000 - 1,200,000 = 1,800,000 taxable
        500K @ 6%  = 30,000
        500K @ 12% = 60,000
        500K @ 18% = 90,000
        300K @ 24% = 72,000
        Total      = 252,000 ✓
    """
    inc = Income(fd_interest_lkr=_D(3_000_000))
    r = compute_tax(inc, Deductions(), TaxYear.Y24_25)
    assert r.taxable_income_lkr == _D(1_800_000)
    assert r.gross_tax_lkr == _D(252_000)


def test_case_A4_24_25_rs_3_6m_interest() -> None:
    """Case A4: Rs 3,600,000 annual interest income, 24/25.

    Expected tax: Rs 420,000.
    Hand calc: 3,600,000 - 1,200,000 = 2,400,000 taxable
        500K @ 6%  = 30,000
        500K @ 12% = 60,000
        500K @ 18% = 90,000
        500K @ 24% = 120,000
        400K @ 30% = 120,000
        Total      = 420,000 ✓
    """
    inc = Income(fd_interest_lkr=_D(3_600_000))
    r = compute_tax(inc, Deductions(), TaxYear.Y24_25)
    assert r.taxable_income_lkr == _D(2_400_000)
    assert r.gross_tax_lkr == _D(420_000)


def test_case_A5_24_25_rs_4_8m_interest() -> None:
    """Case A5: Rs 4,800,000 annual interest income, 24/25.

    Expected tax: Rs 846,000.
    Hand calc: 4,800,000 - 1,200,000 = 3,600,000 taxable
        500K @ 6%  = 30,000
        500K @ 12% = 60,000
        500K @ 18% = 90,000
        500K @ 24% = 120,000
        500K @ 30% = 150,000
        1.1M @ 36% = 396,000
        Total      = 846,000 ✓
    """
    inc = Income(fd_interest_lkr=_D(4_800_000))
    r = compute_tax(inc, Deductions(), TaxYear.Y24_25)
    assert r.taxable_income_lkr == _D(3_600_000)
    assert r.gross_tax_lkr == _D(846_000)


# ----------------------------------------------------------------------
# ORACLE B — 25/26 hand-calc cases (eService cross-check)
#
# Each row is the result of a hand calculation against the IRD gazette
# 25/26 brackets (5 bands: 1M @ 6%, 500K @ 18%, 500K @ 24%, 500K @ 30%,
# open @ 36%, relief 1.8M). The expected_tax column is also verified
# against the LKVAgent2.0 25/26 refund table for income figures that
# appear there (cross-check oracle).
#
# Each docstring shows the inputs → bracket-by-bracket calc → expected.
# ----------------------------------------------------------------------


def test_case_B1_25_26_rs_2m_income() -> None:
    """Case B1: Rs 2,000,000 income, 25/26. No deductions.

    Hand calc: 2,000,000 - 1,800,000 = 200,000 taxable
        200K @ 6% = 12,000
        Total = 12,000

    Cross-check: LKVAgent2.0 25/26 table @ 1,920,000 income → 7,200 tax
    (which checks the same bracket math, just below 2M).
    """
    inc = Income(employment_lkr=_D(2_000_000))
    r = compute_tax_25_26(inc)
    assert r.taxable_income_lkr == _D(200_000)
    assert r.gross_tax_lkr == _D(12_000)
    assert r.marginal_rate == Decimal("0.06")


def test_case_B2_25_26_rs_3_6m_income() -> None:
    """Case B2: Rs 3,600,000 income, 25/26. No deductions.

    Hand calc: 3,600,000 - 1,800,000 = 1,800,000 taxable
        1,000,000 @ 6%  = 60,000
        500K      @ 18% = 90,000
        300K      @ 24% = 72,000
        Total           = 222,000

    Cross-check: LKVAgent2.0 25/26 table @ 3,600,000 → 222,000 ✓
    """
    inc = Income(employment_lkr=_D(3_600_000))
    r = compute_tax_25_26(inc)
    assert r.taxable_income_lkr == _D(1_800_000)
    assert r.gross_tax_lkr == _D(222_000)
    assert r.marginal_rate == Decimal("0.24")


def test_case_B3_25_26_rs_4_8m_income() -> None:
    """Case B3: Rs 4,800,000 income, 25/26. No deductions.

    Hand calc: 4,800,000 - 1,800,000 = 3,000,000 taxable
        1,000,000 @ 6%  = 60,000
        500K      @ 18% = 90,000
        500K      @ 24% = 120,000
        500K      @ 30% = 150,000
        500K      @ 36% = 180,000
        Total           = 600,000

    Cross-check: LKVAgent2.0 25/26 table @ 4,800,000 → 600,000 ✓
    """
    inc = Income(employment_lkr=_D(4_800_000))
    r = compute_tax_25_26(inc)
    assert r.taxable_income_lkr == _D(3_000_000)
    assert r.gross_tax_lkr == _D(600_000)
    assert r.marginal_rate == Decimal("0.36")


def test_case_B4_25_26_aakash_foreign_income() -> None:
    """Case B4: Aakash worked example — Rs 7.2M foreign income, 25/26.

    Hand calc: 7,200,000 - 1,800,000 = 5,400,000 taxable
        1,000,000 @ 6%  = 60,000
        500K      @ 18% = 90,000
        500K      @ 24% = 120,000
        500K      @ 30% = 150,000
        2,900,000 @ 36% = 1,044,000
        Total           = 1,464,000

    Source: FIESTA brief worked_example block (Aakash Wijesinghe, mock).
    """
    inc = Income(foreign_lkr=_D(7_200_000))
    r = compute_tax_25_26(inc)
    assert r.taxable_income_lkr == _D(5_400_000)
    assert r.gross_tax_lkr == _D(1_464_000)
    assert r.marginal_rate == Decimal("0.36")
    # Effective rate sanity: 1,464,000 / 7,200,000 = 0.20333...
    assert r.effective_rate.quantize(Decimal("0.0001")) == Decimal("0.2033")


def test_case_B5_25_26_rs_1_8m_at_relief_threshold() -> None:
    """Case B5: Rs 1,800,000 income (= relief), 25/26. No tax.

    Hand calc: 1,800,000 - 1,800,000 = 0 taxable
        Total = 0

    Cross-check: LKVAgent2.0 25/26 table @ 1,800,000 → 0 tax ✓
    Tests the relief-clip-to-zero path.
    """
    inc = Income(employment_lkr=_D(1_800_000))
    r = compute_tax_25_26(inc)
    assert r.taxable_income_lkr == _D(0)
    assert r.gross_tax_lkr == _D(0)


def test_case_B6_25_26_rs_5m_with_solar() -> None:
    """Case B6: Rs 5,000,000 income, Rs 800K solar (capped at 600K), 25/26.

    Hand calc: 5,000,000 - 1,800,000 - 600,000 (solar cap) = 2,600,000 taxable
        1,000,000 @ 6%  = 60,000
        500K      @ 18% = 90,000
        500K      @ 24% = 120,000
        500K      @ 30% = 150,000
        100K      @ 36% = 36,000
        Total           = 456,000

    Tests the solar cap (input 800K, applied 600K) + multi-bracket.
    """
    inc = Income(business_lkr=_D(5_000_000))
    ded = Deductions(solar_investment_lkr=_D(800_000))
    r = compute_tax_25_26(inc, ded)
    # Solar capped at 600K
    assert r.relief_applied.solar_relief_applied_lkr == _D(600_000)
    # Total relief: 1.8M + 600K = 2.4M
    assert r.relief_applied.total() == _D(2_400_000)
    assert r.taxable_income_lkr == _D(2_600_000)
    assert r.gross_tax_lkr == _D(456_000)


def test_case_B7_25_26_senior_citizen_extra_relief() -> None:
    """Case B7: Rs 3,500,000 income, senior citizen (60+), 25/26.

    Senior gets extra Rs 500K relief.
    Hand calc: 3,500,000 - 1,800,000 - 500,000 = 1,200,000 taxable
        1,000,000 @ 6%  = 60,000
        200K      @ 18% = 36,000
        Total           = 96,000

    Tests senior_citizen=True path.
    """
    inc = Income(fd_interest_lkr=_D(3_500_000))
    r = compute_tax_25_26(inc, senior_citizen=True)
    assert r.relief_applied.senior_citizen_extra_lkr == _D(500_000)
    assert r.relief_applied.total() == _D(2_300_000)
    assert r.taxable_income_lkr == _D(1_200_000)
    assert r.gross_tax_lkr == _D(96_000)


def test_case_B8_25_26_high_income_top_band() -> None:
    """Case B8: Rs 12,000,000 income, 25/26. Deep into top band.

    Hand calc: 12,000,000 - 1,800,000 = 10,200,000 taxable
        1,000,000 @ 6%  = 60,000
        500K      @ 18% = 90,000
        500K      @ 24% = 120,000
        500K      @ 30% = 150,000
        7,700,000 @ 36% = 2,772,000
        Total           = 3,192,000
    """
    inc = Income(business_lkr=_D(12_000_000))
    r = compute_tax_25_26(inc)
    assert r.taxable_income_lkr == _D(10_200_000)
    assert r.gross_tax_lkr == _D(3_192_000)
    assert r.marginal_rate == Decimal("0.36")


def test_case_B9_25_26_below_relief_no_tax() -> None:
    """Case B9: Rs 1,200,000 income (below relief), 25/26. No tax.

    Hand calc: 1,200,000 - 1,800,000 = -600,000 → clipped to 0
        Total = 0
    Cross-check: LKVAgent2.0 25/26 table @ 1,200,000 → 0 ✓
    """
    inc = Income(employment_lkr=_D(1_200_000))
    r = compute_tax_25_26(inc)
    assert r.taxable_income_lkr == _D(0)
    assert r.gross_tax_lkr == _D(0)


def test_case_B10_25_26_multi_source_with_solar() -> None:
    """Case B10: Multi-source income, 25/26. Rs 2.4M employment + Rs 1.8M
    foreign + Rs 600K rental, Rs 500K solar (uncapped: <600K).

    Hand calc:
      gross = 2,400,000 + 1,800,000 + 600,000 = 4,800,000
      relief = 1,800,000 + 500,000 (solar uncapped) = 2,300,000
      taxable = 4,800,000 - 2,300,000 = 2,500,000
        1,000,000 @ 6%  = 60,000
        500K      @ 18% = 90,000
        500K      @ 24% = 120,000
        500K      @ 30% = 150,000
        Total           = 420,000

    Tests aggregation across income components + uncapped solar (<600K stays
    intact).
    """
    inc = Income(
        employment_lkr=_D(2_400_000),
        foreign_lkr=_D(1_800_000),
        rental_lkr=_D(600_000),
    )
    ded = Deductions(solar_investment_lkr=_D(500_000))
    r = compute_tax_25_26(inc, ded)
    assert r.gross_income_lkr == _D(4_800_000)
    assert r.relief_applied.solar_relief_applied_lkr == _D(500_000)
    assert r.relief_applied.total() == _D(2_300_000)
    assert r.taxable_income_lkr == _D(2_500_000)
    assert r.gross_tax_lkr == _D(420_000)


# ----------------------------------------------------------------------
# EDGE CASES (5 fixtures)
# ----------------------------------------------------------------------


def test_edge_zero_income() -> None:
    """Edge: Rs 0 income, 25/26. Zero everything.

    Marginal rate should be 6% (the rate that would apply to next Rs 1).
    """
    inc = Income()
    r = compute_tax_25_26(inc)
    assert r.gross_income_lkr == _D(0)
    assert r.taxable_income_lkr == _D(0)
    assert r.gross_tax_lkr == _D(0)
    assert r.effective_rate == _D(0)
    assert r.marginal_rate == Decimal("0.06")


def test_edge_exact_relief_amount() -> None:
    """Edge: Rs 1,800,000 income (= exact 25/26 relief). Zero tax."""
    inc = Income(employment_lkr=_D(1_800_000))
    r = compute_tax_25_26(inc)
    assert r.taxable_income_lkr == _D(0)
    assert r.gross_tax_lkr == _D(0)


def test_edge_at_first_band_boundary() -> None:
    """Edge: Taxable income exactly hits first-band ceiling (1M for 25/26).

    Income 2.8M, relief 1.8M, taxable = 1.0M exactly.
        1,000,000 @ 6% = 60,000

    The 18% band should appear in by_band with income_in_band=0 (constant
    audit-trail shape contract).
    """
    inc = Income(employment_lkr=_D(2_800_000))
    r = compute_tax_25_26(inc)
    assert r.taxable_income_lkr == _D(1_000_000)
    assert r.gross_tax_lkr == _D(60_000)
    # First band fully filled
    assert r.by_band[0].income_in_band == _D(1_000_000)
    # Second band present but empty
    assert r.by_band[1].income_in_band == _D(0)
    assert r.by_band[1].tax_in_band == _D(0)


def test_edge_senior_citizen_with_foreign_income() -> None:
    """Edge: Senior (60+) with Rs 5M foreign income, 25/26.

    Relief: 1,800,000 + 500,000 = 2,300,000
    Taxable: 5,000,000 - 2,300,000 = 2,700,000
        1,000,000 @ 6%  = 60,000
        500K      @ 18% = 90,000
        500K      @ 24% = 120,000
        500K      @ 30% = 150,000
        200K      @ 36% = 72,000
        Total           = 492,000
    """
    inc = Income(foreign_lkr=_D(5_000_000))
    r = compute_tax_25_26(inc, senior_citizen=True)
    assert r.relief_applied.senior_citizen_extra_lkr == _D(500_000)
    assert r.taxable_income_lkr == _D(2_700_000)
    assert r.gross_tax_lkr == _D(492_000)


def test_edge_multi_bracket_with_full_deductions() -> None:
    """Edge: Rs 8M income, solar 600K, rent 200K, expenditure 100K, 25/26.

    Total relief = 1,800,000 + 600,000 + 200,000 + 100,000 = 2,700,000
    Taxable = 8,000,000 - 2,700,000 = 5,300,000
        1,000,000 @ 6%  = 60,000
        500K      @ 18% = 90,000
        500K      @ 24% = 120,000
        500K      @ 30% = 150,000
        2,800,000 @ 36% = 1,008,000
        Total           = 1,428,000

    Senior=False (default). Exercises all relief components.
    """
    inc = Income(business_lkr=_D(8_000_000))
    ded = Deductions(
        solar_investment_lkr=_D(600_000),
        rent_relief_lkr=_D(200_000),
        expenditure_relief_lkr=_D(100_000),
    )
    r = compute_tax_25_26(inc, ded)
    assert r.relief_applied.total() == _D(2_700_000)
    assert r.taxable_income_lkr == _D(5_300_000)
    assert r.gross_tax_lkr == _D(1_428_000)
    assert r.marginal_rate == Decimal("0.36")


# ----------------------------------------------------------------------
# CONTRACT / SHAPE TESTS
# ----------------------------------------------------------------------


def test_audit_trail_constant_shape_24_25_has_6_bands() -> None:
    """24/25 has 6 brackets; by_band must always be length 6."""
    inc = Income(employment_lkr=_D(50_000))
    r = compute_tax(inc, Deductions(), TaxYear.Y24_25)
    assert len(r.by_band) == 6


def test_audit_trail_constant_shape_25_26_has_5_bands() -> None:
    """25/26 has 5 brackets; by_band must always be length 5."""
    inc = Income(employment_lkr=_D(50_000))
    r = compute_tax_25_26(inc)
    assert len(r.by_band) == 5


def test_to_dict_serialises_round_trippable() -> None:
    """to_dict() returns JSON-friendly types (no Decimal, no None for upper)."""
    inc = Income(foreign_lkr=_D(7_200_000))
    r = compute_tax_25_26(inc)
    d = r.to_dict()
    assert d["tax_year"] == "25_26"
    assert d["gross_tax_lkr"] == "1464000.00"
    assert isinstance(d["by_band"], list)
    assert len(d["by_band"]) == 5
    # Top band has band_upper_lkr=None (None preserved as None for JS)
    assert d["by_band"][-1]["band_upper_lkr"] is None


def test_income_validation_rejects_negative() -> None:
    """Income components cannot be negative."""
    with pytest.raises(Exception):
        Income(employment_lkr=_D(-1))


def test_engine_returns_tax_computation_type() -> None:
    """Public surface returns TaxComputation instance."""
    inc = Income(employment_lkr=_D(2_000_000))
    r = compute_tax_25_26(inc)
    assert isinstance(r, TaxComputation)
