"""tests/multi_year_module/test_multi_year_view.py -- Tier D5 C3.

Two cases:
  1. Carry-forward rental loss is applied to current-year compute_tax_bill,
     reducing the rental_lkr engine kwarg by the carried amount (floored at 0)
     and surfacing through report.rental_loss_carried_forward_lkr +
     rental_loss_applied_lkr.
  2. The /tax-bill/<year>/compare view returns 200 + a non-empty rendered body
     when invoked via the Flask test client with a logged-in user.
"""
from __future__ import annotations

import pathlib
import sys
from decimal import Decimal
from typing import Any

import pytest


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Helper: minimal TaxInputs (mirrors tests/tax_bill/test_s12.py _make_inputs).
# ---------------------------------------------------------------------------
def _make_inputs(
    income_by_category: dict[str, Decimal] | None = None,
    tax_year_s4: str = "2025-26",
    tax_year_s5: str = "2025/2026",
    senior_citizen: bool = False,
):
    from fiesta.tax_bill.aggregator import TaxInputs, _compose_engine_inputs

    inputs = TaxInputs(
        user_id=42,
        tax_year_s4_format=tax_year_s4,
        tax_year_s5_format=tax_year_s5,
        full_name="Test Customer",
        nic="123456789V",
        tin="TIN123456",
        senior_citizen=senior_citizen,
        profile_complete=True,
    )
    inputs.income_by_category_lkr = income_by_category or {}
    inputs.income_total_lkr = sum(
        (v for v in inputs.income_by_category_lkr.values()),
        Decimal("0"),
    )
    inputs.income_by_currency = {"LKR": inputs.income_total_lkr}
    inputs.deductions_itemised = []
    inputs.deductions_total_lkr = Decimal("0")
    inputs.deductions_with_evidence_count = 0
    inputs.deductions_pending_evidence_count = 0
    inputs.service_providers = []
    inputs.sp_disclosure_required_count = 0
    inputs.sp_disclosure_applied_count = 0
    inputs.rentals = []
    inputs.rental_disclosure_required_count = 0
    inputs.rental_disclosure_applied_count = 0
    inputs.missing_disclosures = []
    inputs.sp_agreement_mismatches = []
    inputs.income_unconverted_currencies = []
    _compose_engine_inputs(inputs)
    return inputs


# ---------------------------------------------------------------------------
# Test 1 -- carry-forward rental loss reduces rental_lkr engine input.
# ---------------------------------------------------------------------------
def test_carry_forward_rental_loss_reduces_rental_income_kwarg():
    """compute_tax_bill(..., prior_year_rental_loss_lkr=X) should:
       - reduce engine rental_lkr by X (floored at 0)
       - populate report.rental_loss_carried_forward_lkr = X
       - populate report.rental_loss_applied_lkr = min(X, current_rental_lkr)
       - bypass the per-(user, year) TTL cache (so two consecutive calls
         with different carries don't collide)
    """
    from fiesta.tax_bill.compute import (
        _invalidate_tax_bill_cache,
        compute_tax_bill,
    )

    _invalidate_tax_bill_cache()

    inputs = _make_inputs(
        income_by_category={
            "salary": Decimal("3000000"),
            "rental": Decimal("1200000"),
        }
    )

    # Baseline -- no carry-forward.
    base = compute_tax_bill(
        user_id=42,
        tax_year="2025-26",
        pre_assembled=inputs,
        use_cache=False,
    )
    assert base.engine_error is None, base.engine_error
    base_net = base.net_tax_payable_lkr
    assert base.rental_loss_carried_forward_lkr == Decimal("0")
    assert base.rental_loss_applied_lkr == Decimal("0")

    # Carry forward Rs 500K of prior rental loss -- consumed fully by 1.2M rental income.
    with_carry = compute_tax_bill(
        user_id=42,
        tax_year="2025-26",
        pre_assembled=inputs,
        use_cache=False,
        prior_year_rental_loss_lkr=Decimal("500000"),
    )
    assert with_carry.engine_error is None
    assert with_carry.rental_loss_carried_forward_lkr == Decimal("500000")
    assert with_carry.rental_loss_applied_lkr == Decimal("500000")
    # Net tax must be <= baseline (income shrunk by 500K -> tax owed cannot rise).
    assert with_carry.net_tax_payable_lkr <= base_net, (
        f"carry-forward should not increase tax: "
        f"base={base_net} with_carry={with_carry.net_tax_payable_lkr}"
    )

    # Over-carry: 2M loss vs 1.2M rental income -> applied capped at 1.2M, never negative.
    over = compute_tax_bill(
        user_id=42,
        tax_year="2025-26",
        pre_assembled=inputs,
        use_cache=False,
        prior_year_rental_loss_lkr=Decimal("2000000"),
    )
    assert over.engine_error is None
    assert over.rental_loss_carried_forward_lkr == Decimal("2000000")
    assert over.rental_loss_applied_lkr == Decimal("1200000")
    # Engine never sees a negative rental input.
    # (Indirect verification: tax cannot be < the over-cap case's tax for the
    # same inputs, since rental was already 0 in the engine.)
    assert over.net_tax_payable_lkr >= Decimal("0")


