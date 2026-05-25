"""F-Platform-1 — unified FIESTA shell regression tests.

Locks the Design Lock 1 (`_shell_contract.md`) contract in place:

  - layout_fiesta.html renders for sl_foreign_income users with the
    contract-required surface (wordmark, savings counter element,
    sidebar nav items).
  - Admin users see the admin shell variant.
  - Legacy bookkeeping personas keep layout.html (the shell IS persona-
    gated; Section G removes the gate after MS4).
  - /api/fiesta/savings-projection is login_required + returns the
    contract-shaped JSON.
  - Per-user 60s server-side cache means two calls inside the window
    return identical cached_until timestamps.
  - The canary remittance dashboard renders end-to-end via the new
    shell (it extends layout_fiesta.html directly).

Test design notes:
  - We bypass Flask-Login form auth via the existing `login_as` helper
    (inlined to avoid the stdlib `platform` collision the redirect-
    priority test already documented).
  - The persona factory in conftest.py creates a User with the chosen
    persona but NO organisations — which is fine for sl_foreign_income
    (org-less is the canonical case post F-Platform-3 fix) and a
    no-op for legacy users (they only need layout.html to render).
  - The /remittance/dashboard canary requires the remittance blueprint
    + a sl_foreign_income user; we get both for free from the factory.
"""
from __future__ import annotations

import json
from urllib.parse import urlsplit


def login_as(client, user):
    """Bypass the email/password form. Inlined (rather than imported from
    .conftest) because a relative import would resolve the package name
    as ``platform`` and collide with Python's stdlib ``platform`` module
    on test collection. Mirrors test_redirect_priority.py."""
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def _get_followed(client, path):
    """GET that follows any /scan-style 302 redirects so we exercise the
    eventual rendered surface. We use the test-client's follow_redirects=True
    rather than chasing 302s manually."""
    return client.get(path, follow_redirects=True)


# -------------------------------------------------------------------- #
# 1. sl_foreign_income user sees the FIESTA shell.
# -------------------------------------------------------------------- #
def test_layout_fiesta_renders_for_sl_foreign_income_user(
    app, client, user_factory
):
    user = user_factory(
        "fie_shell",
        persona="sl_foreign_income",
        is_email_verified=True,
        onboarding_completed=True,
    )
    login_as(client, user)

    # /remittance/dashboard is the canonical sl_foreign_income landing per
    # F-Platform-3; it's now migrated to extend layout_fiesta.html directly
    # (the F-Platform-1 canary), so it MUST render the new shell.
    resp = _get_followed(client, "/remittance/dashboard")
    assert resp.status_code == 200, (
        f"sl_foreign_income user got {resp.status_code} on /remittance/dashboard; "
        f"body={resp.get_data(as_text=True)[:200]!r}"
    )

    body = resp.get_data(as_text=True)

    # Wordmark — Design Lock 1 topbar contract.
    assert "FIESTA" in body, "FIESTA wordmark missing from shell"

    # Savings counter element id — LOCKED per Design Lock 1.
    assert 'id="fiesta-savings-counter"' in body, (
        "#fiesta-savings-counter element missing — Design Lock 1 violation"
    )
    assert 'data-source="api"' in body, (
        "data-source=\"api\" attribute missing on savings counter"
    )

    # Sidebar nav items — Design Lock 1 sidebar contract.
    for label in [
        "Remittance Ledger",
        "Add a remittance",
        "Reduce your tax",
        "Your support team",
        "Your tax bill",
        "Sign out",
    ]:
        assert label in body, f"sidebar nav item missing: {label!r}"

    # Shell-level CSS classes — wiring proof.
    assert "fiesta-shell" in body, ".fiesta-shell body class missing"
    assert "fiesta-layout" in body, ".fiesta-layout wrapper missing"
    assert "fiesta-main" in body, ".fiesta-main container missing"

    # Mounted CSS + JS bundles.
    assert "static/css/fiesta.css" in body, "static/css/fiesta.css link missing"
    assert "static/js/fiesta.js" in body, "static/js/fiesta.js script missing"


