"""tests.tax.test_preview — coverage for fiesta.tax.preview.quick_preview.

Run via:
  cd C:/Users/mahes/AppData/Local/Temp/fiesta-s0
  python -m pytest tests/tax/test_preview.py -v

Covers:
  - happy paths for the 3 worked-example personas
  - currency conversion (USD/EUR/AUD -> LKR)
  - senior-citizen overlay
  - edges: zero income, negative income (rejected), very-high income, bad inputs
  - JSON-safe output (all values are str/dict/list/bool/int)
  - Latency: 100 iterations on the calc path should average sub-50ms
"""
from __future__ import annotations

import json
import sys
import time
from decimal import Decimal
from pathlib import Path

import pytest

# Add repo root to sys.path
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fiesta.tax import quick_preview, PreviewError  # noqa: E402
from fiesta.tax.preview import (  # noqa: E402
    FX_FALLBACK_LKR_PER_UNIT,
    SUPPORTED_YEARS,
    _walk_brackets,
    _to_decimal,
)


# ---------------------------------------------------------------------------
# Happy paths — the 3 worked examples
# ---------------------------------------------------------------------------

def test_example1_sl_developer_usd_80k():
    """Anjana D. — USD 80K foreign-income developer."""
    r = quick_preview(
        gross_income=80000, currency="USD", income_source="foreign",
        sp_fee=0, rental=0,
    )
    # USD 80,000 * 302 LKR = 24,160,000 LKR
    assert Decimal(r["gross_income_lkr"]) == Decimal("24160000")
    assert r["currency"] == "USD"
    assert r["income_source"] == "foreign"
    assert Decimal(r["personal_relief_lkr"]) == Decimal("1800000")
    assert Decimal(r["senior_relief_lkr"]) == Decimal("0")
    # Taxable income (naive) = 24.16M - 1.8M = 22.36M
    assert Decimal(r["taxable_income_naive_lkr"]) == Decimal("22360000")
    # FIESTA reduces this with documented deductions, must produce a positive saving
    assert Decimal(r["saving_lkr"]) > Decimal("0")
    assert Decimal(r["fiesta_tax_lkr"]) < Decimal(r["naive_tax_lkr"])


def test_example2_sl_designer_eur_45k():
    """Priya S. — EUR 45K mix-clients designer."""
    r = quick_preview(
        gross_income=45000, currency="EUR", income_source="foreign",
    )
    # EUR 45K * 328 LKR = 14,760,000 LKR
    assert Decimal(r["gross_income_lkr"]) == Decimal("14760000")
    assert r["currency"] == "EUR"
    assert Decimal(r["saving_lkr"]) > Decimal("0")


def test_example3_sl_accountant_aud_60k_mix():
    """Nuwan F. — AUD 60K mix employment + own clients."""
    r = quick_preview(
        gross_income=60000, currency="AUD", income_source="mix",
        rental=15000,  # AUD 15K/mo = silly large but tests rental path
    )
    # AUD 60K * 199 LKR = 11,940,000 LKR
    assert Decimal(r["gross_income_lkr"]) == Decimal("11940000")
    assert r["currency"] == "AUD"
    assert r["income_source"] == "mix"
    # Rental should appear in breakdown
    rental_lkr = Decimal(r["fiesta_deductions_breakdown"]["home_office_rental_annual_lkr"])
    assert rental_lkr > Decimal("0")


# ---------------------------------------------------------------------------
# Currency conversion
# ---------------------------------------------------------------------------

def test_lkr_native_no_conversion():
    r = quick_preview(gross_income=5000000, currency="LKR")
    assert Decimal(r["gross_income_lkr"]) == Decimal("5000000")
    assert Decimal(r["fx_rate_used_lkr_per_unit"]) == Decimal("1")


def test_unsupported_currency_raises():
    with pytest.raises(PreviewError, match="not supported"):
        quick_preview(gross_income=1000, currency="ZWL")


def test_currency_case_insensitive():
    r1 = quick_preview(gross_income=1000, currency="usd")
    r2 = quick_preview(gross_income=1000, currency="USD")
    assert r1["gross_income_lkr"] == r2["gross_income_lkr"]


# ---------------------------------------------------------------------------
# Senior-citizen overlay
# ---------------------------------------------------------------------------

