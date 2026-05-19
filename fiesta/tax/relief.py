"""fiesta.tax.relief — relief computation + application.

Applies gazette relief amounts to gross assessable income, returning the
taxable income that feeds the bracket walker. Mirrors SF flow line 1117:

    FMLtaxableIncome = MAX(0, assessable_income - sum_of_reliefs)

Reliefs applied (in order; sum is commutative but order matters for audit):
  1. Personal relief (year-pinned: 1.2M for 24/25, 1.8M for 25/26)
  2. Senior-citizen extra relief (25/26 onwards: Rs 500K if age >= 60)
  3. Solar investment deduction (capped at 600K per gazette)
  4. Rent relief (pass-through; engine doesn't compute the rent-relief cap)
  5. Expenditure relief (pass-through)

Pure function. No I/O.
"""

from __future__ import annotations

from decimal import Decimal

from .types import Deductions, Income, Reliefs, TaxYearSlabs

# Solar deduction cap (LKR). Matches SF flow line 1111:
#     FMLsolarDeduction = IF(Solar_Investment_Deduction_Amount__c > 600000,
#                            600000, Solar_Investment_Deduction_Amount__c)
SOLAR_RELIEF_CAP_LKR: Decimal = Decimal("600000")


def compute_solar_relief(solar_investment_lkr: Decimal) -> Decimal:
    """Apply the 600K cap to solar investment deduction.

    Args:
        solar_investment_lkr: Raw solar investment amount from Deductions.

    Returns:
        Capped relief amount (min(input, 600K)). Always non-negative.
    """
    if solar_investment_lkr < 0:
        raise ValueError(
            f"solar_investment_lkr must be >= 0 (got {solar_investment_lkr})"
        )
    return min(solar_investment_lkr, SOLAR_RELIEF_CAP_LKR)


def compute_reliefs(
    deductions: Deductions,
    slabs: TaxYearSlabs,
    senior_citizen: bool = False,
) -> Reliefs:
    """Compute the applied-relief breakdown for a given year + senior flag.

    Args:
        deductions: input deductions (raw amounts, caps not yet applied).
        slabs: validated TaxYearSlabs (gives the personal-relief + senior amounts).
        senior_citizen: True if the taxpayer is 60+ for the year. Applies
            senior_citizen_extra_relief_lkr from slabs (zero in 24/25).

    Returns:
        Reliefs: applied amounts ready for taxable-income computation + audit.
    """
    personal_relief = Decimal(slabs.personal_relief_lkr)
    senior_extra = (
        Decimal(slabs.senior_citizen_extra_relief_lkr) if senior_citizen else Decimal("0")
    )
    solar_relief = compute_solar_relief(deductions.solar_investment_lkr)
    rent_relief = deductions.rent_relief_lkr  # pass-through, caller-capped
    expenditure_relief = deductions.expenditure_relief_lkr  # pass-through

    return Reliefs(
        personal_relief_applied_lkr=personal_relief,
        senior_citizen_extra_lkr=senior_extra,
        solar_relief_applied_lkr=solar_relief,
        rent_relief_applied_lkr=rent_relief,
        expenditure_relief_applied_lkr=expenditure_relief,
    )


def apply_relief(gross_income: Decimal, reliefs: Reliefs) -> Decimal:
    """Subtract total relief from gross income, clipping to zero.

    Mirrors SF FMLtaxableIncome = MAX(0, assessable - sum_of_reliefs).

    Args:
        gross_income: Income.total_assessable() result (LKR Decimal).
        reliefs: Reliefs object from compute_reliefs().

    Returns:
        Post-relief taxable income (LKR Decimal). Always >= 0.
    """
    if gross_income < 0:
        raise ValueError(f"gross_income must be >= 0 (got {gross_income})")
    taxable = gross_income - reliefs.total()
    if taxable < 0:
        return Decimal("0")
    return taxable


def gross_income(income: Income) -> Decimal:
    """Sum income components into a single assessable-income figure.

    Re-export of `Income.total_assessable()` for use by the engine.
    """
    return income.total_assessable()


__all__ = [
    "SOLAR_RELIEF_CAP_LKR",
    "compute_solar_relief",
    "compute_reliefs",
    "apply_relief",
    "gross_income",
]
