"""fiesta.tax.brackets — pure bracket arithmetic + slab loading.

Loads version-pinned slabs from `fiesta/tax/data/slabs.yaml` and walks them
band-by-band for a given taxable income, returning a list of `BracketResult`
entries (per-band slice + tax).

Pure function. No I/O outside slab loading. No dependency on the rest of the
engine — `compute_bracket_tax()` is the unit-testable atom.

Acceptance: when taxable_income = 0, returns a single zero-tax band entry.
When taxable_income exactly hits a band boundary (e.g. 1,000,000 in 25/26),
the income_in_band for the boundary band is the full band width and the next
band's income_in_band is 0.
"""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import yaml

from .types import Bracket, BracketResult, TaxYear, TaxYearSlabs

_DATA_DIR = Path(__file__).parent / "data"
_SLABS_PATH = _DATA_DIR / "slabs.yaml"


@lru_cache(maxsize=1)
def _load_slabs_yaml() -> dict:
    """Load and cache slabs.yaml at module-import time (lazy)."""
    with _SLABS_PATH.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict) or "years" not in data:
        raise ValueError(
            f"slabs.yaml malformed: expected top-level 'years' key, got {list(data.keys()) if isinstance(data, dict) else type(data).__name__}"
        )
    return data


@lru_cache(maxsize=8)
def get_slabs(year: TaxYear) -> TaxYearSlabs:
    """Return validated TaxYearSlabs for the given year.

    Raises ValueError if year not present in slabs.yaml.
    """
    data = _load_slabs_yaml()
    years_block = data["years"]
    key = year.value
    if key not in years_block:
        raise ValueError(
            f"Tax year {key} not in slabs.yaml. Available: {list(years_block.keys())}"
        )
    entry = years_block[key]
    brackets_raw = entry.get("brackets", [])
    brackets = tuple(Bracket.model_validate(b) for b in brackets_raw)
    return TaxYearSlabs(
        year=year,
        personal_relief_lkr=int(entry.get("personal_relief_lkr", 0)),
        senior_citizen_extra_relief_lkr=int(entry.get("senior_citizen_extra_relief_lkr", 0)),
        brackets=brackets,
    )


def compute_bracket_tax(
    taxable_income: Decimal, slabs: TaxYearSlabs
) -> list[BracketResult]:
    """Walk the slabs band-by-band, returning per-band slice + tax.

    Always returns a list with one entry per bracket in `slabs`, even when
    the income doesn't reach later bands (those return income_in_band=0,
    tax_in_band=0). This keeps the audit-trail shape constant — UI doesn't
    have to handle a variable-length array per customer.

    Args:
        taxable_income: post-relief taxable income (LKR, Decimal). Must be >=0.
        slabs: validated TaxYearSlabs for the target year.

    Returns:
        list[BracketResult] — one per bracket in slabs.brackets, in order.

    Raises:
        ValueError if taxable_income is negative (relief module should have
        clipped to zero already; reaching here negative is a contract bug).
    """
    if taxable_income < 0:
        raise ValueError(
            f"taxable_income must be >= 0 (got {taxable_income}). "
            f"Relief module is responsible for clipping; reaching brackets "
            f"with negative income is a contract bug."
        )

    results: list[BracketResult] = []
    remaining = taxable_income
    prev_upper = Decimal("0")

    for bracket in slabs.brackets:
        band_lower = prev_upper
        if bracket.up_to is None:
            # Open-ended top band: take whatever remains
            band_upper: Decimal | None = None
            income_in_band = remaining
        else:
            band_top = Decimal(bracket.up_to)
            band_upper = band_top
            band_width = band_top - band_lower
            income_in_band = min(remaining, band_width)
            if income_in_band < 0:
                income_in_band = Decimal("0")

        tax_in_band = income_in_band * bracket.rate

        results.append(
            BracketResult(
                band_lower=band_lower,
                band_upper=band_upper,
                income_in_band=income_in_band,
                rate=bracket.rate,
                tax_in_band=tax_in_band,
            )
        )

        remaining -= income_in_band
        if bracket.up_to is not None:
            prev_upper = Decimal(bracket.up_to)
        if remaining <= 0:
            # Don't break — keep the per-band shape constant. Fall through.
            remaining = Decimal("0")

    return results


def sum_bracket_tax(by_band: list[BracketResult]) -> Decimal:
    """Sum tax_in_band across all bands. Single source of truth for gross_tax."""
    total = Decimal("0")
    for b in by_band:
        total += b.tax_in_band
    return total


def marginal_rate(by_band: list[BracketResult]) -> Decimal:
    """Return the rate of the highest band that has non-zero income.

    For taxable_income = 0, returns the lowest band's rate (no tax owed, but
    a Rs 1 marginal increase would attract the 6% rate).
    """
    last_active_rate = by_band[0].rate if by_band else Decimal("0")
    for b in by_band:
        if b.income_in_band > 0:
            last_active_rate = b.rate
    return last_active_rate


__all__ = [
    "get_slabs",
    "compute_bracket_tax",
    "sum_bracket_tax",
    "marginal_rate",
]
