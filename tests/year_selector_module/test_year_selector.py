"""Tier D4 C4 -- customer year-selector tests.

Two route-level cases:

  test_default_year_renders     GET /tax-bill/2025-26 returns 200 + the
                                year selector dropdown HTML naming both
                                supported years; selected option is 2025-26.

  test_explicit_year_renders    GET /tax-bill/2024-25 returns 200 + the
                                year selector dropdown with selected
                                option = 2024-25 (prior year).

The dropdown is path-based (no ?year= query param) -- the route already
accepts <tax_year> as a URL segment, so the select onchange navigates to
/tax-bill/<year>. Tests verify both years render and the right option is
flagged selected, which is the whole user-visible contract for D4 C4.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Case 1 -- default year (most recent supported) renders.
# ---------------------------------------------------------------------------


def test_default_year_renders(client, user_a, monkeypatch):
    """GET /tax-bill/2025-26 -> 200 + dropdown shows 2025-26 selected.

    Verifies:
      - route status 200 (not the engine-error fallback, not a redirect)
      - the year-selector container is in the HTML
      - both supported years appear as <option> values
      - the current year (2025-26) is the selected option
    """
    import fiesta.paywall.gate as _gate
    monkeypatch.setattr(_gate, "is_tier_active", lambda *a, **kw: True)

    from tests.year_selector_module.conftest import login_as
    login_as(client, user_a)

    resp = client.get("/tax-bill/2025-26", follow_redirects=False)

    assert resp.status_code == 200, (
        f"Expected 200, got {resp.status_code}. "
        f"Body head: {(resp.data or b'')[:300]!r}"
    )

    html = (resp.data or b"").decode("utf-8", errors="replace")

    # The selector wrapper is in the page.
    assert 'data-testid="tb-year-selector"' in html, (
        "Year-selector container missing from /tax-bill HTML — the brief "
        "requires a dropdown to switch between tax years."
    )

    # Both supported years available as options.
    assert 'value="2025-26"' in html, "2025-26 option missing from selector"
    assert 'value="2024-25"' in html, "2024-25 option missing from selector"

    # 2025-26 is the selected option (we requested it via the URL path).
    # Match against the option line that has value="2025-26" and selected
    # marker; the template emits them on the same <option> open tag.
    assert 'value="2025-26" selected' in html, (
        "2025-26 should be the selected option when /tax-bill/2025-26 is "
        f"requested. HTML did not contain the selected marker. "
        f"Head: {html[:500]!r}"
    )


# ---------------------------------------------------------------------------
# Case 2 -- explicit prior year (2024-25) renders with that year selected.
# ---------------------------------------------------------------------------


def test_explicit_year_renders(client, user_a, monkeypatch):
    """GET /tax-bill/2024-25 -> 200 + dropdown shows 2024-25 selected.

    This is the returning-customer flow: existing user pulls up last year's
    bill. Verifies the route serves the requested year (no redirect to the
    default) and that the selector flags 2024-25 as the chosen option.
    """
    import fiesta.paywall.gate as _gate
    monkeypatch.setattr(_gate, "is_tier_active", lambda *a, **kw: True)

    from tests.year_selector_module.conftest import login_as
    login_as(client, user_a)

    resp = client.get("/tax-bill/2024-25", follow_redirects=False)

    assert resp.status_code == 200, (
        f"Expected 200 for /tax-bill/2024-25, got {resp.status_code}. "
        f"Body head: {(resp.data or b'')[:300]!r}"
    )

    html = (resp.data or b"").decode("utf-8", errors="replace")

    assert 'data-testid="tb-year-selector"' in html, (
        "Year-selector container missing on the 2024-25 view."
    )

    # 2024-25 is the selected option.
    assert 'value="2024-25" selected' in html, (
        "2024-25 should be the selected option when /tax-bill/2024-25 is "
        f"requested. Head: {html[:500]!r}"
    )

    # The OTHER year must still be present (not selected) — switcher value.
    assert 'value="2025-26"' in html, (
        "2025-26 option must still be in the selector so a customer viewing "
        "2024-25 can switch back to the current year."
    )
    # Spot-check 2025-26 is NOT the selected one.
    assert 'value="2025-26" selected' not in html, (
        "2025-26 should not be flagged selected when viewing 2024-25."
    )
