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

from datetime import date
from decimal import Decimal
from typing import Optional

from .brackets import (
    compute_bracket_tax,
    compute_bracket_tax_dual_track,
    get_slabs,
    marginal_rate as compute_marginal_rate,
    sum_bracket_tax,
)
from .relief import apply_relief, compute_reliefs, gross_income as compute_gross_income
from .residency import ResidencyStatus
from .types import (
    BracketResult,
    Deductions,
    Income,
    TaxComputation,
    TaxYear,
    TaxYearStructure,
)


def _apply_nrr_exemption(
    income: Income,
    residency_status: Optional[ResidencyStatus],
    returned_to_sl_date: Optional[date],
    on: Optional[date],
) -> Income:
    """B10 NRR — exempt foreign-source income during the 3-year window.

    Date-anchored: window expires at ``returned_to_sl_date + 3 years``
    exactly. If ``on`` is past the boundary, the exemption no longer
    applies and the income flows through untouched (caller's regular
    resident/non-resident bracket logic kicks in).

    Returns a NEW Income object (frozen pydantic model) with
    ``foreign_lkr=0`` when the exemption applies; otherwise returns the
    input unchanged.
    """
    if residency_status != ResidencyStatus.NRR:
        return income
    if returned_to_sl_date is None:
        # Can't anchor the window — be safe, don't exempt.
        return income
    from dateutil.relativedelta import relativedelta
    on_date = on or date.today()
    window_end = returned_to_sl_date + relativedelta(years=3)
    if on_date >= window_end:
        # Window expired — no exemption.
        return income
    # Active NRR window: zero out foreign_lkr.
    return income.model_copy(update={"foreign_lkr": Decimal("0")})


def compute_tax(
    income: Income,
    deductions: Deductions,
    year: TaxYear,
    senior_citizen: bool = False,
    residency_status: Optional[ResidencyStatus] = None,
    returned_to_sl_date: Optional[date] = None,
    on: Optional[date] = None,
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

    B10 NRR (added MS2 E.1): when ``residency_status=ResidencyStatus.NRR``
    and ``returned_to_sl_date`` is within the 3-year concession window,
    ``income.foreign_lkr`` is zeroed out BEFORE relief + bracket walking.
    No exemption applies for RESIDENT (foreign income taxed normally), or
    after the window expires.
    """
    # B10 NRR — apply foreign-income exemption before any bracket math.
    income = _apply_nrr_exemption(
        income=income,
        residency_status=residency_status,
        returned_to_sl_date=returned_to_sl_date,
        on=on,
    )

    slabs = get_slabs(year)

    gross = compute_gross_income(income)
    deductions_total = (
        deductions.solar_investment_lkr
        + deductions.rent_relief_lkr
        + deductions.expenditure_relief_lkr
    )

    reliefs = compute_reliefs(deductions, slabs, senior_citizen=senior_citizen)
    taxable = apply_relief(gross, reliefs)

    # Dispatch on structure. Dual-track requires the foreign vs local gross
    # split so we can apply the 15% cap to foreign and the progressive bands
    # to local (CEO directive 2026-05-20).
    if slabs.structure == TaxYearStructure.DUAL_TRACK:
        by_band: list[BracketResult] = compute_bracket_tax_dual_track(
            taxable_income=taxable,
            foreign_gross=income.foreign_gross(),
            local_gross=income.local_gross(),
            slabs=slabs,
        )
    else:
        by_band = compute_bracket_tax(taxable, slabs)
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
    residency_status: Optional[ResidencyStatus] = None,
    returned_to_sl_date: Optional[date] = None,
    on: Optional[date] = None,
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
        residency_status: Optional ResidencyStatus. When NRR + within the
          3-year window, foreign_lkr is exempted (B10).
        returned_to_sl_date: Required for NRR window calculation.
        on: Reference date for the NRR window check (defaults to today).

    Returns:
        TaxComputation for tax year 25/26.
    """
    deductions = deductions if deductions is not None else Deductions()
    return compute_tax(
        income=income,
        deductions=deductions,
        year=TaxYear.Y25_26,
        senior_citizen=senior_citizen,
        residency_status=residency_status,
        returned_to_sl_date=returned_to_sl_date,
        on=on,
    )


__all__ = ["compute_tax", "compute_tax_25_26"]
