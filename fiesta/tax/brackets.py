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

from .types import (
    Bracket,
    BracketResult,
    FirstTaxableBand,
    ForeignFlatCap,
    TaxYear,
    TaxYearSlabs,
    TaxYearStructure,
)

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

    Handles both single-track (24/25: flat `brackets` list) and dual-track
    (25/26+: `first_taxable_band` + `foreign_above_first_band` +
    `local_above_first_band.brackets`) shapes.

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

    structure_raw = entry.get("structure", "single_track")
    structure = TaxYearStructure(structure_raw)

    personal_relief = int(entry.get("personal_relief_lkr", 0))
    senior_extra = int(entry.get("senior_citizen_extra_relief_lkr", 0))

    if structure == TaxYearStructure.SINGLE_TRACK:
        brackets_raw = entry.get("brackets", [])
        brackets = tuple(Bracket.model_validate(b) for b in brackets_raw)
        return TaxYearSlabs(
            year=year,
            structure=structure,
            personal_relief_lkr=personal_relief,
            senior_citizen_extra_relief_lkr=senior_extra,
            brackets=brackets,
        )

    # Dual-track: 25/26 onwards.
    first_band_raw = entry.get("first_taxable_band")
    if not first_band_raw:
        raise ValueError(
            f"Tax year {key} declared dual-track but missing `first_taxable_band` block"
        )
    foreign_raw = entry.get("foreign_above_first_band")
    if not foreign_raw:
        raise ValueError(
            f"Tax year {key} declared dual-track but missing `foreign_above_first_band` block"
        )
    local_block = entry.get("local_above_first_band") or {}
    local_brackets_raw = local_block.get("brackets", [])
    local_brackets = tuple(Bracket.model_validate(b) for b in local_brackets_raw)

    return TaxYearSlabs(
        year=year,
        structure=structure,
        personal_relief_lkr=personal_relief,
        senior_citizen_extra_relief_lkr=senior_extra,
        first_taxable_band=FirstTaxableBand.model_validate(first_band_raw),
        foreign_above_first_band=ForeignFlatCap.model_validate(foreign_raw),
        local_above_first_band_brackets=local_brackets,
    )


