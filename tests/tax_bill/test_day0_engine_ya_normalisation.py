"""tests/tax_bill/test_day0_engine_ya_normalisation.py — Day-0 P0 BLOCKER C2.

Audit 2026-05-26 found: /tax-bill/2025-2026/breakdown returned
`engine_error="Tax engine import failed or unsupported tax year."` and every
tax bill computed to Rs 0.

Root cause: the YA normaliser's regex fallback only matched "/" separators
(`YYYY/YY` and `YYYY/YYYY`), and the explicit alias map didn't include the
dash-style `YYYY-YYYY` form. Flask routes a URL like
/tax-bill/2025-2026/breakdown to the breakdown view with tax_year="2025-2026".
That string then passed through normalise_tax_year_to_s4_format → no match →
returned `"2025-2026"` unchanged → canonical_tax_year_enum looked up
"2025-2026" in the {"2025-26", "2024-25"} dict → got None → engine_error.

Day-0 Fix 2026-05-27:
  - Add "2025-2026" / "2024-2025" to the explicit alias maps.
  - Extend the regex fallback to accept BOTH "/" and "-" as separators so
    future-year dash-style URLs (e.g. /tax-bill/2026-2027) get the same
    treatment without further alias-map edits.
  - These tests pin the contract so the regression cannot recur.

Run:
    DATABASE_URL=sqlite:///:memory: python -m pytest tests/tax_bill/test_day0_engine_ya_normalisation.py -v
"""
from __future__ import annotations

import pathlib
import sys
from decimal import Decimal

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Pure-function tests: the YA normaliser + canonical-enum lookup.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ya_input,expected_s4",
    [
        # The form that triggered the audit incident.
        ("2025-2026", "2025-26"),
        ("2024-2025", "2024-25"),
        # Forms already supported pre-fix (regression guard).
        ("2025-26", "2025-26"),
        ("2025/26", "2025-26"),
        ("2025/2026", "2025-26"),
        ("25_26", "2025-26"),
        ("Y25_26", "2025-26"),
        ("2024-25", "2024-25"),
        ("2024/25", "2024-25"),
        ("2024/2025", "2024-25"),
        # Future-year dash-style: regex must accept this even though the
        # engine doesn't have brackets for it (the route layer redirects
        # unsupported years; the normaliser is just shape-shifting strings).
        ("2026-2027", "2026-27"),
        ("2026/27", "2026-27"),
    ],
)
def test_normalise_to_s4_accepts_dash_yyyy_yyyy(ya_input, expected_s4):
    """Regression guard for the C2 audit failure — /tax-bill/2025-2026/...
    must normalise to "2025-26" cleanly."""
    from fiesta.tax_bill.aggregator import normalise_tax_year_to_s4_format

    assert normalise_tax_year_to_s4_format(ya_input) == expected_s4, (
        f"Expected {ya_input!r} to normalise to {expected_s4!r}; got "
        f"{normalise_tax_year_to_s4_format(ya_input)!r}"
    )


@pytest.mark.parametrize(
    "ya_input,expected_s5",
    [
        ("2025-2026", "2025/2026"),
        ("2024-2025", "2024/2025"),
        ("2025-26", "2025/2026"),
        ("2025/26", "2025/2026"),
        ("2025/2026", "2025/2026"),
        ("2024-25", "2024/2025"),
        ("2024/25", "2024/2025"),
        ("2024/2025", "2024/2025"),
        # Future-year regex coverage:
        ("2026-2027", "2026/2027"),
        ("2026/27", "2026/2027"),
    ],
)
def test_normalise_to_s5_accepts_dash_yyyy_yyyy(ya_input, expected_s5):
    from fiesta.tax_bill.aggregator import normalise_tax_year_to_s5_format

    assert normalise_tax_year_to_s5_format(ya_input) == expected_s5


def test_canonical_tax_year_enum_for_2025_2026():
    """The form that the audit hit MUST resolve to the engine's Y25_26 enum."""
    from fiesta.tax_bill.aggregator import canonical_tax_year_enum
    from fiesta.tax.types import TaxYear

    assert canonical_tax_year_enum("2025-2026") == TaxYear.Y25_26
    assert canonical_tax_year_enum("2024-2025") == TaxYear.Y24_25


def test_canonical_tax_year_enum_returns_none_for_unsupported():
    """2026-27 has no engine brackets yet; the normaliser still accepts the
    shape, but the enum lookup correctly returns None so the route layer can
    redirect to a supported year. Regression guard."""
    from fiesta.tax_bill.aggregator import canonical_tax_year_enum

    assert canonical_tax_year_enum("2026-2027") is None
    assert canonical_tax_year_enum("2026-27") is None
    assert canonical_tax_year_enum("2026/27") is None


# ---------------------------------------------------------------------------
# Integration test: compute_tax_bill end-to-end via the previously-broken
# tax_year input ("2025-2026") returns a non-zero bill.
# ---------------------------------------------------------------------------


