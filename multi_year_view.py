"""multi_year_view -- Tier D5 C3.

Two responsibilities:

1) `compute_yoy_comparison(user_id, years)` -> dict.
   For each tax-year in `years` (e.g. ["2024-25", "2025-26"]) calls
   `fiesta.tax_bill.compute.compute_tax_bill(user_id, year)` and returns a
   structure suitable for a side-by-side HTML table:

       {
         "user_id": ...,
         "years": ["2024-25", "2025-26"],
         "per_year": [
             {
               "tax_year_s4": "2024-25",
               "tax_year_s5": "2024/2025",
               "gross_income_lkr": "...",
               "total_deductions_lkr": "...",
               "taxable_income_lkr": "...",
               "net_tax_payable_lkr": "...",
               "audit_defensibility_score": 0..100,
               "audit_defensibility_label": "...",
               "rental_loss_carried_forward_lkr": "...",
               "engine_error": str | None,
             },
             ...
           ],
         "deltas": [
             {
               "from_year_s4": "2024-25",
               "to_year_s4":   "2025-26",
               "gross_income_delta_lkr": "...",
               "net_tax_delta_lkr": "...",
               "deductions_delta_lkr": "...",
               "score_delta": int,
             },
             ...
           ],
       }

   `years` is ordered oldest -> newest. Per-year rows preserve that order.
   `deltas` contains len(years)-1 entries, each `to_year - from_year`.

2) `get_carried_losses(user_id, current_year_s4)` -> Decimal.
   Returns the carry-forward rental loss FROM the immediately-prior tax year,
   for use as `prior_year_rental_loss_lkr` kwarg into `compute_tax_bill`.

   Scope cap (v1): ONLY rental losses. The "rental loss" of a year is defined
   as max(0, sum_of_annual_rental_expenses - rental_income_received_lkr) for
   that year, where:
     - rental_income_received_lkr = IncomeEntry rows in category "rental"
       (already aggregated as `inputs.income_by_category_lkr["rental"]`).
     - annual rental expenses = sum of (RentalAgreement.monthly_rent_lkr * 12)
       for that user / year (the rent the user paid out, treated as a rental-
       business expense). The aggregator surfaces this as
       `inputs.rental_total_lkr`.

   When the v1 RentalAgreement model doesn't track an "expense" field separate
   from monthly rent, this is the most defensible proxy: rent paid is the
   biggest cash outflow of a rental sub-let / property-management operation
   and is already captured. A v2 would carry a separate annual_expenses field
   per agreement; out of scope here.

   Only carries forward ONE prior year (n-1), not deeper chains. v1.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Iterable, Optional

from fiesta.tax_bill.aggregator import (
    assemble_tax_inputs,
    normalise_tax_year_to_s4_format,
    normalise_tax_year_to_s5_format,
)
from fiesta.tax_bill.compute import compute_tax_bill


# Newest-first ordering matches routes._SUPPORTED_TAX_YEARS_S4_NEWEST_FIRST.
# Kept here as a local fallback so this module is importable in unit tests
# that don't load the routes blueprint.
_KNOWN_TAX_YEARS_OLDEST_FIRST: list[str] = ["2024-25", "2025-26"]


# ---------------------------------------------------------------------------
# Carry-forward
# ---------------------------------------------------------------------------


def _prior_year_s4(current_year_s4: str) -> Optional[str]:
    """Return the year immediately before `current_year_s4`, or None."""
    s4 = normalise_tax_year_to_s4_format(current_year_s4)
    if s4 not in _KNOWN_TAX_YEARS_OLDEST_FIRST:
        return None
    idx = _KNOWN_TAX_YEARS_OLDEST_FIRST.index(s4)
    if idx == 0:
        return None
    return _KNOWN_TAX_YEARS_OLDEST_FIRST[idx - 1]


def _compute_rental_loss_for_year(user_id: int, year_s4: str) -> Decimal:
    """Compute rental loss for one year, NOT applying any prior-year carry.

    Loss := max(0, annual_rental_expenses - rental_income_received).

    Uses raw `assemble_tax_inputs` directly (not `compute_tax_bill`) so we
    don't recursively trigger carry-forward and so we can be called from
    inside `compute_tax_bill` itself in future without re-entrancy concerns.
    """
    inputs = assemble_tax_inputs(user_id, year_s4)
    rental_income = inputs.income_by_category_lkr.get("rental", Decimal("0"))
    rental_expenses = inputs.rental_total_lkr or Decimal("0")
    loss = rental_expenses - rental_income
    if loss < Decimal("0"):
        return Decimal("0")
    return loss


def get_carried_losses(user_id: int, current_year_s4: str) -> Decimal:
    """Return rental loss carried forward FROM prior year INTO current_year.

    Returns Decimal("0") if there is no prior supported year or if the prior
    year had no rental loss. v1 carries only one year back.
    """
    prior = _prior_year_s4(current_year_s4)
    if prior is None:
        return Decimal("0")
    try:
        return _compute_rental_loss_for_year(user_id, prior)
    except Exception:
        return Decimal("0")


# ---------------------------------------------------------------------------
# YoY comparison
# ---------------------------------------------------------------------------


def _per_year_row(user_id: int, year: str) -> dict:
    year_s4 = normalise_tax_year_to_s4_format(year)
    carried = get_carried_losses(user_id, year_s4)
    report = compute_tax_bill(
        user_id,
        year_s4,
        prior_year_rental_loss_lkr=carried,
    )
    return {
        "tax_year_s4": report.tax_year_s4_format,
        "tax_year_s5": report.tax_year_s5_format,
        "gross_income_lkr": str(report.gross_income_lkr),
        "total_deductions_lkr": str(report.total_deductions_lkr),
        "taxable_income_lkr": str(report.taxable_income_lkr),
        "gross_tax_payable_lkr": str(report.gross_tax_payable_lkr),
        "net_tax_payable_lkr": str(report.net_tax_payable_lkr),
        "tax_without_deductions_lkr": str(report.tax_without_deductions_lkr),
        "savings_vs_no_deductions_lkr": str(report.savings_vs_no_deductions_lkr),
        "audit_defensibility_score": report.audit_defensibility_score,
        "audit_defensibility_label": report.audit_defensibility_label,
        "rental_loss_carried_forward_lkr": str(carried),
        "engine_error": report.engine_error,
    }


def _delta_row(prev_row: dict, curr_row: dict) -> dict:
    """Compute year-over-year delta between two per-year rows (numeric str -> Decimal)."""

    def _d(row: dict, k: str) -> Decimal:
        try:
            return Decimal(row.get(k) or "0")
        except Exception:
            return Decimal("0")

    return {
        "from_year_s4": prev_row["tax_year_s4"],
        "to_year_s4": curr_row["tax_year_s4"],
        "gross_income_delta_lkr": str(
            _d(curr_row, "gross_income_lkr") - _d(prev_row, "gross_income_lkr")
        ),
        "deductions_delta_lkr": str(
            _d(curr_row, "total_deductions_lkr")
            - _d(prev_row, "total_deductions_lkr")
        ),
        "taxable_income_delta_lkr": str(
            _d(curr_row, "taxable_income_lkr")
            - _d(prev_row, "taxable_income_lkr")
        ),
        "net_tax_delta_lkr": str(
            _d(curr_row, "net_tax_payable_lkr")
            - _d(prev_row, "net_tax_payable_lkr")
        ),
        "score_delta": (
            int(curr_row.get("audit_defensibility_score") or 0)
            - int(prev_row.get("audit_defensibility_score") or 0)
        ),
    }


def compute_yoy_comparison(
    user_id: int,
    years: Iterable[str],
) -> dict:
    """Compute side-by-side YoY for the given list of tax years.

    `years` is ordered oldest -> newest. Normalisation is applied internally
    so callers can pass S4 ("2024-25"), S5 ("2024/2025"), short ("24/25"),
    underscore ("24_25") or enum-ish ("Y24_25") forms interchangeably.
    """
    normalised = [normalise_tax_year_to_s4_format(y) for y in years]

    # De-dup while preserving order.
    seen: set[str] = set()
    ordered: list[str] = []
    for y in normalised:
        if y in seen:
            continue
        seen.add(y)
        ordered.append(y)

    per_year = [_per_year_row(user_id, y) for y in ordered]

    deltas: list[dict] = []
    for i in range(1, len(per_year)):
        deltas.append(_delta_row(per_year[i - 1], per_year[i]))

    return {
        "user_id": int(user_id),
        "years": ordered,
        "per_year": per_year,
        "deltas": deltas,
    }


__all__ = [
    "compute_yoy_comparison",
    "get_carried_losses",
]
