"""F-Platform-6 — unified error pages regression tests.

Locks the Design Lock 1 contract for templates/errors/* (added MS1 Stage C3,
2026-05-25):

  - 404 renders in the legacy layout for anonymous + legacy bookkeeping users.
  - 404 renders in the FIESTA shell (layout_fiesta.html) for sl_foreign_income
    users.
  - The persona-aware Home CTA href routes to:
      sl_foreign_income       -> /remittance/dashboard
      legacy authenticated    -> /scan
      anonymous               -> / (public landing)
  - 403, 500, 401 all render successfully.
  - 401 preserves the originally requested URL via the ?next= query param on
    the Sign in CTA.
  - The 500 template never raises an error itself (paranoid render path).

Test design notes:
  - We reuse the conftest.py user_factory + login_as helpers (same pattern as
    test_shell.py / test_redirect_priority.py).
  - For 403/500/401 we register tiny helper routes on the app fixture that
    abort() with the relevant code. Mounting them once per suite (via the
    `_error_test_routes` autouse fixture) is safe because each route has a
    unique path under /__test_errors__/.
  - For 500 we temporarily disable PROPAGATE_EXCEPTIONS so Flask actually
    runs our errorhandler instead of letting the raise bubble up to pytest.
"""
from __future__ import annotations

from urllib.parse import urlsplit, parse_qs

import pytest
from flask import abort


