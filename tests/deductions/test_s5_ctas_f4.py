"""F4.3 + F4.4 (P2 polish, 2026-05-27) — S5 outbound-CTA regression tests.

The /reduce-tax/ (S5) page has two bottom-of-page CTAs that route the
user into adjacent high-deduction flows:

  F4.3 — "Connect your Service Providers" must link to the real
         service-providers index (not a `#s6` anchor stub that does
         nothing). The orphan anchor existed in an earlier draft and
         the brief explicitly calls out replacing it.

  F4.4 — "Set up your home-office rent" must link to /property — the
         CTA pulls customers into the home-office-rental deduction
         flow which is one of the highest-Rs categories foreign-income
         earners typically miss.

These tests are template-source-level (cheap, deterministic). They lock
the exact href strings so a refactor that swaps in a wrong endpoint
name or re-introduces the orphan anchor is caught at test time.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATE = _REPO_ROOT / "templates" / "deductions" / "index.html"


@pytest.fixture(scope="module")
def s5_source() -> str:
    assert _TEMPLATE.exists(), f"S5 template not found: {_TEMPLATE}"
    return _TEMPLATE.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# F4.3 — "Connect Service Providers" CTA must be a real link.
# --------------------------------------------------------------------------- #


def test_f4_3_connect_sp_cta_present_with_correct_label(s5_source):
    """The S5 page must contain the 'Connect your Service Providers'
    CTA label so a future template restructure that drops the section
    is caught here (not just via the href regression)."""
    assert "Connect your Service Providers" in s5_source, (
        "F4.3 CTA label removed from S5 page. The Service-Provider hand-off "
        "section was dropped — customers no longer see the doorway into "
        "the SP roster from the S5 funnel."
    )


def test_f4_3_connect_sp_cta_uses_real_endpoint_not_anchor(s5_source):
    """The CTA must use `url_for('fiesta_service_providers.index')` so
    the rendered href resolves to a real page. The orphan `#s6` anchor
    that existed in an earlier draft must NOT appear anywhere in the
    template — it's a stub that does nothing on click."""
    # The url_for must be present in the SP CTA section.
    assert "url_for('fiesta_service_providers.index')" in s5_source, (
        "F4.3 regression: the Service-Providers CTA is no longer using "
        "url_for('fiesta_service_providers.index'). It may have reverted "
        "to a hardcoded `href=\"#s6\"` anchor (the original orphan that "
        "did nothing on click)."
    )
    # And `#s6` must be gone from the template.
    assert "#s6" not in s5_source, (
        "F4.3 regression: the orphan `#s6` anchor reappeared in the S5 "
        "template. It was the original placeholder CTA that did nothing — "
        "use a real route (url_for('fiesta_service_providers.index'))."
    )


def test_f4_3_connect_sp_cta_resolves_via_url_for(app, s5_source):
    """Resolution check WITHOUT needing an authenticated session: use
    Flask's url_for() in a test request context to verify that the
    'fiesta_service_providers.index' endpoint resolves to a real path
    (not a 404 or BuildError). This is cheaper than a full GET /reduce-tax/
    (which requires @login_required + a User fixture + the property and
    SP blueprints' DB tables) and equally definitive."""
    from flask import url_for
    # The template references `url_for('fiesta_service_providers.index')`
    # — verify that endpoint exists in the app's url_map and resolves to
    # a real path.
    with app.test_request_context():
        resolved = url_for("fiesta_service_providers.index")
    assert resolved.startswith("/"), (
        f"F4.3 regression: url_for('fiesta_service_providers.index') "
        f"returned {resolved!r} — not an absolute path."
    )
    assert "service-provider" in resolved.lower() or "service_provider" in resolved.lower(), (
        f"F4.3 regression: resolved path {resolved!r} does not look like "
        "a service-providers page URL. The blueprint url_prefix may have "
        "drifted; update the assertion AND verify nothing else depends "
        "on the old path."
    )


# --------------------------------------------------------------------------- #
# F4.4 — "Set up home-office rent" CTA must link to /property.
# --------------------------------------------------------------------------- #