def test_tax_engine_produces_nonzero_bill_for_2025_2026_dash_form():
    """The C2 audit smoke test: given a user with Rs 5,000,000 employment
    income and the dash-style YA "2025-2026", the engine must compute a
    non-zero bill rather than returning engine_error.

    Without the Day-0 fix this returned:
        engine_error = "Tax engine import failed or unsupported tax year."
        gross_tax_payable_lkr = 0
        net_tax_payable_lkr = 0

    With the fix, the engine produces the 25/26 dual-track result.
    """
    from fiesta.tax_bill.compute import compute_tax_bill
    from fiesta.tax_bill.aggregator import TaxInputs

    inputs = TaxInputs(
        user_id=42,
        tax_year_s4_format="2025-26",
        tax_year_s5_format="2025/2026",
        profile_complete=True,
    )
    inputs.engine_income_kwargs = {
        "employment_lkr": Decimal("5000000"),
    }
    inputs.engine_deductions_kwargs = {
        "solar_investment_lkr": Decimal("0"),
        "rent_relief_lkr": Decimal("0"),
        "expenditure_relief_lkr": Decimal("0"),
    }

    # Pass the previously-broken form. Pre-fix this routed to enum=None.
    report = compute_tax_bill(
        user_id=42,
        tax_year="2025-2026",
        pre_assembled=inputs,
    )

    assert report.engine_error is None, (
        f"Engine should not error for 2025-2026 dash form; got "
        f"engine_error={report.engine_error!r}"
    )
    assert report.gross_income_lkr > 0, (
        f"Gross income must be non-zero for a 5M-employment user; got "
        f"{report.gross_income_lkr}"
    )
    assert report.taxable_income_lkr > 0, (
        f"Taxable income must be non-zero; got {report.taxable_income_lkr}"
    )
    assert report.net_tax_payable_lkr > 0, (
        f"Net tax must be non-zero for a 5M-employment user; got "
        f"{report.net_tax_payable_lkr}"
    )


def test_tax_engine_2025_dash_26_short_form_still_works():
    """Regression guard: the existing short-form input must keep working
    exactly as it did before the Day-0 fix."""
    from fiesta.tax_bill.compute import compute_tax_bill
    from fiesta.tax_bill.aggregator import TaxInputs

    inputs = TaxInputs(
        user_id=42,
        tax_year_s4_format="2025-26",
        tax_year_s5_format="2025/2026",
        profile_complete=True,
    )
    inputs.engine_income_kwargs = {
        "employment_lkr": Decimal("5000000"),
    }
    inputs.engine_deductions_kwargs = {
        "solar_investment_lkr": Decimal("0"),
        "rent_relief_lkr": Decimal("0"),
        "expenditure_relief_lkr": Decimal("0"),
    }

    report = compute_tax_bill(
        user_id=42,
        tax_year="2025-26",
        pre_assembled=inputs,
    )

    assert report.engine_error is None
    assert report.net_tax_payable_lkr > 0


def test_dash_and_slash_forms_produce_identical_bill():
    """The dash and slash forms of the same YA must produce byte-identical
    headline numbers — they're meant to be aliases."""
    from fiesta.tax_bill.compute import compute_tax_bill
    from fiesta.tax_bill.aggregator import TaxInputs

    def _make_inputs():
        inputs = TaxInputs(
            user_id=42,
            tax_year_s4_format="2025-26",
            tax_year_s5_format="2025/2026",
            profile_complete=True,
        )
        inputs.engine_income_kwargs = {
            "employment_lkr": Decimal("5000000"),
        }
        inputs.engine_deductions_kwargs = {
            "solar_investment_lkr": Decimal("0"),
            "rent_relief_lkr": Decimal("0"),
            "expenditure_relief_lkr": Decimal("0"),
        }
        return inputs

    report_dash_long = compute_tax_bill(
        user_id=42, tax_year="2025-2026", pre_assembled=_make_inputs(), use_cache=False,
    )
    report_slash_long = compute_tax_bill(
        user_id=42, tax_year="2025/2026", pre_assembled=_make_inputs(), use_cache=False,
    )
    report_dash_short = compute_tax_bill(
        user_id=42, tax_year="2025-26", pre_assembled=_make_inputs(), use_cache=False,
    )
    report_slash_short = compute_tax_bill(
        user_id=42, tax_year="2025/26", pre_assembled=_make_inputs(), use_cache=False,
    )

    headline = lambda r: (
        r.gross_income_lkr,
        r.taxable_income_lkr,
        r.gross_tax_payable_lkr,
        r.net_tax_payable_lkr,
    )

    assert headline(report_dash_long) == headline(report_slash_long)
    assert headline(report_dash_long) == headline(report_dash_short)
    assert headline(report_dash_long) == headline(report_slash_short)
    # All four engine_error fields must be None — same alias, same enum.
    for r in (report_dash_long, report_slash_long,
              report_dash_short, report_slash_short):
        assert r.engine_error is None, (
            f"Alias should resolve cleanly; got engine_error={r.engine_error!r}"
        )