def test_senior_relief_applied():
    """Senior citizen flag adds Rs 500K extra relief."""
    r_standard = quick_preview(gross_income=5000000, currency="LKR", senior=False)
    r_senior = quick_preview(gross_income=5000000, currency="LKR", senior=True)
    assert Decimal(r_standard["senior_relief_lkr"]) == Decimal("0")
    assert Decimal(r_senior["senior_relief_lkr"]) == Decimal("500000")
    # Senior has higher relief => lower taxable income => lower naive tax
    assert Decimal(r_senior["naive_tax_lkr"]) < Decimal(r_standard["naive_tax_lkr"])
    assert (
        Decimal(r_standard["naive_tax_lkr"]) - Decimal(r_senior["naive_tax_lkr"])
        > Decimal("0")
    )


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_zero_income():
    r = quick_preview(gross_income=0, currency="LKR")
    assert Decimal(r["naive_tax_lkr"]) == Decimal("0")
    assert Decimal(r["fiesta_tax_lkr"]) == Decimal("0")
    assert Decimal(r["saving_lkr"]) == Decimal("0")


def test_negative_income_rejected():
    with pytest.raises(PreviewError, match="gross_income must be >= 0"):
        quick_preview(gross_income=-1000, currency="LKR")


def test_negative_sp_fee_rejected():
    with pytest.raises(PreviewError, match="sp_fee must be >= 0"):
        quick_preview(gross_income=5000000, currency="LKR", sp_fee=-100)


def test_very_high_income_1b_lkr():
    """1 billion LKR income — must still compute, top band hit hard."""
    r = quick_preview(gross_income=1_000_000_000, currency="LKR")
    # Most of the income falls in the open-ended 36% band
    top_band = r["bracket_breakdown_naive"][-1]
    assert top_band["band_upper_lkr"] is None
    assert Decimal(top_band["income_in_band_lkr"]) > Decimal("900000000")
    assert Decimal(r["naive_tax_lkr"]) > Decimal("350000000")
    # FIESTA cap (30% of gross) limits deduction inflation at high income
    assert Decimal(r["fiesta_deductions_lkr"]) <= Decimal("1000000000") * Decimal("0.30")


def test_below_relief_threshold_no_tax():
    """Income at or below personal relief = no tax."""
    r = quick_preview(gross_income=1800000, currency="LKR")
    assert Decimal(r["taxable_income_naive_lkr"]) == Decimal("0")
    assert Decimal(r["naive_tax_lkr"]) == Decimal("0")


def test_just_above_relief_only_band1():
    """Income Rs 2M = 200K taxable, all in band 1 (6%)."""
    r = quick_preview(gross_income=2000000, currency="LKR")
    assert Decimal(r["taxable_income_naive_lkr"]) == Decimal("200000")
    assert Decimal(r["naive_tax_lkr"]) == Decimal("12000")  # 200K * 6%


def test_invalid_income_source_rejected():
    with pytest.raises(PreviewError, match="income_source"):
        quick_preview(gross_income=5000000, currency="LKR", income_source="alien")


def test_unsupported_year_rejected():
    with pytest.raises(PreviewError, match="not supported in preview"):
        quick_preview(gross_income=5000000, currency="LKR", year="99_00")


def test_string_inputs_accepted():
    """Web form will POST strings — coercion must work."""
    r = quick_preview(
        gross_income="5,000,000", currency="LKR", sp_fee="100,000", rental="50000"
    )
    assert Decimal(r["gross_income_lkr"]) == Decimal("5000000")


def test_missing_optional_fields():
    """sp_fee and rental are optional — defaults to 0."""
    r = quick_preview(gross_income=5000000, currency="LKR")
    breakdown = r["fiesta_deductions_breakdown"]
    assert Decimal(breakdown["service_provider_fees_lkr"]) == Decimal("0")
    assert Decimal(breakdown["home_office_rental_annual_lkr"]) == Decimal("0")


def test_bool_for_numeric_field_rejected():
    with pytest.raises(PreviewError, match="must be numeric, got bool"):
        quick_preview(gross_income=True, currency="LKR")


# ---------------------------------------------------------------------------
# Output shape contract — JSON serialisable, all fields present
# ---------------------------------------------------------------------------

def test_output_is_json_serialisable():
    r = quick_preview(gross_income=80000, currency="USD")
    # Round-trip through JSON
    s = json.dumps(r)
    r2 = json.loads(s)
    assert r2["gross_income_lkr"] == r["gross_income_lkr"]


REQUIRED_FIELDS = [
    "year", "currency", "fx_rate_used_lkr_per_unit",
    "gross_income_input", "gross_income_lkr", "income_source", "senior",
    "inputs", "personal_relief_lkr", "senior_relief_lkr", "relief_total_lkr",
    "fiesta_deductions_lkr", "fiesta_deduction_pct_applied",
    "fiesta_deductions_breakdown",
    "taxable_income_naive_lkr", "taxable_income_fiesta_lkr",
    "bracket_breakdown_naive", "bracket_breakdown_fiesta",
    "naive_tax_lkr", "fiesta_tax_lkr", "saving_lkr", "saving_pct",
    "effective_rate_naive_pct", "effective_rate_fiesta_pct",
    "ira_citations", "disclaimer",
]

