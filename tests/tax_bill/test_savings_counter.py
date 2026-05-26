"""tests/tax_bill/test_savings_counter.py — F6.6 always-visible savings counter.

Phase C Wave 2 (Fix 2, 2026-05-26): the S12 savings counter previously
disappeared when projected_savings_lkr == 0 — killing the perceived value
for new users with no deductions claimed yet. Now the .tb-projected-savings
container is ALWAYS in the DOM (when the engine is healthy), and renders
two distinct shapes:

  - happy path (>0 savings): the figure + source attribution
  - opportunity CTA (0 savings): headline + body + CTA button to /reduce-tax/

Both states are wrapped in the same .tb-projected-savings .savings-counter
container so frontends / Playwright probes can always locate the element
by the same selector.

Run:
    cd C:/Users/mahes/fiesta_phase_a/worktrees/tax-bill-rendering
    DATABASE_URL=sqlite:///:memory: python -m pytest tests/tax_bill/test_savings_counter.py -v
"""
from __future__ import annotations

import pathlib
import sys
from dataclasses import dataclass, field
from decimal import Decimal

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Same minimal Jinja env as the bracket test — we render the full
# templates/tax_bill/index.html with stubbed url_for + layout_template.
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
    env.globals["url_for"] = lambda endpoint, **kw: f"/_stub_{endpoint}"
    env.globals["csrf_token"] = lambda: "stub-csrf"
    return env


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
        return self.personal_relief_applied_lkr


@dataclass
class _FakeComp:
    by_band: list = field(default_factory=list)
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
    computation_with_deductions: _FakeComp
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


class _FakeRequest:
    def __init__(self, args=None):
        self.args = args or {}

    def __bool__(self):
        return True


def _render(
    savings_vs_no_deductions_lkr: Decimal,
    hub_projected_savings_lkr: int = 0,
    engine_error: str | None = None,
) -> str:
    env = _make_env()
    template = env.get_template("tax_bill/index.html")

    inputs = _FakeInputs(
        income_by_category_lkr={"foreign_remittance": Decimal("3000000")},
        income_by_currency={"LKR": Decimal("3000000")},
    )
    comp = _FakeComp(
        by_band=[
            _FakeBand(
                band_lower=Decimal("0"),
                band_upper=Decimal("500000"),
                income_in_band=Decimal("500000"),
                rate=Decimal("0.06"),
                tax_in_band=Decimal("30000"),
            ),
        ]
    )
    report = _FakeReport(
        inputs=inputs,
        computation_with_deductions=comp,
        savings_vs_no_deductions_lkr=savings_vs_no_deductions_lkr,
        engine_error=engine_error,
    )
    return template.render(
        report=report,
        inputs=inputs,
        gate=None,
        tax_year_s4="2025-26",
        tax_year_display="2025/2026",
        available_years=["2025-26"],
        selected_year="2025-26",
        layout_template="_stub_layout.html",
        hub_projected_savings_lkr=hub_projected_savings_lkr,
        request=_FakeRequest(),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_zero_savings_renders_cta_not_amount():
    """projected_savings_lkr=0 — assert deduction CTA renders, savings figure
    NOT rendered. The container is still present in the DOM with state =
    zero_deductions_cta."""
    html = _render(savings_vs_no_deductions_lkr=Decimal("0"),
                   hub_projected_savings_lkr=0)

    # Container ALWAYS present.
    assert 'data-testid="savings-counter"' in html, (
        "The .savings-counter container must always be in the DOM"
    )
    # State attribute reflects the CTA mode.
    assert 'data-state="zero_deductions_cta"' in html

    # CTA elements present.
    assert 'data-testid="savings-counter-cta"' in html
    assert 'data-testid="savings-counter-cta-button"' in html
    assert "You haven't claimed any deductions yet" in html
    assert "Claim deductions" in html
    assert "/reduce-tax/" in html
    assert "Rs 50,000" in html  # body copy mentions the typical range
    assert "Rs 200,000" in html

    # Savings figure UI elements absent.
    assert 'data-testid="savings-counter-amount"' not in html, (
        "Zero-savings state must NOT render the savings amount span"
    )


def test_positive_savings_renders_amount_not_cta():
    """projected_savings_lkr=100000 — assert savings figure renders, CTA
    hidden. Container still present with state = has_savings."""
    html = _render(savings_vs_no_deductions_lkr=Decimal("100000"))

    # Container present.
    assert 'data-testid="savings-counter"' in html
    assert 'data-state="has_savings"' in html

    # Savings figure rendered.
    assert 'data-testid="savings-counter-amount"' in html
    assert "Rs 100,000" in html
    assert "based on your claimed deductions" in html

    # CTA hidden.
    assert 'data-testid="savings-counter-cta"' not in html, (
        "Positive-savings state must NOT render the CTA block"
    )
    assert 'data-testid="savings-counter-cta-button"' not in html
    assert "You haven't claimed any deductions yet" not in html


def test_container_always_present_in_both_states():
    """Both happy path AND opportunity CTA wrap in the same .savings-counter
    container — the contract is 'always present in DOM' (modulo engine error)."""
    html_zero = _render(savings_vs_no_deductions_lkr=Decimal("0"))
    html_pos = _render(savings_vs_no_deductions_lkr=Decimal("100000"))

    assert "savings-counter" in html_zero
    assert "savings-counter" in html_pos

    # Both states emit data-testid="savings-counter"
    assert 'data-testid="savings-counter"' in html_zero
    assert 'data-testid="savings-counter"' in html_pos

    # Container class .tb-projected-savings still present in both — preserves
    # all existing CSS hooks.
    assert 'class="tb-projected-savings savings-counter"' in html_zero
    assert 'class="tb-projected-savings savings-counter"' in html_pos


def test_hub_projection_shown_when_no_real_savings():
    """When the user has no claimed-deduction savings but the hub context
    provides a projected estimate, we show that estimate (not the CTA)."""
    html = _render(
        savings_vs_no_deductions_lkr=Decimal("0"),
        hub_projected_savings_lkr=50000,
    )

    assert 'data-state="has_savings"' in html
    assert 'data-testid="savings-counter-amount"' in html
    assert "Rs 50,000" in html
    # Note copy steers user to /reduce-tax to refine.
    assert "estimate" in html.lower()
    # CTA block NOT present (we have a projection to show).
    assert 'data-testid="savings-counter-cta"' not in html


def test_engine_error_suppresses_counter():
    """When engine_error is set, the engine-error block takes the visual
    real-estate and the savings counter is suppressed (existing behaviour).
    This is a regression guard — Fix 2 must NOT over-correct and double-up
    the error surface."""
    html = _render(
        savings_vs_no_deductions_lkr=Decimal("0"),
        engine_error="Engine version mismatch",
    )

    assert "Tax engine unavailable" in html  # the engine-error block fired
    assert 'data-testid="savings-counter"' not in html, (
        "When engine_error is set, the savings counter must not render"
    )
