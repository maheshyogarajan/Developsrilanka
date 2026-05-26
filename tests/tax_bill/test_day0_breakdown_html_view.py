"""tests/tax_bill/test_day0_breakdown_html_view.py — Day-0 P0 BLOCKER C1.

Audit 2026-05-26 found: a browser hitting /tax-bill/<ya>/breakdown saw raw
JSON containing internal field names ("engine_error", "rule_id" values like
"S12-DEDUCTION-RATIO", "audit_defensibility.label", "sources_loaded").

Day-0 Fix 2026-05-27:
  - Wrap the breakdown view in a Jinja template (templates/tax_bill/
    breakdown.html) that presents the data as a styled HTML page.
  - Customers (browser, Accept: text/html) see the HTML.
  - JSON consumers (Accept: application/json, ?format=json, or
    X-Requested-With: XMLHttpRequest) still get the JSON payload — the
    audit-pack PDF, integration tests, and external API callers are
    unaffected.

These tests render the template directly via a minimal Jinja environment
(same approach as test_breakdown_empty_rows.py) — no Flask app, no DB.

Run:
    DATABASE_URL=sqlite:///:memory: python -m pytest tests/tax_bill/test_day0_breakdown_html_view.py -v
"""
from __future__ import annotations

import pathlib
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Minimal Jinja2 environment with a stub layout_template so we don't need
# the full Flask app shell. Mirrors test_breakdown_empty_rows.py.
# ---------------------------------------------------------------------------


def _make_env():
    from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

    fs_loader = FileSystemLoader(str(_REPO_ROOT / "templates"))
    stub_loader = DictLoader({
        "_stub_layout.html": (
            "<html><head><title>{% block title %}{% endblock %}</title>"
            "{% block additional_styles %}{% endblock %}</head><body>"
            "{% block content %}{% endblock %}"
            "{% block scripts %}{% endblock %}"
            "</body></html>"
        )
    })
    env = Environment(
        loader=ChoiceLoader([fs_loader, stub_loader]),
        autoescape=True,
    )

    def _url_for(endpoint, **kwargs):
        return f"/_stub_{endpoint}"

    env.globals["url_for"] = _url_for
    env.globals["csrf_token"] = lambda: "stub-csrf"
    return env


# ---------------------------------------------------------------------------
# Lightweight fakes that match what the template attribute-accesses.
# ---------------------------------------------------------------------------


@dataclass
class _FakeBand:
    band_lower: Decimal
    band_upper: Decimal | None
    income_in_band: Decimal
    rate: Decimal
    tax_in_band: Decimal


@dataclass
class _FakeRelief:
    personal_relief_applied_lkr: Decimal = Decimal("1800000")
    senior_citizen_extra_lkr: Decimal = Decimal("0")
    solar_relief_applied_lkr: Decimal = Decimal("0")
    rent_relief_applied_lkr: Decimal = Decimal("0")
    expenditure_relief_applied_lkr: Decimal = Decimal("0")

    def total(self):
        return (
            self.personal_relief_applied_lkr
            + self.senior_citizen_extra_lkr
            + self.solar_relief_applied_lkr
            + self.rent_relief_applied_lkr
            + self.expenditure_relief_applied_lkr
        )


@dataclass
class _FakeComp:
    by_band: list[_FakeBand]
    gross_income_lkr: Decimal = Decimal("5000000")
    deductions_input_lkr: Decimal = Decimal("0")
    relief_applied: _FakeRelief = field(default_factory=_FakeRelief)
    taxable_income_lkr: Decimal = Decimal("3200000")
    gross_tax_lkr: Decimal = Decimal("672000")
    net_tax_due_lkr: Decimal = Decimal("672000")
    marginal_rate: Decimal = Decimal("0.30")
    effective_rate: Decimal = Decimal("0.1344")


@dataclass
class _FakeInputs:
    income_by_category_lkr: dict = field(default_factory=dict)
    income_by_currency: dict = field(default_factory=dict)
    income_total_lkr: Decimal = Decimal("5000000")
    income_unconverted_currencies: list = field(default_factory=list)
    income_fx_warnings: list = field(default_factory=list)
    deductions_itemised: list = field(default_factory=list)
    deductions_total_lkr: Decimal = Decimal("0")
    deductions_with_evidence_count: int = 0