def compute_bracket_tax(
    taxable_income: Decimal, slabs: TaxYearSlabs
) -> list[BracketResult]:
    """Walk the slabs band-by-band, returning per-band slice + tax.

    Single-track years (24/25): walks `slabs.brackets` in order. Returned
    list length == len(slabs.brackets).

    Dual-track years (25/26+): not appropriate for this helper because the
    walk requires the foreign vs local income split. Callers should use
    `compute_bracket_tax_dual_track` instead. For back-compat we still
    accept dual-track here and treat all income as local (the conservative
    default per CEO directive 2026-05-20).

    Always returns a list with one entry per bracket in `slabs`, even when
    the income doesn't reach later bands (those return income_in_band=0,
    tax_in_band=0). This keeps the audit-trail shape constant — UI doesn't
    have to handle a variable-length array per customer.

    Args:
        taxable_income: post-relief taxable income (LKR, Decimal). Must be >=0.
        slabs: validated TaxYearSlabs for the target year.

    Returns:
        list[BracketResult] — one per bracket in slabs.brackets, in order
        (single-track) OR the full dual-track shape (first + foreign + local).

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

    if slabs.structure == TaxYearStructure.DUAL_TRACK:
        # Conservative default: treat all taxable income as local.
        return compute_bracket_tax_dual_track(
            taxable_income=taxable_income,
            foreign_gross=Decimal("0"),
            local_gross=taxable_income if taxable_income > 0 else Decimal("1"),
            slabs=slabs,
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
                track=None,
            )
        )

        remaining -= income_in_band
        if bracket.up_to is not None:
            prev_upper = Decimal(bracket.up_to)
        if remaining <= 0:
            # Don't break — keep the per-band shape constant. Fall through.
            remaining = Decimal("0")

    return results


def compute_bracket_tax_dual_track(
    taxable_income: Decimal,
    foreign_gross: Decimal,
    local_gross: Decimal,
    slabs: TaxYearSlabs,
) -> list[BracketResult]:
    """Walk dual-track slabs (25/26+) returning the full per-band audit trail.

    Algorithm (CEO directive 2026-05-20 IST):
      1. Apply `first_taxable_band` (Rs 1M @ 6%) to the FIRST Rs N of taxable
         income regardless of source. Uses up the shared lower threshold.
      2. Remaining taxable = max(0, taxable_income - first_band_top).
      3. Split remaining pro-rata by source:
            foreign_share = foreign_gross / (foreign_gross + local_gross)
            local_share = 1 - foreign_share
            foreign_taxable_above = remaining × foreign_share
            local_taxable_above = remaining × local_share
         If both shares are zero (foreign_gross + local_gross == 0), default
         to 100% local (conservative — applies higher rates).
      4. Apply foreign flat-cap rate to `foreign_taxable_above`.
      5. Walk local progressive bands for `local_taxable_above`.

    Output shape (constant for the year):
      [first_band, foreign_band, *local_bands]
      For 25/26 this is 6 entries: 1 first + 1 foreign + 4 local.

    Args:
        taxable_income: post-relief taxable income (LKR Decimal, >=0).
        foreign_gross: foreign-sourced gross income (drives source split).
        local_gross: local-sourced gross income.
        slabs: dual-track TaxYearSlabs (structure=DUAL_TRACK).

    Returns:
        list[BracketResult] with constant shape per year.

    Raises:
        ValueError if slabs is not dual-track.
    """
    if slabs.structure != TaxYearStructure.DUAL_TRACK:
        raise ValueError(
            f"compute_bracket_tax_dual_track called on non-dual-track slabs "
            f"(structure={slabs.structure})"
        )
    if taxable_income < 0:
        raise ValueError(f"taxable_income must be >= 0 (got {taxable_income})")
    if foreign_gross < 0 or local_gross < 0:
        raise ValueError(
            f"foreign_gross + local_gross must both be >= 0 "
            f"(foreign={foreign_gross}, local={local_gross})"
        )

    assert slabs.first_taxable_band is not None
    assert slabs.foreign_above_first_band is not None

    first_band = slabs.first_taxable_band
    foreign_cap = slabs.foreign_above_first_band

    # Step 1: first taxable band (shared, single cycle).
    first_band_top = Decimal(first_band.up_to)
    first_band_filled = min(taxable_income, first_band_top)
    first_band_tax = first_band_filled * first_band.rate

    results: list[BracketResult] = [
        BracketResult(
            band_lower=Decimal("0"),
            band_upper=first_band_top,
            income_in_band=first_band_filled,
            rate=first_band.rate,
            tax_in_band=first_band_tax,
            track="first",
        )
    ]

    # Step 2: remaining taxable income above the first band.
    remaining = taxable_income - first_band_filled
    if remaining < 0:
        remaining = Decimal("0")

    # Step 3: split by source. Conservative default: if neither source has
    # gross income recorded, treat 100% as local (higher rates).
    total_source_gross = foreign_gross + local_gross
    if total_source_gross > 0:
        foreign_share = foreign_gross / total_source_gross
    else:
        foreign_share = Decimal("0")  # default-local

    foreign_taxable_above = remaining * foreign_share
    local_taxable_above = remaining - foreign_taxable_above

    # Step 4: foreign band — flat cap rate.
    foreign_tax = foreign_taxable_above * foreign_cap.flat_rate
    results.append(
        BracketResult(
            band_lower=first_band_top,
            band_upper=None,  # foreign cap is open-ended (it's a max-rate)
            income_in_band=foreign_taxable_above,
            rate=foreign_cap.flat_rate,
            tax_in_band=foreign_tax,
            track="foreign",
        )
    )

    # Step 5: walk local progressive bands.
    local_remaining = local_taxable_above
    # Local band coordinates are expressed RELATIVE to the first-band top.
    # i.e. local up_to=500000 means "1M-1.5M of taxable" in absolute terms.
    prev_upper_rel = Decimal("0")
    for bracket in slabs.local_above_first_band_brackets:
        band_lower_abs = first_band_top + prev_upper_rel
        if bracket.up_to is None:
            band_upper_abs: Decimal | None = None
            income_in_band = local_remaining
        else:
            band_top_rel = Decimal(bracket.up_to)
            band_upper_abs = first_band_top + band_top_rel
            band_width = band_top_rel - prev_upper_rel
            income_in_band = min(local_remaining, band_width)
            if income_in_band < 0:
                income_in_band = Decimal("0")

        tax_in_band = income_in_band * bracket.rate

        results.append(
            BracketResult(
                band_lower=band_lower_abs,
                band_upper=band_upper_abs,
                income_in_band=income_in_band,
                rate=bracket.rate,
                tax_in_band=tax_in_band,
                track="local",
            )
        )

        local_remaining -= income_in_band
        if bracket.up_to is not None:
            prev_upper_rel = Decimal(bracket.up_to)
        if local_remaining <= 0:
            local_remaining = Decimal("0")

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
    "compute_bracket_tax_dual_track",
    "sum_bracket_tax",
    "marginal_rate",
]
