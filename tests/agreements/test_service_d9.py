"""tests.agreements.test_service_d9 — D9: B4 protected-deductions strip.

D9 (Tier-D6 minor, 2026-05-25)
------------------------------
Defect: the B4 "protects approximately Rs X in deductions" sentence was
absent from /agreements/service/<sp_id> for SPs with no fee/expense
(monthly_rate unset → compute_protected_deductions_lkr returns 0 → the
Jinja template's `{% if _protected and _protected > 0 %}` branch hid
the entire strip, leaving DOM probes unable to detect ANY framing
about the protected-Rs value).

Fix: render the strip unconditionally, with a coachmark when
protected_lkr == 0 prompting the operator/user to add a fee.

These tests pin both branches by rendering the template against
isolated contexts (no full Flask boot — the template is plain Jinja
with only `url_for` + `csrf_token` shims required).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest


_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


_TEMPLATE_PATH = _ROOT / "templates" / "agreements" / "service_preview.html"


def _render_preview(protected_deductions_lkr: int) -> str:
    """Render the service_preview.html template against a minimal context.

    We strip the `{% extends layout_template %}` directive and shim the
    Jinja-side functions (`url_for`, `csrf_token`) the template references
    so we can render it standalone without booting the full Flask app
    (which would require Neon + Stripe + Flask-Login).
    """
    from jinja2 import Environment, DictLoader

    raw = _TEMPLATE_PATH.read_text(encoding="utf-8")
    # Drop layout inheritance + block wrappers so we can render the body
    # standalone. Substitute a no-op extends so block content + styles
    # render inline.
    no_extends = re.sub(
        r"\{%\s*extends\s+layout_template\s*%\}",
        "",
        raw,
        count=1,
    )
    # Render the {% block content %}...{% endblock %} portion. Keep block
    # additional_styles in place too — both will render inside our shim
    # base template.
    base = (
        "{% block additional_styles %}{% endblock %}"
        "{% block title %}{% endblock %}"
        "{% block content %}{% endblock %}"
    )
    env = Environment(
        loader=DictLoader({"base.html": base, "preview.html": no_extends}),
        autoescape=True,
    )

    def _url_for(name, **kwargs):
        return f"/{name}/" + "/".join(str(v) for v in kwargs.values())

    def _csrf_token():
        return "test-csrf-token"

    env.globals["url_for"] = _url_for
    env.globals["csrf_token"] = _csrf_token

    tmpl = env.get_template("preview.html")
    return tmpl.render(
        sp_id="sp-test-123",
        disclosure_default_on=False,
        evidence_prompt="",
        gate_warnings=[],
        gate_blocks=[],
        protected_deductions_lkr=protected_deductions_lkr,
    )


def test_d9_strip_renders_with_amount_when_protected_gt_zero():
    """SP with a fee → "protects approximately Rs X" sentence is present."""
    html = _render_preview(protected_deductions_lkr=540_000)

    assert 'data-testid="b4-protected-strip"' in html, (
        "B4 strip must always render (defect D9)"
    )
    assert 'data-testid="b4-protected-amount"' in html, (
        "Amount span must render when protected > 0"
    )
    assert "Rs 540,000" in html, "Formatted LKR amount must appear"
    assert "protects approximately" in html.lower(), (
        "Anchor phrase must appear"
    )
    assert "IRA §6(1)" in html, "IRA citation note must appear"


def test_d9_strip_renders_coachmark_when_protected_zero():
    """SP without a fee → strip still renders + prompts user to add one."""
    html = _render_preview(protected_deductions_lkr=0)

    assert 'data-testid="b4-protected-strip"' in html, (
        "B4 strip must render unconditionally (defect D9 — no longer hidden)"
    )
    assert 'data-testid="b4-protected-zero"' in html, (
        "Zero-state coachmark must render"
    )
    assert "Add a fee/expense for this SP" in html, (
        "Coachmark text must guide the user to add a fee"
    )
    # Negative assertion: amount span MUST NOT render when zero
    assert 'data-testid="b4-protected-amount"' not in html, (
        "Amount span must NOT render in zero state"
    )
