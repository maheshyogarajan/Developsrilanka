"""fiesta.tax.preview — lightweight bracket-by-bracket estimator for S0 landing.

PURPOSE
=======
Power the "Tax Math Breakdown" component on the pre-paywall landing page.
Customer types gross income + a couple of optional fields, sees the
bracket walk, sees their projected saving, then decides to pay Rs 2,500.

This module is the SoT for that calculation. It reads the same
`fiesta/tax/data/slabs.yaml` that the full engine reads, so the preview
number cannot drift from the engine number unless the engine reads a
different SoT (which it shouldn't).

DESIGN NOTES
============
- All currency: `Decimal`. No `float`.
- Two flavours of "tax" returned:
    1. `naive_tax_lkr`  — what an under-prepared filer would owe (no
       defensible deductions claimed).
    2. `fiesta_tax_lkr` — what they'd owe *with* legitimate deductions
       FIESTA helps them document (Service Provider fees, home-office
       rental, etc.).
- The `saving_lkr` is `naive_tax - fiesta_tax`. Always >= 0.
- IRA citations carried alongside every relief so the UI can show them.
- Output is *PREVIEW*, not authoritative. UI must flag this clearly.

CONSTRAINTS
===========
- Returns serialisable dict (JSON-safe — strings for Decimal).
- Pure function. No I/O except the cached slabs.yaml load.
- Sub-50ms target on the calc step (the YAML load is cached at module level).

PROVENANCE
==========
- Council THE_PATH_20260520.md Risk A mitigation.
- Council brief tax_math_anchors (unanimous on 25/26 slabs).
- Worked example numbers verified against council brief.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_DATA_DIR = Path(__file__).parent / "data"
_SLABS_PATH = _DATA_DIR / "slabs.yaml"


# Supported tax years. v1 locks to 25/26; 26/27 reserved (gazette not yet out).
SUPPORTED_YEARS = ("25_26",)
DEFAULT_YEAR = "25_26"


# Currency conversion rates (mid-rate, preview-only).
# These are placeholder fallbacks used when no live FX feed is wired in.
# Real production must replace with CBSL mid-rate or Stripe FX feed.
# Updated 2026-05-20 from CBSL indicative rates.
FX_FALLBACK_LKR_PER_UNIT: dict[str, Decimal] = {
    "LKR": Decimal("1"),
    "USD": Decimal("302"),
    "EUR": Decimal("328"),
    "GBP": Decimal("382"),
    "AUD": Decimal("199"),
    "SGD": Decimal("224"),
}

# Recognised income sources. Drives the deduction-rate heuristic for the
# "with FIESTA" tax estimate.
INCOME_SOURCES = ("employment", "foreign", "mix")

# FIESTA deduction-capture rate per source. This is the *typical* additional
# deductible the average customer can substantiate when working with FIESTA.
# Conservative estimate — based on Lanka.tax 2024/25 sample of foreign-income
# filers (n=78, council brief THE_PATH section 4).
#
# Tuned for "honest preview that customer can defend in an audit". NOT a
# promise — every individual case differs and the engine will compute the
# real number from actual receipts.
FIESTA_DEDUCTION_RATE = {
    "employment": Decimal("0.05"),   # Few deductions available for employees
    "foreign":    Decimal("0.22"),   # Self-employed foreign-income: home office, SP fees, software, training, insurance
    "mix":        Decimal("0.13"),   # Blended estimate
}

# Cap on FIESTA-helped deductions as a fraction of gross income. Without
# this cap, very low-income earners would see implausibly large savings.
# IRA §6 "wholly, exclusively, necessarily" — total deductions rarely
# exceed 25% of gross for legitimate self-employed.
FIESTA_DEDUCTION_CEILING_PCT = Decimal("0.30")


# IRA citations for the relief / deduction reasoning. These get surfaced in
# the UI so customers see the legal basis.
IRA_CITATIONS = {
    "personal_relief": {
        "section": "First Schedule, Part I",
        "label": "Personal relief Rs 1.8M (24/25 amendment)",
    },
    "senior_relief": {
        "section": "§51",
        "label": "Senior-citizen extra relief Rs 500K",
    },
    "service_provider_fees": {
        "section": "§6(1)",
        "label": "Service Provider fees — wholly, exclusively, necessarily incurred",
    },
    "home_office_rental": {
        "section": "§6(1)",
        "label": "Home-office portion of rental — proportionate to business use",
    },
    "remittance_basis": {
        "section": "§13",
        "label": "Foreign income — remittance basis for resident individuals",
    },
    "brackets": {
        "section": "First Schedule, Part I",
        "label": "Progressive PIT slabs",
    },
}


class PreviewError(ValueError):
    """Raised on known-bad input (negative income, unsupported currency, etc.).

    Distinct from generic ValueError so the route can map it to HTTP 400
    without swallowing real bugs.
    """


@lru_cache(maxsize=1)
def _load_slabs() -> dict[str, Any]:
    """Load and validate slabs.yaml. Cached for the process lifetime."""
    if not _SLABS_PATH.exists():
        raise PreviewError(
            f"slabs.yaml missing at {_SLABS_PATH} — tax preview cannot run"
        )
    with _SLABS_PATH.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict) or "years" not in data:
        raise PreviewError("slabs.yaml malformed — missing 'years' key")
    return data


def _get_year_slabs(year: str) -> dict[str, Any]:
    data = _load_slabs()
    years_block = data["years"]
    if year not in years_block:
        raise PreviewError(
            f"Tax year {year!r} not in slabs.yaml. Available: {list(years_block.keys())}"
        )
    return years_block[year]


def _to_decimal(value: Any, field: str) -> Decimal:
    """Coerce input to Decimal — accept int, float, str, Decimal."""
    if isinstance(value, Decimal):
        return value
    if value is None:
        return Decimal("0")
    if isinstance(value, bool):
        # Avoid Python's int(True)==1 silently flowing through
        raise PreviewError(f"{field} must be numeric, got bool")
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        s = value.strip().replace(",", "")
        if not s:
            return Decimal("0")
        try:
            return Decimal(s)
        except Exception as exc:
            raise PreviewError(f"{field}={value!r} is not a valid number") from exc
    raise PreviewError(f"{field} type {type(value).__name__} not supported")


def _round_lkr(d: Decimal) -> Decimal:
    """Round to nearest whole LKR — IRD convention is integer LKR on returns."""
    return d.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def _walk_brackets(taxable_income: Decimal, brackets: list[dict]) -> list[dict]:
    """Walk slabs band-by-band. Returns one entry per band (constant shape)."""
    results: list[dict] = []
    remaining = taxable_income
    prev_upper = Decimal("0")

    for bracket in brackets:
        up_to = bracket.get("up_to")
        rate = Decimal(str(bracket.get("rate", "0")))
        band_lower = prev_upper

        if up_to is None:
            # Open-ended top band
            band_upper: Decimal | None = None
            income_in_band = max(remaining, Decimal("0"))
        else:
            band_top = Decimal(str(up_to))
            band_upper = band_top
            band_width = band_top - band_lower
            income_in_band = min(max(remaining, Decimal("0")), band_width)

        tax_in_band = income_in_band * rate

        # Display rate as integer % when possible (e.g. "6%", "30%", "36%").
        rate_pct = rate * Decimal("100")
        if rate_pct == rate_pct.to_integral_value():
            rate_pct_display = f"{int(rate_pct)}%"
        else:
            rate_pct_display = f"{rate_pct.normalize()}%"

        results.append({
            "band_lower_lkr": str(_round_lkr(band_lower)),
            "band_upper_lkr": str(_round_lkr(band_upper)) if band_upper is not None else None,
            "income_in_band_lkr": str(_round_lkr(income_in_band)),
            "rate": str(rate),
            "rate_pct_display": rate_pct_display,
            "tax_in_band_lkr": str(_round_lkr(tax_in_band)),
        })

        remaining -= income_in_band
        if up_to is not None:
            prev_upper = Decimal(str(up_to))

    return results


def _convert_to_lkr(amount: Decimal, currency: str) -> tuple[Decimal, Decimal]:
    """Return (lkr_amount, rate_used). Raise PreviewError on unknown currency."""
    cur = currency.upper().strip()
    if cur not in FX_FALLBACK_LKR_PER_UNIT:
        raise PreviewError(
            f"Currency {currency!r} not supported in preview. "
            f"Use one of: {', '.join(FX_FALLBACK_LKR_PER_UNIT.keys())}"
        )
    rate = FX_FALLBACK_LKR_PER_UNIT[cur]
    return amount * rate, rate


def quick_preview(
    gross_income: Any,
    currency: str = "LKR",
    income_source: str = "foreign",
    sp_fee: Any = 0,
    rental: Any = 0,
    senior: bool = False,
    year: str = DEFAULT_YEAR,
) -> dict[str, Any]:
    """Compute a customer-facing tax preview.

    Args:
        gross_income: gross annual income in `currency`. Decimal-coercible.
        currency: ISO code (LKR, USD, EUR, GBP, AUD, SGD).
        income_source: 'employment', 'foreign', or 'mix'.
        sp_fee: optional Service Provider fee (per year, in `currency`).
        rental: optional monthly home-office rental (in `currency`).
        senior: True if resident individual aged 60+ (extra Rs 500K relief).
        year: tax year key. v1 only supports '25_26'.

    Returns:
        A JSON-serialisable dict with:
          gross_income_lkr, currency, fx_rate_used,
          income_source, year,
          relief_total_lkr, personal_relief_lkr, senior_relief_lkr,
          fiesta_deductions_lkr, fiesta_deduction_pct_applied,
          taxable_income_naive_lkr, taxable_income_fiesta_lkr,
          bracket_breakdown_naive, bracket_breakdown_fiesta,
          naive_tax_lkr, fiesta_tax_lkr, saving_lkr, saving_pct,
          effective_rate_naive, effective_rate_fiesta,
          ira_citations, disclaimer,
          inputs (echoed back for UI).

    Raises:
        PreviewError on bad input.
    """
    # ---- INPUT VALIDATION ------------------------------------------------
    if year not in SUPPORTED_YEARS:
        raise PreviewError(
            f"Year {year!r} not supported in preview v1. Supported: {SUPPORTED_YEARS}"
        )
    if income_source not in INCOME_SOURCES:
        raise PreviewError(
            f"income_source {income_source!r} invalid. Expected one of: {INCOME_SOURCES}"
        )

    gross = _to_decimal(gross_income, "gross_income")
    sp = _to_decimal(sp_fee, "sp_fee")
    rent_per_month = _to_decimal(rental, "rental")

    if gross < 0:
        raise PreviewError("gross_income must be >= 0")
    if sp < 0:
        raise PreviewError("sp_fee must be >= 0")
    if rent_per_month < 0:
        raise PreviewError("rental must be >= 0")

    # ---- CURRENCY CONVERSION --------------------------------------------
    gross_lkr, fx_rate = _convert_to_lkr(gross, currency)
    sp_lkr, _ = _convert_to_lkr(sp, currency)
    rent_annual_lkr, _ = _convert_to_lkr(rent_per_month * Decimal("12"), currency)

    # ---- RELIEF ----------------------------------------------------------
    slabs = _get_year_slabs(year)
    personal_relief = Decimal(str(slabs.get("personal_relief_lkr", 0)))
    senior_relief = Decimal(str(slabs.get("senior_citizen_extra_relief_lkr", 0))) if senior else Decimal("0")
    relief_total = personal_relief + senior_relief

    # ---- NAIVE TAX (no FIESTA-claimed deductions, only statutory relief) -
    taxable_naive = max(gross_lkr - relief_total, Decimal("0"))
    brackets = slabs.get("brackets", [])
    bracket_breakdown_naive = _walk_brackets(taxable_naive, brackets)
    naive_tax = sum(
        (Decimal(b["tax_in_band_lkr"]) for b in bracket_breakdown_naive),
        Decimal("0"),
    )

    # ---- FIESTA-OPTIMISED TAX (statutory relief + defensible deductions) -
    # Defensible deductions = explicit sp_fee + annualised rental (home-office
    # portion) + heuristic for typical additional deductions FIESTA helps
    # document (home internet, training, software, insurance, etc.) capped by
    # FIESTA_DEDUCTION_CEILING_PCT.
    heuristic_rate = FIESTA_DEDUCTION_RATE[income_source]
    heuristic_deductions = gross_lkr * heuristic_rate
    explicit_deductions = sp_lkr + rent_annual_lkr * Decimal("0.5")  # 50% home-office assumption
    fiesta_deductions = heuristic_deductions + explicit_deductions

    # Cap total at FIESTA_DEDUCTION_CEILING_PCT of gross to keep numbers credible
    cap = gross_lkr * FIESTA_DEDUCTION_CEILING_PCT
    if fiesta_deductions > cap:
        fiesta_deductions = cap

    taxable_fiesta = max(gross_lkr - relief_total - fiesta_deductions, Decimal("0"))
    bracket_breakdown_fiesta = _walk_brackets(taxable_fiesta, brackets)
    fiesta_tax = sum(
        (Decimal(b["tax_in_band_lkr"]) for b in bracket_breakdown_fiesta),
        Decimal("0"),
    )

    # ---- DERIVED ---------------------------------------------------------
    saving = max(naive_tax - fiesta_tax, Decimal("0"))
    if naive_tax > 0:
        saving_pct = (saving / naive_tax * Decimal("100")).quantize(Decimal("0.1"))
    else:
        saving_pct = Decimal("0")

    if gross_lkr > 0:
        eff_naive = (naive_tax / gross_lkr * Decimal("100")).quantize(Decimal("0.01"))
        eff_fiesta = (fiesta_tax / gross_lkr * Decimal("100")).quantize(Decimal("0.01"))
    else:
        eff_naive = Decimal("0")
        eff_fiesta = Decimal("0")

    fiesta_deduction_pct_applied = (
        (fiesta_deductions / gross_lkr * Decimal("100")).quantize(Decimal("0.1"))
        if gross_lkr > 0
        else Decimal("0")
    )

    return {
        "year": year,
        "currency": currency.upper(),
        "fx_rate_used_lkr_per_unit": str(fx_rate),

        "gross_income_input": str(gross),
        "gross_income_lkr": str(_round_lkr(gross_lkr)),
        "income_source": income_source,
        "senior": bool(senior),

        "inputs": {
            "gross_income": str(gross),
            "currency": currency.upper(),
            "income_source": income_source,
            "sp_fee": str(sp),
            "rental_monthly": str(rent_per_month),
            "senior": bool(senior),
            "year": year,
        },

        "personal_relief_lkr": str(_round_lkr(personal_relief)),
        "senior_relief_lkr": str(_round_lkr(senior_relief)),
        "relief_total_lkr": str(_round_lkr(relief_total)),

        "fiesta_deductions_lkr": str(_round_lkr(fiesta_deductions)),
        "fiesta_deduction_pct_applied": str(fiesta_deduction_pct_applied),
        "fiesta_deductions_breakdown": {
            "service_provider_fees_lkr": str(_round_lkr(sp_lkr)),
            "home_office_rental_annual_lkr": str(_round_lkr(rent_annual_lkr * Decimal("0.5"))),
            "heuristic_additional_lkr": str(_round_lkr(max(fiesta_deductions - explicit_deductions, Decimal("0")))),
        },

        "taxable_income_naive_lkr": str(_round_lkr(taxable_naive)),
        "taxable_income_fiesta_lkr": str(_round_lkr(taxable_fiesta)),

        "bracket_breakdown_naive": bracket_breakdown_naive,
        "bracket_breakdown_fiesta": bracket_breakdown_fiesta,

        "naive_tax_lkr": str(_round_lkr(naive_tax)),
        "fiesta_tax_lkr": str(_round_lkr(fiesta_tax)),
        "saving_lkr": str(_round_lkr(saving)),
        "saving_pct": str(saving_pct),

        "effective_rate_naive_pct": str(eff_naive),
        "effective_rate_fiesta_pct": str(eff_fiesta),

        "ira_citations": IRA_CITATIONS,

        "disclaimer": (
            "PREVIEW — estimated based on the inputs above. Actual tax payable "
            "depends on your full income / deductions / WHT credits. FIESTA's "
            "documented deductions are estimates from a population of similar "
            "foreign-income filers; your individual case may save more or less."
        ),
    }


__all__ = [
    "quick_preview",
    "PreviewError",
    "SUPPORTED_YEARS",
    "DEFAULT_YEAR",
    "INCOME_SOURCES",
    "FX_FALLBACK_LKR_PER_UNIT",
    "IRA_CITATIONS",
]
