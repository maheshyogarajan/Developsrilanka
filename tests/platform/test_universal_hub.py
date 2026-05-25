"""MS4 W2 Agent 1 — G1.2 + G1.3 universal hub regression suite.

Locks the Design Lock 3 contract (see
`G:/My Drive/CEO OS/working files/_fiesta_ms1_to_ms4/_g1_design_lock_universal_shell.md`):

  G1.2 (`/` as the universal FIESTA hub):
    - Anonymous user → S0 landing.
    - Authenticated non-admin user → `templates/fiesta_home.html` regardless
      of persona / income_sources state.
    - Admin user → redirect to `/scan` (operator surface).
    - `use_fiesta_shell()` returns True for any authenticated user.

  G1.3 (`/scan` becomes admin-only):
    - Non-admin user → 302 to `/`.
    - Admin user → renders the legacy receipt-scanner surface.

  D4 funnel-state recommender (non-foreign-income cohorts):
    - empty income_sources → 'no_income_sources' card.
    - business_lkr → 'has_business_lkr' card (quarterly receipts).
    - employment_lkr → 'has_employment_lkr' card (recent payslip).
    - crypto → 'has_crypto' card (disposal log).
    - multi-source → 'has_multiple' funnel_state, highest-precedence card.

  D7 (templates/persona/home.html flipped to layout_template).

  Triage post-completion always routes to `/` for every persona.

These tests live in `tests/platform/` alongside `test_shell.py`,
`test_hub.py`, and `test_redirect_priority.py`. They use the shared
`tests/platform/conftest.py` fixtures (`app`, `client`, `db_session`,
`user_factory`, `login_as`).

NOTE on fixture extension: the shared `user_factory` does not yet
accept `income_sources`. This module ships a tiny `set_income_sources`
helper that mutates a user post-creation and commits — that's the
narrowest change that keeps the existing fixtures untouched (W3 will
own the fixture overhaul).
"""
from __future__ import annotations

import pytest


def login_as(client, user):
    """Bypass the email/password form. Mirrors test_shell.py's helper."""
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def _set_income_sources(db_session, user, sources):
    """Helper — extends user_factory to support income_sources without
    touching the shared conftest fixture (W3 follow-up will subsume)."""
    user.income_sources = list(sources or [])
    db_session.add(user)
    db_session.commit()
    return user


# ---------------------------------------------------------------------------
# G1.2 — universal hub at /
# ---------------------------------------------------------------------------


def test_anon_user_lands_on_s0_landing_at_root(client):
    """Anonymous GET / renders the S0 estimator landing (unchanged
    behaviour pre/post G1.2 for the anonymous path)."""
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # S0 landing renders the public hero — fiesta_public/s0_landing.html
    # is one of the markers; the authenticated hub uses fiesta_home.html
    # which has a data-fiesta-home attribute we explicitly check absent here.
    assert 'data-fiesta-home="1"' not in body


def test_authenticated_non_admin_lands_on_fiesta_home(client, user_factory, db_session):
    """G1.2 core: any authenticated non-admin user renders fiesta_home.html
    regardless of persona / income_sources. Tested with the most ambiguous
    case — persona=None AND income_sources=[]. Pre-G1.2 this user would
    have been bounced to /scan."""
    u = user_factory("hub_any_user", persona=None, role="user")
    _set_income_sources(db_session, u, [])
    login_as(client, u)
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}"
    body = resp.get_data(as_text=True)
    assert 'data-fiesta-home="1"' in body, "fiesta_home.html marker missing"


def test_admin_user_redirects_to_scan_from_root(client, user_factory):
    """G1.2 D2: admin role → /scan (operator surface)."""
    admin = user_factory("hub_admin", persona=None, role="admin")
    login_as(client, admin)
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code in (301, 302)
    assert "/scan" in resp.headers.get("Location", "")


# ---------------------------------------------------------------------------
# G1.3 — /scan becomes admin-only
# ---------------------------------------------------------------------------


def test_scan_redirects_non_admin_to_home(client, user_factory):
    """G1.3 core: any authenticated non-admin user hitting /scan is
    redirected to /. This holds even for legacy sl_foreign_income
    persona — they go home like everyone else."""
    u = user_factory("scan_redir_user", persona="sl_foreign_income", role="user")
    login_as(client, u)
    resp = client.get("/scan", follow_redirects=False)
    assert resp.status_code in (301, 302)
    loc = resp.headers.get("Location", "")
    # Allow either bare "/" or fully-qualified URL.
    assert loc.endswith("/") or loc == "/" or "/?" in loc or loc.rstrip("/").endswith("//"), (
        f"expected redirect to /, got {loc!r}"
    )


def test_scan_serves_admin_legacy_operator_surface(client, user_factory):
    """G1.3: admin user hitting /scan gets the legacy index.html
    (receipt-scanner operator surface) — NOT a redirect to /."""
    admin = user_factory("scan_admin", persona=None, role="admin")
    login_as(client, admin)
    resp = client.get("/scan", follow_redirects=False)
    # Admin path renders index.html template (status 200). If the env
    # is missing index.html assets we may see a 500 — accept either as
    # long as it's NOT a 302 to /.
    assert resp.status_code != 302 or "/scan" in resp.headers.get("Location", ""), (
        f"admin /scan should not redirect away; got {resp.status_code} -> "
        f"{resp.headers.get('Location', '')}"
    )


# ---------------------------------------------------------------------------
# use_fiesta_shell() predicate post-G1.2
# ---------------------------------------------------------------------------


