"""fiesta.tax.engine — main computation orchestrator.

Wires income + deductions + reliefs + brackets into a single TaxComputation.
Pure function, offline-computable from inputs, deterministic. No SF, no IRD
eService, no network. The engine is the authoritative computation; JS client
is preview only (see `_js_parity.py`).

Contract:
  compute_tax_25_26(income, deductions, senior_citizen=False)
    -> TaxComputation

  compute_tax(income, deductions, year, senior_citizen=False)
    -> TaxComputation   (multi-year wrapper used for 24/25 regression too)

Both return the same TaxComputation shape. `compute_tax_25_26` is the v1
public surface called from S0/S12/S14; `compute_tax` is for regression tests
and Phase 4 multi-year expansion.

This file does NOT compute foreign-income FX (Phase 2), donations/QP
(Phase 3), penalties (Phase 4), or amendments (Phase 4). Those modules wrap
this engine and pass already-LKR-converted income in.
"""

from __future__ import annotations

from decimal import Decimal

from .brackets import (
    compute_bracket_tax,
    get_slabs,
    marginal_rate as compute_marginal_rate,
    sum_bracket_tax,
)
from .relief import apply_relief, compute_reliefs, gross_income as compute_gross_income
from .types import (
    BracketResult,
    Deductions,
    Income,
    TaxComputation,
    TaxYear,
)


def compute_tax(
    income: Income,
    deductions: Deductions,
    year: TaxYear,
    senior_citizen: bool = False,
) -> TaxComputation:
    """Compute tax for any supported year (used by 24/25 regression + 25/26).

    Args:
        income: Income components (LKR Decimal, pre-summed).
        deductions: Pre-relief deductions (solar etc.). Raw — caps applied inside.
        year: TaxYear enum (24_25 or 25_26).
        senior_citizen: True if taxpayer is 60+ for the year. Applies the
            year's senior_citizen_extra_relief_lkr (zero in 24/25 per SF flow).

    Returns:
        TaxComputation with full audit trail.
    """
    slabs = get_slabs(year)

    gross = compute_gross_income(income)
    deductions_total = (
        deductions.solar_investment_lkr
        + deductions.rent_relief_lkr
        + deductions.expenditure_relief_lkr
    )

    reliefs = compute_reliefs(deductions, slabs, senior_citizen=senior_citizen)
    taxable = apply_relief(gross, reliefs)
    by_band: list[BracketResult] = compute_bracket_tax(taxable, slabs)
    gross_tax = sum_bracket_tax(by_band)

    # net_tax_due: for Phase 1, equal to gross_tax. Phase 2/3 will subtract
    # WHT credits, foreign tax credits, qualifying-payment relief etc. Keep
    # the field present in the output now so downstream consumers don't have
    # to change shape later.
    net_tax_due = gross_tax

    marginal = compute_marginal_rate(by_band)
    effective = (
        gross_tax / gross if gross > 0 else Decimal("0")
    )

    return TaxComputation(
        tax_year=year,
        gross_income_lkr=gross,
        deductions_input_lkr=deductions_total,
        relief_applied=reliefs,
        taxable_income_lkr=taxable,
        by_band=tuple(by_band),
        gross_tax_lkr=gross_tax,
        net_tax_due_lkr=net_tax_due,
        marginal_rate=marginal,
        effective_rate=effective,
    )


def compute_tax_25_26(
    income: Income,
    deductions: Deductions | None = None,
    senior_citizen: bool = False,
) -> TaxComputation:
    """Compute tax for tax year 25/26 — v1 public surface.

    Convenience wrapper around `compute_tax(year=TaxYear.Y25_26)`. Used by:
      - S0 landing estimator (server-side authority)
      - S12 tax bill display
      - S14 Auto-File pre-fill

    Args:
        income: Income components (LKR Decimal).
        deductions: Optional. Empty deductions if None.
        senior_citizen: True if taxpayer is 60+ for the year.

    Returns:
        TaxComputation for tax year 25/26.
    """
    deductions = deductions if deductions is not None else Deductions()
    return compute_tax(
        income=income,
        deductions=deductions,
        year=TaxYear.Y25_26,
        senior_citizen=senior_citizen,
    )


__all__ = ["compute_tax", "compute_tax_25_26"]
