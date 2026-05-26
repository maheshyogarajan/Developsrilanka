"""D4 (2026-05-27) — unified pricing copy regression suite.

Locks the contract created by `templates/_pricing_macros.html`:

  - Every customer-facing surface that mentions launch pricing reads its
    line from the `pricing_copy()` macro. Before the macro existed FIESTA
    shipped six (and counting) slightly-different framings across
    /register, /pricing, /pricing/x1, /signup, /tax-preview, s0_landing.
  - The CANONICAL phrasing (rendered by `pricing_copy()` with no args) is:

        Rs 2,500 per tax year · Free during launch period · 14-day refund window

  - Surfaces are free to compose the macro into their own visual element
    (badge / pill / inline text) but the rendered string must contain
    the substring "Rs 2,500 per tax year" so the cross-surface contract
    can be detected at HTTP-render time.

Why HTTP rendering (not just filesystem grep):
  - The contract is "the canonical phrase reaches the rendered page",
    not "the macro is referenced in source". Surfaces that mis-spell the
    import or accidentally suppress the call would silently regress.
"""
from __future__ import annotations

from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATES = _REPO_ROOT / "templates"


# The price string is the load-bearing substring of the macro's default
# output. Surfaces are free to add suffixes like " · Backed by Lanka.tax"
# (tax_math_breakdown.html) but the price string itself must survive.
CANONICAL_PRICE_SUBSTRING = "Rs 2,500 per tax year"
CANONICAL_LAUNCH_SUBSTRING = "Free during launch period"
CANONICAL_REFUND_SUBSTRING = "14-day refund window"


# --------------------------------------------------------------------------- #
# D4.a — macro file exists + contains the canonical phrases.
# --------------------------------------------------------------------------- #


def test_d4_pricing_macros_file_exists():
    macros = _TEMPLATES / "_pricing_macros.html"
    assert macros.exists(), (
        "templates/_pricing_macros.html is missing. The D4 unified-pricing "
        "contract requires this macro file."
    )
    src = macros.read_text(encoding="utf-8")
    for phrase in (
        CANONICAL_PRICE_SUBSTRING,
        CANONICAL_LAUNCH_SUBSTRING,
        CANONICAL_REFUND_SUBSTRING,
    ):
        assert phrase in src, (
            f"_pricing_macros.html does not contain the canonical phrase "
            f"{phrase!r}. The macro contract is broken."
        )


# --------------------------------------------------------------------------- #
# D4.b — each surface imports the macro (cheap precheck).
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "rel_path",
    [
        "register.html",
        "pricing.html",
        "paywall/pricing_x1.html",
        "signup.html",
        "components/tax_math_breakdown.html",
        "fiesta_public/s0_landing.html",
    ],
)
def test_d4_surface_imports_pricing_macro(rel_path):
    """Each pricing-mentioning surface must reference the shared macro via
    `{% from '_pricing_macros.html' import ... %}`. A missing import is
    the most likely regression vector for this fix."""
    src = (_TEMPLATES / rel_path).read_text(encoding="utf-8", errors="ignore")
    assert "_pricing_macros.html" in src, (
        f"{rel_path} does not import from `_pricing_macros.html`. "
        "The unified-pricing contract is broken — the surface is rendering "
        "its own launch / price / refund framing again."
    )


# --------------------------------------------------------------------------- #
# D4.c — HTTP-level: each surface renders the canonical price phrase.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "route",
    [
        "/",
        "/register",
        "/signup",
        "/tax-preview",
        "/pricing",
    ],
)
def test_d4_route_renders_canonical_price(client, route):
    """Every customer-facing pricing surface must emit the canonical
    price substring `Rs 2,500 per tax year` in its rendered HTML.

    /pricing/x1 is NOT included here — it routes through the paywall
    blueprint and requires an authenticated session; the source-level
    test (test_d4_surface_imports_pricing_macro) covers it instead.
    """
    r = client.get(route)
    assert r.status_code == 200, (
        f"GET {route} returned {r.status_code} (expected 200). "
        f"Body preview: {r.get_data(as_text=True)[:300]!r}"
    )
    body = r.get_data(as_text=True)
    assert CANONICAL_PRICE_SUBSTRING in body, (
        f"GET {route} response is missing the canonical price phrase "
        f"{CANONICAL_PRICE_SUBSTRING!r}. The unified-pricing macro did not "
        "render on this surface — either the import is wrong, the macro "
        "was bypassed, or the surface added its own competing framing."
    )


# --------------------------------------------------------------------------- #
# D4.d — banned legacy framings must be gone from rendered HTML.
# --------------------------------------------------------------------------- #


_LEGACY_PRICING_PHRASES = [
    # /register's old phrasing — the COMPOSITE "from 2026-09-01" string
    # was the launch-pricing framing the macro replaces. "No card required"
    # in isolation is also used by the Free Trial tier (legitimate, distinct
    # context — 30-day trial mechanic, not launch pricing), so we only
    # flag the composite that anchors it to the launch date.
    "Rs 2,500 from 2026-09-01",
    # /pricing/x1 old phrasing (badge).
    "Refundable 14 days",
]


@pytest.mark.parametrize(
    "route",
    [
        "/",
        "/register",
        "/signup",
        "/tax-preview",
        "/pricing",
    ],
)
def test_d4_legacy_pricing_framings_removed(client, route):
    """The legacy launch-pricing phrasings (six different framings across
    the funnel before D4) must NOT appear in the rendered HTML. If any
    surface re-introduces them, this catches it."""
    r = client.get(route)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    leaked = [phrase for phrase in _LEGACY_PRICING_PHRASES if phrase in body]
    assert not leaked, (
        f"GET {route} rendered legacy pricing phrases that should have been "
        f"replaced by the `_pricing_macros.pricing_copy()` macro: {leaked!r}"
    )