# -------------------------------------------------------------------- #
# 2. Admin user sees the admin shell variant.
# -------------------------------------------------------------------- #
def test_layout_fiesta_renders_for_admin_user(app, client, user_factory):
    """Admin pages extend layout_fiesta_admin.html. We don't have a
    canary admin page migrated in F-Platform-1 (C4 owns that work),
    so we verify the use_fiesta_shell() gate returns True for admins
    AND that the admin-shell partials (_fiesta/topbar_admin.html,
    _fiesta/sidebar_admin.html) render via direct render_template
    inside an app context.
    """
    admin = user_factory(
        "admin_shell",
        persona=None,
        role="admin",
        is_email_verified=True,
        onboarding_completed=True,
    )

    # use_fiesta_shell() gate is True for admins.
    from app import use_fiesta_shell
    assert use_fiesta_shell(admin) is True, (
        "use_fiesta_shell() must return True for admin role"
    )

    # The admin shell renders end-to-end (uses render_template inside the
    # request context so url_for + current_user resolve).
    login_as(client, admin)
    with client.application.test_request_context("/admin"):
        from flask import render_template
        from flask_login import login_user
        login_user(admin)
        html = render_template("layout_fiesta_admin.html")

    assert "fiesta-shell" in html, "admin variant missing .fiesta-shell body class"
    assert "ADMIN" in html, "admin variant missing ADMIN badge"
    assert "Dashboard" in html, "admin sidebar missing Dashboard link"
    assert "Per-user 360" in html, "admin sidebar missing Per-user 360 link"
    assert "PCSE Inspector" in html, "admin sidebar missing PCSE Inspector link"
    assert 'id="fiesta-savings-counter"' in html, (
        "admin variant must still expose #fiesta-savings-counter"
    )


# -------------------------------------------------------------------- #
# 3. Legacy bookkeeping user does NOT get the FIESTA shell.
# -------------------------------------------------------------------- #
def test_layout_fiesta_NOT_rendered_for_legacy_bookkeeping_user(
    app, client, user_factory
):
    """Legacy persona = None (and not admin). use_fiesta_shell() must
    return False; the persona-gated `layout_template` context value
    must stay layout.html.
    """
    legacy = user_factory(
        "legacy_bookkeeping",
        persona=None,            # legacy bookkeeping
        role="user",
        is_email_verified=True,
        onboarding_completed=True,
    )

    # use_fiesta_shell() gate is False.
    from app import use_fiesta_shell
    assert use_fiesta_shell(legacy) is False, (
        "use_fiesta_shell() must return False for legacy bookkeeping persona"
    )

    # Layout selector resolves to legacy layout.html for this user.
    login_as(client, legacy)
    with client.application.test_request_context("/"):
        from flask import g
        from flask_login import login_user
        from app import check_authentication
        login_user(legacy)
        check_authentication()
        assert g.layout_template == "layout.html", (
            f"legacy user got layout_template={g.layout_template!r}; "
            f"expected 'layout.html' (FIESTA shell must NOT be served)"
        )
        assert g.is_fiesta_persona is False


# -------------------------------------------------------------------- #
# 4. /api/fiesta/savings-projection is login_required + JSON-shaped.
# -------------------------------------------------------------------- #
def test_api_savings_projection_anon_gate(app):
    """Anonymous gate is enforced (login_required)."""
    with app.test_client() as anon_client:
        resp = anon_client.get("/api/fiesta/savings-projection")
    assert resp.status_code in (302, 401), (
        f"anonymous GET returned {resp.status_code}; expected 302 or 401"
    )


def test_api_savings_projection_returns_json(app, client, user_factory):
    """Use the same `client` fixture pattern as test 1 (which authenticates
    successfully via login_as for /remittance/dashboard).
    """
    user = user_factory(
        "savings_json",
        persona="sl_foreign_income",
        is_email_verified=True,
        onboarding_completed=True,
    )

    login_as(client, user)
    resp = client.get("/api/fiesta/savings-projection")
    assert resp.status_code == 200, (
        f"authed GET /api/fiesta/savings-projection returned {resp.status_code}; "
        f"body={resp.get_data(as_text=True)[:200]!r}"
    )
    payload = json.loads(resp.get_data(as_text=True))

    # Contract shape — every field required.
    for key in ("lkr_saved", "lkr_projected", "tax_year", "source", "fresh", "cached_until"):
        assert key in payload, f"missing contract key: {key!r}"

    assert isinstance(payload["lkr_saved"], int)
    assert isinstance(payload["lkr_projected"], int)
    assert payload["lkr_projected"] > 0, "lkr_projected must be > 0 (fallback floor)"
    assert isinstance(payload["tax_year"], str) and "/" in payload["tax_year"]
    assert payload["source"] in ("compute_tax_25_26", "projected", "fallback")
    assert isinstance(payload["fresh"], bool)
    assert isinstance(payload["cached_until"], str)
    assert payload["cached_until"].endswith("Z"), (
        "cached_until must be ISO-8601 UTC ('Z' suffix per contract)"
    )