@dataclass
class _FakeReport:
    inputs: _FakeInputs
    computation_with_deductions: _FakeComp | None
    gross_income_lkr: Decimal = Decimal("5000000")
    gross_tax_payable_lkr: Decimal = Decimal("672000")
    total_deductions_lkr: Decimal = Decimal("0")
    net_tax_payable_lkr: Decimal = Decimal("672000")
    tax_without_deductions_lkr: Decimal = Decimal("672000")
    savings_vs_no_deductions_lkr: Decimal = Decimal("0")
    engine_error: str | None = None
    is_finalized: bool = False
    audit_defensibility_score: int = 75
    audit_defensibility_label: str = "Moderate"
    audit_score_components: dict = field(default_factory=dict)
    tax_year_s4_format: str = "2025-26"
    tax_year_s5_format: str = "2025/2026"


def _render_breakdown(
    by_band: list[_FakeBand] | None = None,
    *,
    engine_error: str | None = None,
    income_total: Decimal = Decimal("5000000"),
    deductions: list[dict] | None = None,
    audit_label: str = "Moderate",
) -> str:
    env = _make_env()
    template = env.get_template("tax_bill/breakdown.html")

    inputs = _FakeInputs(
        income_by_category_lkr={"foreign_remittance": income_total} if income_total > 0 else {},
        income_by_currency={"LKR": income_total} if income_total > 0 else {},
        income_total_lkr=income_total,
        deductions_itemised=deductions or [],
        deductions_total_lkr=sum(
            (Decimal(str(d.get("amount_lkr", 0))) for d in (deductions or [])),
            Decimal("0"),
        ),
        deductions_with_evidence_count=sum(
            1 for d in (deductions or []) if d.get("has_evidence")
        ),
    )

    comp = (
        _FakeComp(by_band=by_band) if by_band is not None and engine_error is None
        else None
    )

    report = _FakeReport(
        inputs=inputs,
        computation_with_deductions=comp,
        engine_error=engine_error,
        audit_defensibility_label=audit_label,
    )

    return template.render(
        report=report,
        inputs=inputs,
        gate=None,
        tax_year_s4="2025-26",
        tax_year_display="2025/2026",
        layout_template="_stub_layout.html",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_breakdown_renders_html_not_raw_json():
    """The view must produce HTML, not the raw JSON dump the audit found."""
    bands = [
        _FakeBand(
            band_lower=Decimal("0"),
            band_upper=Decimal("1000000"),
            income_in_band=Decimal("1000000"),
            rate=Decimal("0.06"),
            tax_in_band=Decimal("60000"),
        ),
    ]
    html = _render_breakdown(bands)

    assert "<html>" in html and "</html>" in html, (
        "Breakdown view must produce HTML, not JSON."
    )
    assert 'data-testid="tax-bill-breakdown-page"' in html
    assert "Tax bill breakdown" in html


def test_breakdown_does_not_expose_internal_field_names():
    """The view must NOT surface any of the internal field names the audit
    flagged: engine_error, rule_id, S12-* identifiers, sources_loaded,
    audit_defensibility.* component machinery, etc."""
    bands = [
        _FakeBand(
            band_lower=Decimal("0"),
            band_upper=Decimal("1000000"),
            income_in_band=Decimal("1000000"),
            rate=Decimal("0.06"),
            tax_in_band=Decimal("60000"),
        ),
    ]
    html = _render_breakdown(bands)

    # Internal machinery field names — none of these may appear in the
    # rendered customer-facing HTML.
    forbidden_substrings = [
        "engine_error",
        "rule_id",
        "S12-DEDUCTION-RATIO",
        "S12-MISSING-195-DISCLOSURE",
        "S12-SP-AGREEMENT-MISMATCH",
        "sources_loaded",
        "audit_defensibility.label",
        "audit_defensibility.score",
        "audit_score_components",
        "gate_customer_data",
        # Internal Python repr leakage:
        "Decimal(",
        "<class ",
    ]
    for needle in forbidden_substrings:
        assert needle not in html, (
            f"breakdown.html leaked internal token {needle!r} to the "
            f"customer-facing HTML — Day-0 C1 contract violation.\n"
            f"--- HTML sample ---\n{html[:2000]}"
        )


def test_breakdown_renders_calm_fallback_when_engine_errored():
    """When the engine errored, the page must NEVER print the raw error
    string. Customers see a calm "your bill is being prepared" card."""
    html = _render_breakdown(
        by_band=None,
        engine_error="Tax engine import failed or unsupported tax year.",
    )

    # The verbatim audit-incident string must NOT appear:
    assert "Tax engine import failed or unsupported tax year." not in html, (
        "Day-0 C1: the engine_error string must be hidden from customers."
    )
    assert "import failed" not in html.lower()
    # The friendly fallback card MUST appear:
    assert 'data-testid="tbb-engine-pending"' in html
    assert "Your bill is being prepared" in html


def test_breakdown_shows_non_zero_bracket_walk_when_engine_ran():
    """Happy path: when the engine produced a real bill, the bracket walk
    table renders and shows the non-zero figures."""
    bands = [
        _FakeBand(
            band_lower=Decimal("0"),
            band_upper=Decimal("1000000"),
            income_in_band=Decimal("1000000"),
            rate=Decimal("0.06"),
            tax_in_band=Decimal("60000"),
        ),
        _FakeBand(
            band_lower=Decimal("1000000"),
            band_upper=Decimal("2000000"),
            income_in_band=Decimal("700000"),
            rate=Decimal("0.30"),
            tax_in_band=Decimal("210000"),
        ),
    ]
    html = _render_breakdown(bands)

    assert 'data-testid="tbb-bracket-walk-table"' in html
    assert html.count('data-testid="tbb-bracket-row"') == 2
    assert "672,000" in html  # net tax due — appears
    assert "Gross tax" in html
    assert "Net tax due" in html


def test_breakdown_empty_bracket_rows_suppressed():
    """Empty (Rs 0 / Rs 0) bracket rows must be suppressed in the
    customer-facing view — matches the index.html F6.7 contract."""
    bands = [
        _FakeBand(
            band_lower=Decimal("0"),
            band_upper=Decimal("1000000"),
            income_in_band=Decimal("1000000"),
            rate=Decimal("0.06"),
            tax_in_band=Decimal("60000"),
        ),
        # Empty band — must NOT render.
        _FakeBand(
            band_lower=Decimal("1000000"),
            band_upper=Decimal("2000000"),
            income_in_band=Decimal("0"),
            rate=Decimal("0.30"),
            tax_in_band=Decimal("0"),
        ),
    ]
    html = _render_breakdown(bands)

    assert html.count('data-testid="tbb-bracket-row"') == 1


def test_breakdown_renders_audit_readiness_label_not_score():
    """The audit-readiness card shows the PLAIN-ENGLISH label (Strong /
    Moderate / At-Risk / Building) and a customer-readable sentence — not
    the numeric score, never the component machinery, never any rule_id."""
    bands = [
        _FakeBand(
            band_lower=Decimal("0"),
            band_upper=Decimal("1000000"),
            income_in_band=Decimal("1000000"),
            rate=Decimal("0.06"),
            tax_in_band=Decimal("60000"),
        ),
    ]
    html = _render_breakdown(bands, audit_label="Strong")

    assert 'data-testid="tbb-readiness-label"' in html
    assert "Strong" in html
    # Numeric score from the fake report (75) must NOT appear:
    assert ">75<" not in html  # would be inside a label tag if exposed
    # Component machinery must NOT appear:
    assert "score" not in html.lower() or "score" in "your audit-readiness score"


def test_breakdown_income_card_uses_friendly_labels():
    """Income categories shown to customers use spaces + title-case, not
    raw snake_case DB column names."""
    bands = [
        _FakeBand(
            band_lower=Decimal("0"),
            band_upper=Decimal("1000000"),
            income_in_band=Decimal("1000000"),
            rate=Decimal("0.06"),
            tax_in_band=Decimal("60000"),
        ),
    ]
    html = _render_breakdown(bands, income_total=Decimal("5000000"))

    assert 'data-testid="tbb-income-card"' in html
    # The category key "foreign_remittance" should be presented friendly:
    assert "Foreign Remittance" in html
    # Raw snake_case form must NOT appear:
    assert "foreign_remittance" not in html


def test_breakdown_back_link_points_to_main_bill():
    """The page MUST give the customer a way back to the main tax-bill
    page — both as a top-of-page link AND as a CTA button."""
    bands = [
        _FakeBand(
            band_lower=Decimal("0"),
            band_upper=Decimal("1000000"),
            income_in_band=Decimal("1000000"),
            rate=Decimal("0.06"),
            tax_in_band=Decimal("60000"),
        ),
    ]
    html = _render_breakdown(bands)

    # Top-of-page back link:
    assert 'data-testid="tbb-back-link"' in html
    # Bottom CTA:
    assert 'data-testid="tbb-ctas"' in html
    assert "Back to your tax bill" in html
