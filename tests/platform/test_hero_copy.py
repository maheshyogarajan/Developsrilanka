"""Phase C Wave 2 / F1.2 — unified hero copy regression suite.

Locks the contract created by `templates/fiesta_public/_hero_partial.html`:

  - The 4 front-door surfaces (anon /, /tax-preview, /register, /signup)
    all render the same hero headline:
        "Cut your tax bill. Keep the records clean."
    (in the markup as: <em>Keep the records clean.</em>)
  - The shared partial leaves a `fiesta-hero-unified` marker class on the
    rendered hero wrapper, so any future refactor that bypasses the partial
    on one surface will break this test.

Why HTTP rendering (not just filesystem grep):
  - We need to prove the include actually FIRES, not just that the surface
    contains a reference to the partial path. Surfaces that mis-spell the
    include path or accidentally remove it would silently drop the marker.
  - This test piggybacks on the existing `client` fixture in
    tests/platform/conftest.py so it does not need its own Flask boot.
"""
from __future__ import annotations

from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATES = _REPO_ROOT / "templates"


# The headline string is the SHARED contract. Both visual variants below
# must appear (the literal markup and the human-readable text), because
# the partial wraps the second sentence in <em>.
HERO_HEADLINE_MARKUP = "Cut your tax bill. <em>Keep the records clean.</em>"
HERO_MARKER_CLASS = "fiesta-hero-unified"


# --------------------------------------------------------------------------- #
# F1.2.a — partial file exists + contains the canonical headline.
# --------------------------------------------------------------------------- #


def test_f1_2_partial_exists():
    partial = _TEMPLATES / "fiesta_public" / "_hero_partial.html"
    assert partial.exists(), (
        "templates/fiesta_public/_hero_partial.html is missing. The F1.2 "
        "unified hero contract requires this shared partial to exist."
    )
    src = partial.read_text(encoding="utf-8")
    assert HERO_HEADLINE_MARKUP in src, (
        "_hero_partial.html does not contain the canonical headline markup "
        f"{HERO_HEADLINE_MARKUP!r}. Restoring the exact string is the "
        "contract — every other front-door surface includes this partial."
    )
    assert HERO_MARKER_CLASS in src, (
        "_hero_partial.html is missing the `fiesta-hero-unified` marker class. "
        "The marker is what the cross-surface tests assert against."
    )


# --------------------------------------------------------------------------- #
# F1.2.b — each surface filesystem-includes the partial (cheap precheck).
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "rel_path",
    [
        "fiesta_public/s0_landing.html",
        "components/tax_math_breakdown.html",
        "register.html",
        "signup.html",
    ],
)
def test_f1_2_surface_includes_partial(rel_path):
    """Each front-door surface must reference the shared partial via an
    `{% include 'fiesta_public/_hero_partial.html' %}` Jinja directive.
    A missing include is the most likely regression vector for this fix."""
    src = (_TEMPLATES / rel_path).read_text(encoding="utf-8", errors="ignore")
    assert "fiesta_public/_hero_partial.html" in src, (
        f"{rel_path} does not include `fiesta_public/_hero_partial.html`. "
        "The unified-hero contract is broken — the surface is rendering "
        "its own headline copy again."
    )


# --------------------------------------------------------------------------- #
# F1.2.c — HTTP-level: each surface renders the canonical headline + marker.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "route",
    [
        "/",
        "/tax-preview",
        "/register",
        "/signup",
    ],
)
def test_f1_2_route_renders_unified_hero_marker(client, route):
    """Every front-door route must emit the `fiesta-hero-unified` marker
    class. This proves the include actually fires at render-time, not just
    that the source file references the partial path."""
    r = client.get(route)
    assert r.status_code == 200, (
        f"GET {route} returned {r.status_code} (expected 200). "
        f"Body preview: {r.get_data(as_text=True)[:300]!r}"
    )
    body = r.get_data(as_text=True)
    assert HERO_MARKER_CLASS in body, (
        f"GET {route} response is missing the `{HERO_MARKER_CLASS}` marker "
        "class. The unified-hero include did not fire — either the include "
        "path is wrong or the partial is being overridden by a block."
    )


@pytest.mark.parametrize(
    "route",
    [
        "/",
        "/tax-preview",
        "/register",
        "/signup",
    ],
)
def test_f1_2_route_renders_canonical_headline(client, route):
    """Every front-door route must emit the exact canonical headline markup.
    The string match is intentionally byte-for-byte — any wording drift on
    any surface is a regression."""
    r = client.get(route)
    assert r.status_code == 200, (
        f"GET {route} returned {r.status_code} (expected 200)."
    )
    body = r.get_data(as_text=True)
    assert HERO_HEADLINE_MARKUP in body, (
        f"GET {route} response is missing the canonical headline "
        f"{HERO_HEADLINE_MARKUP!r}. The cross-surface copy contract is "
        "broken — the partial probably renders a different string on this "
        "surface, OR the surface overrode the partial output."
    )
