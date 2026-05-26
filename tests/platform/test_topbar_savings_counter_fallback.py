"""tests/platform/test_topbar_savings_counter_fallback.py — D-N3 polish.

D-N3 (2026-05-27): the FIESTA topbar savings counter previously rendered
"Rs --" when `hub_projected_savings_lkr` was undefined / None / 0. The
"--" placeholder reads as a broken / still-loading state to customers.

Fix: when the projection is missing or zero, render "Rs 0" instead — the
honest "no savings projected yet" value. The Design-Lock-1 element id +
data-source attribute remain locked; only the visible fallback text
changes.

Rendering strategy: pure Jinja env (no full Flask app shell) — the
topbar is a partial that's normally `{% include %}`d from layout_fiesta;
we render it standalone with stubbed url_for + g + request to keep the
test fast and dependency-free.

Run:
    cd C:/Users/mahes/fiesta_phase_a/worktrees/dn-polish
    DATABASE_URL=sqlite:///:memory: python -m pytest tests/platform/test_topbar_savings_counter_fallback.py -v
"""
from __future__ import annotations

import pathlib
import re
import sys

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


class _StubUser:
    """Stand-in for Flask-Login `current_user`. Defaults to anonymous."""

    def __init__(self, authenticated=False, role="user", name="Test", email="t@t.com"):
        self.is_authenticated = authenticated
        self.role = role
        self.name = name
        self.email = email


class _StubG:
    """Stand-in for the Flask `g` request-global. Empty by default; the
    template uses `g.admin_view_as is defined` so absence is fine."""

    def __init__(self):
        pass

    def __getattr__(self, _name):
        # Jinja's `is defined` check uses Undefined; we mimic that by
        # raising AttributeError so the test renders cleanly.
        raise AttributeError(_name)


def _make_env():
    """Build a Jinja2 env rooted at templates/ with the stubs the topbar
    partial needs (url_for + a couple of globals)."""
    from jinja2 import ChainableUndefined, Environment, FileSystemLoader

    env = Environment(
        loader=FileSystemLoader(str(_REPO_ROOT / "templates")),
        autoescape=True,
        # ChainableUndefined lets `foo.bar.baz` not crash mid-chain — the
        # topbar uses `g.admin_view_as` which is undefined in tests.
        undefined=ChainableUndefined,
    )
    env.globals["url_for"] = lambda endpoint, **kw: f"/_stub_{endpoint}"
    env.globals["csrf_token"] = lambda: "stub-csrf"
    return env


def _render_topbar(**context):
    """Render the topbar partial with the supplied template context.

    The partial references `_ya_default`, `_ya_options`,
    `hub_projected_savings_lkr`, `current_user`, and `g` — we provide
    safe stand-ins so the template renders without UndefinedError.
    Anything not supplied here defaults to a safe stand-in.
    """
    env = _make_env()
    tmpl = env.get_template("_fiesta/topbar.html")
    ctx = {
        "_ya_default": "24/25",
        "_ya_options": [{"value": "24/25", "label": "YA 24/25"}],
        "_ya_label": "YA 24/25",
        "current_user": _StubUser(authenticated=True, role="user"),
        # `hub_projected_savings_lkr` intentionally omitted from defaults —
        # callers supply it (or don't, to exercise the `is defined` branch).
    }
    ctx.update(context)
    return tmpl.render(**ctx)


# ---------------------------------------------------------------------------
# D-N3 regression tests — the fallback for missing/None/0 must be "Rs 0",
# never "Rs --".
# ---------------------------------------------------------------------------


def test_savings_counter_renders_rs_zero_when_projection_undefined():
    """No `hub_projected_savings_lkr` in context → counter says "Rs 0".

    Pre-fix: rendered "Rs --" (looks broken).
    Post-fix: renders "Rs 0".
    """
    html = _render_topbar()  # no hub_projected_savings_lkr passed in
    # Element id is Design-Lock-1 locked — find it first.
    assert 'id="fiesta-savings-counter"' in html, (
        "topbar must keep the Design-Lock-1 counter element id"
    )
    assert "Rs --" not in html, (
        f"D-N3 regression: 'Rs --' rendered when hub_projected_savings_lkr "
        f"was undefined. Should fall through to 'Rs 0'. HTML excerpt: "
        f"{_excerpt(html, 'fiesta-savings-counter', 200)}"
    )
    assert "Rs 0" in html, (
        f"D-N3 polish: expected fallback to render 'Rs 0' when projection "
        f"is undefined. HTML excerpt: "
        f"{_excerpt(html, 'fiesta-savings-counter', 200)}"
    )


def test_savings_counter_renders_rs_zero_when_projection_is_none():
    """`hub_projected_savings_lkr=None` → counter says "Rs 0" (not "--")."""
    html = _render_topbar(hub_projected_savings_lkr=None)
    assert "Rs --" not in html, (
        "D-N3 regression: explicit None should fall through to 'Rs 0', "
        "not the broken-looking 'Rs --' placeholder."
    )
    assert "Rs 0" in html


def test_savings_counter_renders_rs_zero_when_projection_is_zero():
    """`hub_projected_savings_lkr=0` → counter says "Rs 0" (not "--").

    The original Jinja expression was effectively `value or '--'` — meaning
    0 (falsy in Jinja) triggered the placeholder. With D-N3 the fallback
    is '0', so the displayed value is "Rs 0" either via the format branch
    or via the fallback.
    """
    html = _render_topbar(hub_projected_savings_lkr=0)
    assert "Rs --" not in html
    assert "Rs 0" in html


def test_savings_counter_renders_formatted_amount_when_projection_present():
    """Sanity: the happy path still works — real number renders formatted."""
    html = _render_topbar(hub_projected_savings_lkr=125000)
    assert "Rs --" not in html
    # Comma-grouped format.
    assert "Rs 125,000" in html or "Rs 125000" in html, (
        f"Expected formatted 125,000 in counter, got: "
        f"{_excerpt(html, 'fiesta-savings-counter', 200)}"
    )


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _excerpt(html: str, anchor: str, width: int = 160) -> str:
    """Return a ±width-char window of the HTML around the first occurrence
    of `anchor` — keeps assertion-failure output focused on the relevant
    fragment instead of the whole rendered template."""
    idx = html.find(anchor)
    if idx < 0:
        return html[:width]
    start = max(0, idx - width)
    end = min(len(html), idx + width)
    return html[start:end]