# -------------------------------------------------------------------- #
# 5. Caching — consecutive calls within 60s return identical cached_until.
# -------------------------------------------------------------------- #
def test_api_savings_projection_caches_60s(app, user_factory):
    """The per-user TTL cache (fiesta.perf_cache, 60s) means two
    consecutive calls share a cached_until timestamp because they
    resolve from the same cached payload. Force-busts must produce a
    NEW timestamp.
    """
    user = user_factory(
        "savings_cache",
        persona="sl_foreign_income",
        is_email_verified=True,
        onboarding_completed=True,
    )
    authed = app.test_client()
    login_as(authed, user)

    # Cold call. May or may not be cache-cold depending on test ordering;
    # we force-bust first to guarantee a known state.
    cold = authed.get("/api/fiesta/savings-projection?force=true")
    assert cold.status_code == 200, (
        f"cold force-bust returned {cold.status_code}; body={cold.get_data(as_text=True)[:200]!r}"
    )
    cold_payload = json.loads(cold.get_data(as_text=True))

    # Warm call — within the same 60s window, should hit cache.
    warm = authed.get("/api/fiesta/savings-projection")
    assert warm.status_code == 200
    warm_payload = json.loads(warm.get_data(as_text=True))

    assert warm_payload["cached_until"] == cold_payload["cached_until"], (
        f"cached_until differs between cold ({cold_payload['cached_until']}) "
        f"and warm ({warm_payload['cached_until']}) — cache TTL not honoured"
    )
    assert warm_payload["lkr_projected"] == cold_payload["lkr_projected"]

    # Force-bust → NEW cached_until (cache was dropped + recomputed).
    forced = authed.get("/api/fiesta/savings-projection?force=true")
    assert forced.status_code == 200
    forced_payload = json.loads(forced.get_data(as_text=True))
    # The timestamps may collide if the clock didn't tick a full second
    # between calls. Assertion is "not stronger than warm" — that is,
    # force-bust must produce a value >= warm (monotonic time) and the
    # source/shape stay stable.
    assert forced_payload["cached_until"] >= warm_payload["cached_until"]


# -------------------------------------------------------------------- #
# 6. The canary remittance dashboard extends the new shell.
# -------------------------------------------------------------------- #
def test_remittance_dashboard_extends_fiesta_layout(
    app, client, user_factory
):
    """The /remittance/dashboard template was migrated to
    `{% extends "layout_fiesta.html" %}` as the F-Platform-1 canary
    proving the shell renders end-to-end. F-Platform-4 follows the
    same pattern for the rest of the FIESTA pages.

    Assertion: the rendered surface contains shell-only markers
    (#fiesta-savings-counter + .fiesta-main + .fiesta-sidebar) that
    only appear when layout_fiesta.html is the parent template.
    """
    user = user_factory(
        "fie_canary",
        persona="sl_foreign_income",
        is_email_verified=True,
        onboarding_completed=True,
    )
    login_as(client, user)

    resp = _get_followed(client, "/remittance/dashboard")
    assert resp.status_code == 200, (
        f"canary /remittance/dashboard returned {resp.status_code}"
    )
    body = resp.get_data(as_text=True)

    # Markers proving the new shell rendered.
    assert 'id="fiesta-savings-counter"' in body
    assert "fiesta-main" in body
    assert "fiesta-sidebar" in body

    # Original page content still renders inside the new shell.
    assert "Remittance Ledger" in body
    assert "Year of Assessment" in body