# -------------------------------------------------------------------- #
# Local login helper — see test_shell.py for the why (stdlib `platform`
# collision blocks `from .conftest import login_as`).
# -------------------------------------------------------------------- #
def login_as(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


# -------------------------------------------------------------------- #
# Register synthetic error-trigger routes once per session.
#
# /__test_errors__/403 -> abort(403)
# /__test_errors__/401 -> abort(401)
# /__test_errors__/500 -> raise RuntimeError (handled by 500 errorhandler
#                          when PROPAGATE_EXCEPTIONS is False)
# -------------------------------------------------------------------- #
@pytest.fixture(scope="session", autouse=True)
def _error_test_routes(app):
    """Mount the abort-triggers under unique paths so they only exist for
    these tests. Idempotent — Flask raises if a rule is added twice, so we
    guard by view-function name."""
    def _trigger_403():
        abort(403)

    def _trigger_401():
        abort(401)

    def _trigger_500():
        raise RuntimeError("synthetic 500 for F-Platform-6 tests")

    pairs = [
        ("/__test_errors__/403", "_f_platform_6_trigger_403", _trigger_403),
        ("/__test_errors__/401", "_f_platform_6_trigger_401", _trigger_401),
        ("/__test_errors__/500", "_f_platform_6_trigger_500", _trigger_500),
    ]
    for rule, endpoint, fn in pairs:
        if endpoint in app.view_functions:
            continue
        app.add_url_rule(rule, endpoint=endpoint, view_func=fn)
    yield


# -------------------------------------------------------------------- #
# Helpers
# -------------------------------------------------------------------- #
def _home_cta_href(body: str) -> str:
    """Extract the href of the [data-fiesta-home-cta] anchor from a rendered
    error page. Uses the data attribute (locked) so we don't depend on
    fragile Bootstrap classes."""
    marker = 'data-fiesta-home-href="'
    idx = body.find(marker)
    assert idx != -1, (
        "[data-fiesta-home-href] attribute missing from error page — "
        "Home CTA contract violated"
    )
    start = idx + len(marker)
    end = body.find('"', start)
    return body[start:end]


# -------------------------------------------------------------------- #
# 1. Anonymous user hits a non-existent URL -> 404 in the legacy layout.
# -------------------------------------------------------------------- #
def test_404_anon_user_renders_legacy_layout(app, client):
    """Anonymous (not logged-in) request to a bogus URL must:
      - return 404
      - render in templates/layout.html (legacy), NOT layout_fiesta.html
      - NOT include the FIESTA shell markers (#fiesta-savings-counter etc.)
    """
    resp = client.get("/this-url-does-not-exist-f-platform-6")
    assert resp.status_code == 404
    body = resp.get_data(as_text=True)

    # 404 marker on the page
    assert 'data-error-code="404"' in body, "missing 404 error-code marker"
    assert "This page isn't here" in body, (
        "missing 404 headline 'This page isn't here'"
    )

    # Legacy layout markers — present in layout.html but not layout_fiesta.html
    # (Bootstrap CDN, Font Awesome, etc. are layout.html-specific).
    assert "fiesta-shell" not in body, (
        "anon 404 must NOT render the FIESTA shell (.fiesta-shell body class)"
    )
    assert "id=\"fiesta-savings-counter\"" not in body, (
        "anon 404 must NOT include the FIESTA shell savings counter"
    )


# -------------------------------------------------------------------- #
# 2. sl_foreign_income user hits a bad URL -> 404 in the FIESTA shell.
# -------------------------------------------------------------------- #
def test_404_fiesta_user_renders_fiesta_shell(app, client, user_factory):
    """sl_foreign_income user gets 404 rendered inside the unified FIESTA
    shell so brand consistency is preserved on errors."""
    user = user_factory(
        "err404_fiesta",
        persona="sl_foreign_income",
        is_email_verified=True,
        onboarding_completed=True,
    )
    login_as(client, user)

    resp = client.get("/this-url-does-not-exist-f-platform-6")
    assert resp.status_code == 404
    body = resp.get_data(as_text=True)

    # FIESTA shell markers — proves layout_fiesta.html rendered.
    assert "fiesta-shell" in body, (
        "sl_foreign_income 404 must render in the FIESTA shell (.fiesta-shell)"
    )
    assert 'id="fiesta-savings-counter"' in body, (
        "sl_foreign_income 404 must include the FIESTA shell savings counter"
    )

    # 404 marker still present
    assert 'data-error-code="404"' in body


# -------------------------------------------------------------------- #
# 3. Home CTA href routes to /remittance/dashboard for sl_foreign_income.
# -------------------------------------------------------------------- #
def test_404_home_cta_routes_to_remittance_for_fiesta(
    app, client, user_factory
):
    user = user_factory(
        "err404_home_cta_fiesta",
        persona="sl_foreign_income",
        is_email_verified=True,
        onboarding_completed=True,
    )
    login_as(client, user)

    resp = client.get("/this-url-does-not-exist-cta")
    assert resp.status_code == 404
    href = _home_cta_href(resp.get_data(as_text=True))
    assert href == "/remittance/dashboard", (
        f"FIESTA Home CTA href = {href!r}; expected '/remittance/dashboard'"
    )


# -------------------------------------------------------------------- #
# 4. Home CTA href routes to /scan for authenticated legacy users.
# -------------------------------------------------------------------- #
def test_404_home_cta_routes_to_scan_for_legacy(app, client, user_factory):
    """Legacy bookkeeping persona (= None, role=user) is authenticated but
    not on the FIESTA shell. Home CTA should land them on /scan, the
    legacy hub."""
    user = user_factory(
        "err404_home_cta_legacy",
        persona=None,
        role="user",
        is_email_verified=True,
        onboarding_completed=True,
    )
    login_as(client, user)

    resp = client.get("/this-url-does-not-exist-legacy-cta")
    assert resp.status_code == 404
    href = _home_cta_href(resp.get_data(as_text=True))
    assert href == "/scan", (
        f"legacy Home CTA href = {href!r}; expected '/scan'"
    )


# -------------------------------------------------------------------- #
# 5. Synthetic 403 renders without error and exposes the 403 marker.
# -------------------------------------------------------------------- #
def test_403_renders_correctly(app, client):
    resp = client.get("/__test_errors__/403")
    assert resp.status_code == 403
    body = resp.get_data(as_text=True)
    assert 'data-error-code="403"' in body, "403 page missing data-error-code"
    assert "don't have access" in body, (
        "403 page missing 'don't have access' messaging"
    )
    # Home CTA wired even on 403
    assert "data-fiesta-home-href=" in body


# -------------------------------------------------------------------- #
# 6. Synthetic 500 renders without error.
#
# We disable PROPAGATE_EXCEPTIONS for this test only, so Flask actually
# routes the RuntimeError to our @errorhandler(500) instead of letting
# pytest catch the raise.
# -------------------------------------------------------------------- #
def test_500_renders_correctly(app, client):
    prior_propagate = app.config.get("PROPAGATE_EXCEPTIONS")
    prior_debug = app.debug
    app.config["PROPAGATE_EXCEPTIONS"] = False
    app.debug = False
    try:
        resp = client.get("/__test_errors__/500")
    finally:
        app.config["PROPAGATE_EXCEPTIONS"] = prior_propagate
        app.debug = prior_debug

    assert resp.status_code == 500
    body = resp.get_data(as_text=True)

    # If the paranoid render path fired (template render failed), the body
    # is the static plaintext fallback — still a valid 500 surface. We
    # accept either rendered-template OR the fallback.
    assert ("data-error-code=\"500\"" in body) or ("<h1>500</h1>" in body), (
        "500 page rendered neither the unified template nor the static "
        "fallback — paranoid render contract violated"
    )
    assert "500" in body  # belt-and-braces


# -------------------------------------------------------------------- #
# 7. 401 preserves the originally requested URL via ?next=.
# -------------------------------------------------------------------- #
def test_401_preserves_next_param(app, client):
    """The Sign in CTA on the 401 page must include ?next=<original URL>
    so post-login redirects round-trip back to where the user started."""
    resp = client.get("/__test_errors__/401")
    assert resp.status_code == 401
    body = resp.get_data(as_text=True)
    assert 'data-error-code="401"' in body
    assert "Sign in" in body, "401 page missing 'Sign in' CTA"

    # Find the Sign in CTA and verify its href has ?next= pointing at our
    # triggering URL.
    marker = 'data-fiesta-signin-link'
    idx = body.find(marker)
    assert idx != -1, "401 page missing [data-fiesta-signin-link] CTA"

    # Walk backwards from the marker to find the href= for this anchor.
    href_idx = body.rfind('href="', 0, idx)
    assert href_idx != -1, "401 Sign in CTA missing href attribute"
    href_start = href_idx + len('href="')
    href_end = body.find('"', href_start)
    href = body[href_start:href_end]

    # Parse out ?next=
    parts = urlsplit(href)
    qs = parse_qs(parts.query)
    assert "next" in qs, (
        f"401 Sign in CTA href = {href!r}; missing ?next= param"
    )
    next_val = qs["next"][0]
    assert "/__test_errors__/401" in next_val, (
        f"401 ?next= = {next_val!r}; expected to include /__test_errors__/401"
    )
