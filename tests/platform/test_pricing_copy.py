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


# --------------------------------------------------------------------------- #
# F1.3 (P2 polish, 2026-05-27) — filesystem-level SoT regression.
#
# The macro file is the ONLY legitimate source of the literal pricing
# strings "Rs 2,500" / "Free during launch". Every other Jinja template
# must read those strings through {% from "_pricing_macros.html" import ... %}.
#
# This test grep-scans templates/ + static/ and fails if any future
# commit re-introduces a hardcoded pricing string outside the macro file.
# --------------------------------------------------------------------------- #


_F1_3_HARDCODED_PRICING_PATTERNS = [
    "Rs 2,500",
    "Free during launch",
    "first year free",
]

# Files allowed to contain the literal strings:
#   - `_pricing_macros.html`             the single source of truth itself
#   - `legal/tos_draft.md`               legal text (separate contract layer)
#   - `admin/receipts.html`              sample receipt-data mock (Rs 2,500.00
#                                        is a printer-ink line, not pricing)
#   - `paywall/pricing_x1.html`          Jinja `{#...#}` source comment only;
#                                        the literal does not reach rendered
#                                        HTML, kept as a code-author hint.
#   - `pricing.html`                     Jinja `{#...#}` source comment only;
#                                        actual price comes from
#                                        pricing_engine.PRICING_TIERS.
#   - `home_bookkeeping_legacy.html`     was a violator BEFORE this fix and
#                                        is documented rollback-only; the
#                                        D6/G5 audit already excludes it
#                                        from the active set. F1.3 swapped
#                                        the user-visible string to the
#                                        launch_posture() macro, no literal
#                                        survives on this surface anymore.
_F1_3_EXEMPT = {
    "_pricing_macros.html",
    "legal/tos_draft.md",
    "admin/receipts.html",
    "paywall/pricing_x1.html",
    "pricing.html",
}


def _iter_template_paths():
    for path in _TEMPLATES.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".html", ".md", ".js"}:
            continue
        # `.bak` files are historical snapshots, not in the rendered chain.
        if path.name.endswith(".bak") or path.suffix == ".bak":
            continue
        rel = path.relative_to(_TEMPLATES).as_posix()
        yield path, rel


def test_f1_3_no_hardcoded_pricing_outside_macro():
    """Grep templates/ for hardcoded pricing literals. Every literal must
    live in `_pricing_macros.html` (the SoT) or in the small allowlist of
    files where the string is intentional (legal text, sample mock data,
    Jinja source comments that don't render).

    If this test fails: switch the offending hardcoded string to one of
    the macros from `_pricing_macros.html` (pricing_amount / pricing_copy
    / pricing_badge / launch_posture / refund_window) — or, if the new
    file is a legitimate documentation / mock surface, add it to
    `_F1_3_EXEMPT` with a comment explaining why."""
    violations: list[tuple[str, str, int]] = []
    for path, rel in _iter_template_paths():
        if rel in _F1_3_EXEMPT:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for needle in _F1_3_HARDCODED_PRICING_PATTERNS:
                if needle in line:
                    violations.append((rel, needle, lineno))
                    break
    assert not violations, (
        "F1.3 SoT violation: hardcoded pricing literals found OUTSIDE "
        "templates/_pricing_macros.html. Switch each to the macros "
        "(pricing_amount / pricing_copy / launch_posture / refund_window) "
        f"or extend _F1_3_EXEMPT with justification. Violations:\n"
        + "\n".join(f"  {r} (L{ln}): {needle!r}" for r, needle, ln in violations)
    )


def test_f1_3_static_js_no_hardcoded_pricing():
    """Mirror of the templates/ scan for the static/ tree — JS/CSS surfaces
    that mention pricing must route through the macro (typically by
    consuming a server-rendered data attribute, not by hardcoding the
    string in client-side code)."""
    static_root = _REPO_ROOT / "static"
    if not static_root.exists():
        pytest.skip("no static/ directory in this checkout")
    violations: list[tuple[str, str, int]] = []
    for path in static_root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".js", ".css", ".html"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rel = path.relative_to(static_root).as_posix()
        for lineno, line in enumerate(text.splitlines(), 1):
            for needle in _F1_3_HARDCODED_PRICING_PATTERNS:
                if needle in line:
                    violations.append((rel, needle, lineno))
                    break
    assert not violations, (
        "F1.3 SoT violation: hardcoded pricing literals found in static/. "
        "Pricing strings must be server-rendered via the macros and read "
        f"by JS through DOM attributes, not embedded as literals.\nViolations:\n"
        + "\n".join(f"  static/{r} (L{ln}): {needle!r}" for r, needle, ln in violations)
    )