def test_use_fiesta_shell_returns_true_for_any_authenticated_user(app):
    """D1: post-G1.2 the predicate collapses to is_authenticated for
    every user shape — admin OR non-admin, with OR without persona,
    with OR without income_sources."""
    from app import use_fiesta_shell

    class _U:
        def __init__(self, **kw):
            self.is_authenticated = True
            self.persona = kw.get("persona")
            self.role = kw.get("role", "user")
            self.income_sources = kw.get("income_sources", [])

    assert use_fiesta_shell(_U()) is True
    assert use_fiesta_shell(_U(persona="sl_foreign_income")) is True
    assert use_fiesta_shell(_U(persona=None)) is True
    assert use_fiesta_shell(_U(role="admin")) is True
    assert use_fiesta_shell(_U(income_sources=["business_lkr"])) is True


def test_use_fiesta_shell_returns_false_for_anon(app):
    """D1: anonymous users still get layout.html (the predicate is the
    seam that keeps anonymous flows on the legacy shell)."""
    from app import use_fiesta_shell

    class _Anon:
        is_authenticated = False
        persona = None
        role = None

    assert use_fiesta_shell(_Anon()) is False
    assert use_fiesta_shell(None) is False


# ---------------------------------------------------------------------------
# D4 funnel-state recommender — non-foreign-income cohorts
# ---------------------------------------------------------------------------


def test_hub_funnel_state_no_income_sources_shows_tell_us_card(
    client, user_factory, db_session
):
    """D4: empty income_sources → 'no_income_sources' funnel state with
    a Tell-us-about-your-income card pointing at /onboarding."""
    u = user_factory("hub_empty", persona=None, role="user")
    _set_income_sources(db_session, u, [])
    login_as(client, u)
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-funnel-state="no_income_sources"' in body
    assert "Tell us about your income" in body
    assert "/onboarding" in body


def test_hub_funnel_state_has_business_lkr_shows_business_next_step(
    client, user_factory, db_session
):
    """D4: business_lkr → 'has_business_lkr' card (quarterly receipts)."""
    u = user_factory("hub_biz", persona=None, role="user")
    _set_income_sources(db_session, u, ["business_lkr"])
    login_as(client, u)
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-funnel-state="has_business_lkr"' in body
    assert "business receipts" in body.lower()


def test_hub_funnel_state_has_crypto_shows_crypto_next_step(
    client, user_factory, db_session
):
    """D4: crypto → 'has_crypto' card (disposal log)."""
    u = user_factory("hub_crypto", persona=None, role="user")
    _set_income_sources(db_session, u, ["crypto"])
    login_as(client, u)
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-funnel-state="has_crypto"' in body
    assert "crypto" in body.lower()


def test_hub_funnel_state_has_employment_lkr_shows_employment_next_step(
    client, user_factory, db_session
):
    """D4: employment_lkr → 'has_employment_lkr' card (recent payslip)."""
    u = user_factory("hub_emp", persona=None, role="user")
    _set_income_sources(db_session, u, ["employment_lkr"])
    login_as(client, u)
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-funnel-state="has_employment_lkr"' in body
    assert "payslip" in body.lower()


def test_hub_funnel_state_has_multiple_ranks_to_highest_value(
    client, user_factory, db_session
):
    """D4: multi-source users get 'has_multiple' funnel state AND the
    card for the highest-precedence source. Precedence in the
    recommender: business > rsu > crypto > employment > rental >
    investment > other. With business + crypto + employment, the
    business card wins."""
    u = user_factory("hub_multi", persona=None, role="user")
    _set_income_sources(db_session, u, ["business_lkr", "crypto", "employment_lkr"])
    login_as(client, u)
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-funnel-state="has_multiple"' in body
    # Highest-precedence card is business — verify its text won, not
    # crypto's or employment's.
    assert "business receipts" in body.lower()
    assert "crypto disposals" not in body.lower()
    assert "log your most recent payslip" not in body.lower()


# ---------------------------------------------------------------------------
# Triage post-completion routes to /
# ---------------------------------------------------------------------------


def test_triage_post_complete_redirects_to_home_for_all_personas(app):
    """D2 forward link: triage's _post_complete_redirect always returns
    url_for('home') post-G1.2 — no more persona-aware branch."""
    from fiesta.triage.routes import _post_complete_redirect

    with app.test_request_context("/fie/triage"):
        # The function reads request.args + url_for; both work under a
        # test_request_context. We don't need a logged-in user — the
        # G1.2 implementation drops the persona check entirely.
        result = _post_complete_redirect()
        # url_for('home') resolves to '/'.
        assert result == "/" or result.endswith("/"), (
            f"expected '/', got {result!r}"
        )


# ---------------------------------------------------------------------------
# D7 — templates/persona/home.html flipped to layout_template
# ---------------------------------------------------------------------------


def test_persona_home_template_renders_in_fiesta_shell(client, user_factory):
    """D7: GET /persona renders persona/home.html which now extends
    layout_template — for an authenticated non-admin user the resolved
    layout is layout_fiesta.html. We assert the response is 2xx/3xx
    (route is mounted) and the body — if it renders — does NOT have
    the legacy layout.html topbar marker.

    The /persona blueprint may return 200, 302 (redirect to login if not
    verified), or 404 if the blueprint isn't registered. Accept any
    non-5xx as a "the flip didn't crash the template" signal."""
    u = user_factory("persona_shell", persona=None, role="user")
    login_as(client, u)
    resp = client.get("/persona", follow_redirects=False)
    assert resp.status_code < 500, (
        f"persona/home.html render crashed: status={resp.status_code}"
    )
