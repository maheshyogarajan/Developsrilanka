"""tests/tax_bill/test_breakdown_empty_rows.py — F6.7 bracket-row suppression.

Phase C Wave 2 (Fix 1, 2026-05-26): the S12 bracket walk previously rendered
EVERY tax bracket — including those where the user owed Rs 0 because their
taxable income didn't reach that band. For most foreign-remit / single-
source filers that means most rows are Rs 0, making the bill look broken.

Contract
--------
1. By default, rows where (taxable_in_band == 0 AND tax_due == 0) are hidden.
2. A footer note appears explaining the suppression: "Brackets where you owe
   nothing aren't shown." (link to ?show_all_brackets=1).
3. With ?show_all_brackets=1 query param, ALL bracket rows render, plus a
   "Hide empty brackets" toggle.
4. The bracket-walk table emits a `data-testid="bracket-walk-table"` and each
   rendered row a `data-testid="bracket-row"` so downstream tests / Playwright
   probes can count rows.
5. If NO band has income (zero-income user), the empty-state block renders
   instead (existing behaviour, regression-guarded here).

Test strategy: render the templates/tax_bill/index.html template directly
through a minimal Jinja environment with a stub `layout_template` (so we
don't drag in the full Flask app shell). The bracket-walk block lives
deep inside the page; we render the whole page and assert on the resulting
HTML markup.

Run:
    cd C:/Users/mahes/fiesta_phase_a/worktrees/tax-bill-rendering
    DATABASE_URL=sqlite:///:memory: python -m pytest tests/tax_bill/test_breakdown_empty_rows.py -v
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
# Minimal Jinja2 environment that loads templates/tax_bill/index.html with a
# stub `layout_template` so we don't need the full Flask app shell.
# ---------------------------------------------------------------------------


def _make_env():
    """Build a Jinja2 environment rooted at templates/.

    - Provides a stub `layout.html` so `{% extends layout_template %}` resolves.
    - The stub passes content + scripts blocks through unchanged.
    """
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

    # url_for stub — the template references several url_for() calls
    # (audit-pack export, return PDF, etc.). Return placeholder anchors.
    def _url_for(endpoint, **kwargs):
        return f"/_stub_{endpoint}"

    env.globals["url_for"] = _url_for
    env.globals["csrf_token"] = lambda: "stub-csrf"

    return env


# ---------------------------------------------------------------------------
# Lightweight fakes that quack like the real report / comp / band objects.
# Jinja accesses attributes (b.income_in_band, comp.by_band, …), so plain
# dataclasses with the right names are enough.
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

    def total(self):
        return (
            self.personal_relief_applied_lkr
            + self.senior_citizen_extra_lkr
            + self.solar_relief_applied_lkr
        )


@dataclass
class _FakeComp:
    by_band: list[_FakeBand]
    gross_income_lkr: Decimal = Decimal("3000000")
    deductions_input_lkr: Decimal = Decimal("0")
    relief_applied: _FakeRelief = field(default_factory=_FakeRelief)
    taxable_income_lkr: Decimal = Decimal("1200000")
    gross_tax_lkr: Decimal = Decimal("72000")
    net_tax_due_lkr: Decimal = Decimal("72000")
    marginal_rate: Decimal = Decimal("0.12")
    effective_rate: Decimal = Decimal("0.024")


@dataclass
class _FakeInputs:
    income_by_category_lkr: dict = field(default_factory=dict)
    income_by_currency: dict = field(default_factory=dict)
    income_unconverted_currencies: list = field(default_factory=list)
    income_fx_warnings: list = field(default_factory=list)
    deductions_itemised: list = field(default_factory=list)
    deductions_total_lkr: Decimal = Decimal("0")
    deductions_with_evidence_count: int = 0
    service_providers: list = field(default_factory=list)
    rentals: list = field(default_factory=list)
    rsu_vesting_lines: list = field(default_factory=list)
    rsu_cgt_lines: list = field(default_factory=list)
    rsu_vesting_total_lkr: Decimal = Decimal("0")
    rsu_cgt_total_lkr: Decimal = Decimal("0")
    rsu_dtaa_deferred: bool = False
    business_lines: list = field(default_factory=list)
    business_gross_total_lkr: Decimal = Decimal("0")
    business_expenses_total_lkr: Decimal = Decimal("0")
    business_taxable_profit_total_lkr: Decimal = Decimal("0")
    business_dtaa_deferred: bool = False
    employment_lines: list = field(default_factory=list)
    employment_gross_total_lkr: Decimal = Decimal("0")
    employment_apit_credit_total_lkr: Decimal = Decimal("0")
    professional_fee_lines: list = field(default_factory=list)
    professional_fee_gross_total_lkr: Decimal = Decimal("0")
    professional_fee_wht_credit_total_lkr: Decimal = Decimal("0")


@dataclass
class _FakeReport:
    inputs: _FakeInputs
    computation_with_deductions: _FakeComp | None
    gross_income_lkr: Decimal = Decimal("3000000")
    gross_tax_payable_lkr: Decimal = Decimal("72000")
    total_deductions_lkr: Decimal = Decimal("0")
    net_tax_payable_lkr: Decimal = Decimal("72000")
    tax_without_deductions_lkr: Decimal = Decimal("72000")
    savings_vs_no_deductions_lkr: Decimal = Decimal("0")
    engine_error: str | None = None
    is_finalized: bool = False
    audit_defensibility_score: int = 60
    audit_defensibility_label: str = "Moderate"
    audit_score_components: dict = field(default_factory=dict)
    tax_year_s4_format: str = "2025-26"
    tax_year_s5_format: str = "2025/2026"


@dataclass
class _FakeRequest:
    args: dict

    def __init__(self, args: dict | None = None):
        self.args = args or {}

    def __bool__(self):
        return True


def _render_index(
    by_band: list[_FakeBand],
    request_args: dict | None = None,
    **report_overrides,
) -> str:
    """Render templates/tax_bill/index.html with a fake report containing
    the supplied bracket list. Returns the resulting HTML as a string."""
    env = _make_env()
    template = env.get_template("tax_bill/index.html")

    inputs = _FakeInputs(
        income_by_category_lkr={"foreign_remittance": Decimal("3000000")},
        income_by_currency={"LKR": Decimal("3000000")},
    )
    comp = _FakeComp(by_band=by_band)
    report = _FakeReport(inputs=inputs, computation_with_deductions=comp, **report_overrides)

    return template.render(
        report=report,
        inputs=inputs,
        gate=None,
        tax_year_s4="2025-26",
        tax_year_display="2025/2026",
        available_years=["2025-26"],
        selected_year="2025-26",
        layout_template="_stub_layout.html",
        hub_projected_savings_lkr=0,
        request=_FakeRequest(request_args),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_empty_rows_hidden_by_default():
    """A breakdown with brackets [{taxable:100k,due:6k},
    {taxable:0,due:0}, {taxable:50k,due:9k}] renders only 2 rows by default."""
    bands = [
        _FakeBand(
            band_lower=Decimal("0"),
            band_upper=Decimal("500000"),
            income_in_band=Decimal("100000"),
            rate=Decimal("0.06"),
            tax_in_band=Decimal("6000"),
        ),
        # The "owe-nothing" band — must be hidden by default.
        _FakeBand(
            band_lower=Decimal("500000"),
            band_upper=Decimal("1000000"),
            income_in_band=Decimal("0"),
            rate=Decimal("0.12"),
            tax_in_band=Decimal("0"),
        ),
        _FakeBand(
            band_lower=Decimal("1000000"),
            band_upper=Decimal("1500000"),
            income_in_band=Decimal("50000"),
            rate=Decimal("0.18"),
            tax_in_band=Decimal("9000"),
        ),
    ]
    html = _render_index(bands)

    # Should render 2 bracket rows (the populated ones), not 3.
    rendered_rows = html.count('data-testid="bracket-row"')
    assert rendered_rows == 2, (
        f"Expected 2 bracket rows (empty band suppressed), got {rendered_rows}\n"
        f"--- HTML ---\n{html[:4000]}"
    )

    # Footer note must explain the suppression and link to ?show_all_brackets=1.
    assert "Brackets where you owe nothing aren't shown" in html, (
        "Missing footer note explaining bracket suppression"
    )
    assert 'data-testid="bracket-show-all-toggle"' in html, (
        "Missing 'Show all brackets' toggle link"
    )
    assert "show_all_brackets=1" in html


def test_show_all_brackets_param_brings_back_all_rows():
    """The 'show all' parameter brings back all 3 rows + a 'Hide empty
    brackets' toggle. Empty rows are still flagged via
    data-bracket-empty='true' so frontends can dim them visually."""
    bands = [
        _FakeBand(
            band_lower=Decimal("0"),
            band_upper=Decimal("500000"),
            income_in_band=Decimal("100000"),
            rate=Decimal("0.06"),
            tax_in_band=Decimal("6000"),
        ),
        _FakeBand(
            band_lower=Decimal("500000"),
            band_upper=Decimal("1000000"),
            income_in_band=Decimal("0"),
            rate=Decimal("0.12"),
            tax_in_band=Decimal("0"),
        ),
        _FakeBand(
            band_lower=Decimal("1000000"),
            band_upper=Decimal("1500000"),
            income_in_band=Decimal("50000"),
            rate=Decimal("0.18"),
            tax_in_band=Decimal("9000"),
        ),
    ]
    html = _render_index(bands, request_args={"show_all_brackets": "1"})

    rendered_rows = html.count('data-testid="bracket-row"')
    assert rendered_rows == 3, (
        f"Expected 3 bracket rows with show_all_brackets=1, got {rendered_rows}"
    )

    # The empty band should be flagged as such for downstream styling.
    assert 'data-bracket-empty="true"' in html, (
        "Empty bracket rows must be flagged with data-bracket-empty='true'"
    )
    # And the populated bands must NOT be flagged as empty.
    assert 'data-bracket-empty="false"' in html

    # The toggle direction flips: now there's a "Hide" link, not "Show all".
    assert 'data-testid="bracket-hide-toggle"' in html, (
        "Show-all view must offer a 'Hide empty brackets' toggle"
    )
    assert "Hide empty brackets" in html
    # Footer line counts the visible bands AND the hidden ones.
    assert "Showing all 3 brackets" in html
    assert "1 where you owe nothing" in html or "1 brackets where" in html or \
           "the 1" in html  # phrasing tolerance


def test_no_footer_note_when_no_empty_bands():
    """When every band has income, no suppression occurred — no footer note
    needed (the user already sees the full structure)."""
    bands = [
        _FakeBand(
            band_lower=Decimal("0"),
            band_upper=Decimal("500000"),
            income_in_band=Decimal("100000"),
            rate=Decimal("0.06"),
            tax_in_band=Decimal("6000"),
        ),
        _FakeBand(
            band_lower=Decimal("500000"),
            band_upper=Decimal("1000000"),
            income_in_band=Decimal("80000"),
            rate=Decimal("0.12"),
            tax_in_band=Decimal("9600"),
        ),
    ]
    html = _render_index(bands)

    rendered_rows = html.count('data-testid="bracket-row"')
    assert rendered_rows == 2

    # No empty rows -> no footer note.
    assert "Brackets where you owe nothing aren't shown" not in html, (
        "Footer note should not appear when there are no empty bands"
    )
    assert 'data-testid="bracket-footer-note"' not in html


def test_zero_income_user_sees_empty_state_not_table():
    """If NO band has income, the empty-state block renders instead of an
    empty table (existing behaviour — regression guard)."""
    bands = [
        _FakeBand(
            band_lower=Decimal("0"),
            band_upper=Decimal("500000"),
            income_in_band=Decimal("0"),
            rate=Decimal("0.06"),
            tax_in_band=Decimal("0"),
        ),
        _FakeBand(
            band_lower=Decimal("500000"),
            band_upper=Decimal("1000000"),
            income_in_band=Decimal("0"),
            rate=Decimal("0.12"),
            tax_in_band=Decimal("0"),
        ),
    ]
    html = _render_index(bands)

    # Empty state present, table absent.
    assert 'data-testid="bracket-empty-state"' in html
    assert 'data-testid="bracket-walk-table"' not in html
    assert "Once you log your income" in html