def test_output_contract_all_fields_present():
    r = quick_preview(gross_income=80000, currency="USD")
    for f in REQUIRED_FIELDS:
        assert f in r, f"output missing required field: {f}"


def test_bracket_breakdown_shape_constant():
    """Bracket breakdown always has same N entries (one per band in slabs.yaml)."""
    r_low = quick_preview(gross_income=2000000, currency="LKR")
    r_high = quick_preview(gross_income=50000000, currency="LKR")
    assert len(r_low["bracket_breakdown_naive"]) == len(r_high["bracket_breakdown_naive"])
    assert len(r_low["bracket_breakdown_naive"]) == 5  # 25/26 has 5 bands


def test_saving_never_negative():
    """For any positive income, saving must be >= 0."""
    for gi in [1000, 1000000, 5000000, 24000000, 100000000]:
        r = quick_preview(gross_income=gi, currency="LKR")
        assert Decimal(r["saving_lkr"]) >= Decimal("0"), f"saving negative at gross {gi}"


def test_rate_display_integer_pct():
    """Rate display should show '6%', '30%' etc — not '3E+1%'."""
    r = quick_preview(gross_income=10000000, currency="LKR")
    for band in r["bracket_breakdown_naive"]:
        d = band["rate_pct_display"]
        # Display must be human-readable; no scientific notation
        assert "E" not in d.upper(), f"scientific notation leaked: {d!r}"


# ---------------------------------------------------------------------------
# Latency target — sub-100ms perceived, sub-50ms calc itself
# ---------------------------------------------------------------------------

def test_latency_100_iterations_under_50ms_avg():
    """100 iterations of quick_preview should average sub-50ms (excludes network).

    The frontend debounces input changes by 300ms, so 50ms compute leaves
    plenty of budget for network round-trip and DOM render.
    """
    # Warm the LRU cache first
    quick_preview(gross_income=80000, currency="USD")

    n = 100
    start = time.perf_counter()
    for _ in range(n):
        quick_preview(
            gross_income=80000, currency="USD", income_source="foreign",
            sp_fee=2000, rental=750,
        )
    elapsed = time.perf_counter() - start
    avg_ms = (elapsed / n) * 1000
    print(f"\n  Avg quick_preview latency: {avg_ms:.2f}ms over {n} iterations")
    assert avg_ms < 50, f"avg {avg_ms:.2f}ms exceeds 50ms budget"


# ---------------------------------------------------------------------------
# Walk-brackets unit tests (internal helper, but contract-critical)
# ---------------------------------------------------------------------------

def test_walk_brackets_zero_income():
    """Zero income yields zero tax in every band."""
    slabs = [
        {"up_to": 1000000, "rate": Decimal("0.06")},
        {"up_to": None, "rate": Decimal("0.36")},
    ]
    out = _walk_brackets(Decimal("0"), slabs)
    assert len(out) == 2
    assert all(Decimal(b["tax_in_band_lkr"]) == Decimal("0") for b in out)


def test_walk_brackets_exact_boundary():
    """Income exactly at the band boundary fills band 1 completely, band 2 empty."""
    slabs = [
        {"up_to": 1000000, "rate": Decimal("0.06")},
        {"up_to": 2000000, "rate": Decimal("0.18")},
        {"up_to": None, "rate": Decimal("0.36")},
    ]
    out = _walk_brackets(Decimal("1000000"), slabs)
    assert Decimal(out[0]["income_in_band_lkr"]) == Decimal("1000000")
    assert Decimal(out[0]["tax_in_band_lkr"]) == Decimal("60000")
    assert Decimal(out[1]["income_in_band_lkr"]) == Decimal("0")


def test_to_decimal_string_with_commas():
    """Form input 'Rs 5,000,000' -> Decimal('5000000')."""
    assert _to_decimal("5,000,000", "x") == Decimal("5000000")
    assert _to_decimal("  100  ", "x") == Decimal("100")
    assert _to_decimal("", "x") == Decimal("0")
    assert _to_decimal(None, "x") == Decimal("0")


# ---------------------------------------------------------------------------
# FX rate sanity
# ---------------------------------------------------------------------------

def test_fx_rates_are_decimal():
    for cur, rate in FX_FALLBACK_LKR_PER_UNIT.items():
        assert isinstance(rate, Decimal), f"{cur} rate is {type(rate)}, must be Decimal"
        assert rate > 0, f"{cur} rate must be positive"