def test_f4_4_home_office_cta_present_with_correct_label(s5_source):
    """The S5 page must contain the home-office-rent CTA section. This
    is the doorway into the property setup flow — one of the highest-
    Rs deduction categories foreign-income earners miss."""
    assert "Home-office rent" in s5_source, (
        "F4.4 regression: the 'Home-office rent' CTA section was removed "
        "from the S5 page. Customers no longer see the doorway into the "
        "property setup flow."
    )
    assert "Set up your home-office rent" in s5_source, (
        "F4.4 regression: the home-office CTA button label was changed "
        "or removed. Pin label 'Set up your home-office rent' so the "
        "button is recognisable across the funnel."
    )


def test_f4_4_home_office_cta_links_to_property(s5_source):
    """The CTA must link to /property. We allow either:
      - hardcoded `href=\"/property\"` (current implementation)
      - or `url_for('fiesta_property.index')` / similar that resolves to /property
    The grep below accepts both."""
    # Find the home-office CTA section by anchoring on the label.
    # The <a class=\"s5-cta__btn\" href=\"...\"> is the button right
    # below the section text.
    section_re = re.compile(
        r"Home-office rent:.*?<a\b[^>]*class\s*=\s*\"s5-cta__btn\"[^>]*href\s*=\s*\"([^\"]+)\"",
        re.IGNORECASE | re.DOTALL,
    )
    match = section_re.search(s5_source)
    assert match is not None, (
        "F4.4 regression: could not locate the home-office CTA <a> tag "
        "in the S5 template. Either the section was restructured or the "
        "btn class drifted."
    )
    href = match.group(1)
    # Must resolve to /property (either hardcoded or via url_for).
    is_hardcoded_property = href == "/property" or href.startswith("/property/") or href.startswith("/property?")
    is_url_for_property = "url_for(" in href and "property" in href.lower()
    assert is_hardcoded_property or is_url_for_property, (
        f"F4.4 regression: home-office CTA href={href!r} does not point "
        "at the /property setup page. Expected `/property` or a Jinja "
        "url_for(...) that resolves to it."
    )


def test_f4_4_property_endpoint_resolves(app):
    """The /property route the home-office CTA points to must exist in
    the app's url_map. We test by GET-ing the path directly (the route
    is also @login_required, but the redirect-to-login response is a
    302/3xx, NOT a 404 — that's what we're checking)."""
    with app.test_client() as c:
        r = c.get("/property", follow_redirects=False)
    # 404 = route doesn't exist (regression we're guarding against).
    # 302/308 = exists but auth-redirects (expected). 200 = exists and
    # public (also fine). 405 = exists but wrong method (fine).
    assert r.status_code != 404, (
        f"F4.4 regression: GET /property returned 404 — the route the "
        "home-office CTA points to no longer exists. Either the blueprint "
        "was renamed/dropped or its url_prefix moved."
    )


# --------------------------------------------------------------------------- #
# Combined — both CTAs sit below the deductions grid in template order.
# --------------------------------------------------------------------------- #


def test_f4_3_and_f4_4_both_below_grid_in_template(s5_source):
    """Both CTAs must appear AFTER the S5 `<section class="s5-grid">`
    in the template source (== DOM order in render). If they slide
    above the grid by accident, the customer misses the actual
    deductions walk-through they came for.

    Template-source comparison avoids the cost of a full auth'd
    GET /reduce-tax/ in the test suite."""
    grid_pos = s5_source.find('class="s5-grid"')
    sp_cta_pos = s5_source.find("Connect your Service Providers")
    ho_cta_pos = s5_source.find("Set up your home-office rent")
    assert grid_pos > 0, "S5 deductions grid missing — page broken upstream of F4 work."
    assert sp_cta_pos > grid_pos, (
        "F4.3 layout regression: 'Connect your Service Providers' CTA "
        "appears BEFORE the s5-grid in the template. The CTA must be a "
        "footer to the deductions grid, not a header."
    )
    assert ho_cta_pos > grid_pos, (
        "F4.4 layout regression: 'Set up your home-office rent' CTA "
        "appears BEFORE the s5-grid in the template. The CTA must be a "
        "footer to the deductions grid, not a header."
    )