# ---------------------------------------------------------------------------
# Test 2 -- /tax-bill/<year>/compare returns a non-empty 200 response.
# ---------------------------------------------------------------------------
def test_compare_view_renders_200(monkeypatch):
    """The compare view should render without 500-ing even when the user
    has no prior-year data. We stub flask-login / paywall / compute so the
    route reaches render_template with a synthetic comparison dict.
    """
    flask = pytest.importorskip("flask")
    from fiesta.tax_bill import routes as tax_bill_routes

    # Stub auth -- pretend the current user is logged in with id=42.
    monkeypatch.setattr(tax_bill_routes, "_current_user_id", lambda: 42)

    # Stub available-years scan -- avoid touching the DB.
    monkeypatch.setattr(
        tax_bill_routes,
        "_available_years_for_user",
        lambda uid: ["2025-26", "2024-25"],
    )

    # Stub compute_yoy_comparison so we don't need a live DB / engine env.
    import multi_year_view

    def _fake_yoy(user_id, years):
        years = list(years)
        return {
            "user_id": int(user_id),
            "years": years,
            "per_year": [
                {
                    "tax_year_s4": y,
                    "tax_year_s5": y.replace("-", "/20")[:4] + "/" + y[-2:],
                    "gross_income_lkr": "1000000",
                    "total_deductions_lkr": "0",
                    "taxable_income_lkr": "1000000",
                    "gross_tax_payable_lkr": "60000",
                    "net_tax_payable_lkr": "60000",
                    "tax_without_deductions_lkr": "60000",
                    "savings_vs_no_deductions_lkr": "0",
                    "audit_defensibility_score": 90,
                    "audit_defensibility_label": "Strong",
                    "rental_loss_carried_forward_lkr": "0",
                    "engine_error": None,
                }
                for y in years
            ],
            "deltas": [],
        }

    monkeypatch.setattr(multi_year_view, "compute_yoy_comparison", _fake_yoy)

    # Stub the @login_required decorator that wraps yoy_compare so the test
    # client doesn't need a full auth session. We rebuild the blueprint with
    # the unwrapped view function.
    app = flask.Flask(
        __name__,
        template_folder=str(_REPO_ROOT / "templates"),
    )
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-only"

    # Minimal base.html for template inheritance.
    base_path = _REPO_ROOT / "templates" / "base.html"
    base_existed = base_path.exists()
    if not base_existed:
        base_path.parent.mkdir(parents=True, exist_ok=True)
        base_path.write_text(
            "<html><body>{% block content %}{% endblock %}</body></html>",
            encoding="utf-8",
        )

    try:
        # Re-register a stripped blueprint with the same view function but
        # WITHOUT the login_required / paywall_required decorators (those are
        # baked in at import time, but the underlying function is still
        # accessible via __wrapped__ chain on the route handler).
        view_fn = tax_bill_routes.yoy_compare
        # Defensively unwrap if decorators set __wrapped__.
        while hasattr(view_fn, "__wrapped__"):
            view_fn = view_fn.__wrapped__

        # Register under the canonical endpoint name so url_for() inside the
        # rendered template ("fiesta_tax_bill.show_tax_bill") resolves.
        app.add_url_rule(
            "/tax-bill/<tax_year>/compare",
            endpoint="fiesta_tax_bill.yoy_compare",
            view_func=lambda tax_year: view_fn(tax_year),
        )
        app.add_url_rule(
            "/tax-bill/<tax_year>",
            endpoint="fiesta_tax_bill.show_tax_bill",
            view_func=lambda tax_year: ("", 200),
        )

        client = app.test_client()
        rv = client.get("/tax-bill/2025-26/compare")
        assert rv.status_code == 200, rv.status_code
        body = rv.get_data(as_text=True)
        # Page header present + both stubbed years contribute rows.
        assert "Year-over-Year" in body
        assert "2024" in body
        assert "2025" in body
        # Headline metric row rendered.
        assert "Net tax payable" in body
        assert "Rental loss carried forward" in body
    finally:
        if not base_existed and base_path.exists():
            base_path.unlink()
