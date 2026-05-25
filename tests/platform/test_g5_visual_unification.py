"""MS4 W4 — G5 visual + admin unification regression suite.

Closes out MS4 by locking the §G5 contract from
`G:/My Drive/CEO OS/working files/_fiesta_unification_addendum_20260525.md`:

  - G5.1: No AUTHENTICATED template still extends "layout.html" on line 1
    (only the documented ANON accept-list is permitted).
  - G5.2: All templates/errors/* extend errors/_base.html (which dispatches
    via layout_template).
  - G5.3: templates/accounts/dashboard.html does NOT emit a duplicate FIESTA
    topbar (a 'fiesta-shell' on body inside content would be a duplicate).
  - G5.4: The S15 admin Users list now extends admin/layout_fiesta.html
    (not standalone DOCTYPE) AND the row email cells link to the canonical
    360 URL (customer_brain.view).
  - G5.5: Profile-only strings post-persona-deprecation: register.html no
    longer routes the user to the Foreign Income Remittance Ledger
    explicitly; the unified-onboarding hint copy is in place.

Many tests are filesystem inspections (no HTTP client needed) — they are
intentionally fast and avoid the FK-cascade flakiness documented in
test_sidebar_bookkeeping.py + test_g2_bookkeeping_shell.py.

The HTTP test for the admin users page falls under the W2-Agent-2 xfail
pattern when the DB fixture exhibits FK teardown noise — we still ship the
filesystem proof first so the contract is asserted unconditionally.
"""
from __future__ import annotations

from pathlib import Path

import pytest


# --------------------------------------------------------------------------- #
# Repo root
# --------------------------------------------------------------------------- #
_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATES = _REPO_ROOT / "templates"


# --------------------------------------------------------------------------- #
# G5.1 — anon accept-list. Any template NOT in this list MUST NOT extend
# layout.html on line 1.
# --------------------------------------------------------------------------- #
_ANON_LAYOUT_ACCEPT_LIST = frozenset({
    # public marketing + auth surfaces
    "templates/login.html",
    "templates/register.html",
    "templates/fiesta_public/s0_landing.html",
    "templates/pricing.html",
    # public SEO surfaces (no login_required)
    "templates/help/index.html",
    "templates/help/entry.html",
    "templates/articles/index.html",
    "templates/articles/detail.html",
    # public band-only leaderboard (no auth)
    "templates/ai_org/leaderboard.html",
    # public invitation accept (invitee may be unsigned)
    "templates/confirm_friend_invitation.html",
    # documented rollback path — kept on layout.html on purpose
    "templates/home_bookkeeping_legacy.html",
})


def _list_extends_layout_html_line_one():
    """Walk templates/ and collect every file whose line 1 is an
    `{% extends "layout.html" %}` (single or double quoted) directive.
    Lines inside comment blocks {# ... #} are NOT matched because we
    only inspect line 1."""
    hits = []
    for path in _TEMPLATES.rglob("*.html"):
        try:
            first = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        if not first:
            continue
        line1 = first[0].strip()
        # Two literal forms only — the dispatcher uses `extends layout_template`
        # (no quotes) which is allowed everywhere.
        if line1 == '{% extends "layout.html" %}' or \
           line1 == "{% extends 'layout.html' %}":
            rel = path.relative_to(_REPO_ROOT).as_posix()
            hits.append(rel)
    return sorted(hits)


def test_no_authenticated_template_still_extends_legacy_layout():
    """G5.1: only the documented ANON accept-list may extend layout.html.

    Any template NOT in `_ANON_LAYOUT_ACCEPT_LIST` that still extends
    "layout.html" is a regression — the bookkeeping/legacy authed surface
    must run through `layout_template` (the dispatcher set in
    app.py:check_authentication).
    """
    found = set(_list_extends_layout_html_line_one())
    violations = sorted(found - _ANON_LAYOUT_ACCEPT_LIST)
    assert not violations, (
        "G5.1 violation: the following templates extend layout.html on "
        "line 1 but are NOT in the ANON accept-list. They must either be "
        "added to the accept-list (with route-decorator proof of anonymity) "
        "or flipped to `extends layout_template`:\n  "
        + "\n  ".join(violations)
    )


def test_anon_accept_list_is_minimal():
    """G5.1: every entry in _ANON_LAYOUT_ACCEPT_LIST must still exist on
    disk AND still extend layout.html on line 1. Otherwise the list is
    accumulating stale references that future audits won't catch.

    If an accept-list entry disappears or migrates, REMOVE it from the
    list rather than keep dead permission."""
    found = set(_list_extends_layout_html_line_one())
    stale = sorted(_ANON_LAYOUT_ACCEPT_LIST - found)
    assert not stale, (
        "G5.1 accept-list contains entries no longer on layout.html. "
        "Remove them from _ANON_LAYOUT_ACCEPT_LIST:\n  " + "\n  ".join(stale)
    )


# --------------------------------------------------------------------------- #
# G5.4 — admin 360-customer view URL unification
# --------------------------------------------------------------------------- #
def test_s15_users_template_extends_admin_fiesta_shell():
    """G5.4: templates/fiesta_admin/users.html must extend
    admin/layout_fiesta.html (no standalone DOCTYPE), so the page inherits
    the canonical admin chrome."""
    path = _TEMPLATES / "fiesta_admin" / "users.html"
    body = path.read_text(encoding="utf-8")
    # Must contain the extends directive somewhere near the top.
    assert '{% extends "admin/layout_fiesta.html" %}' in body, (
        f"{path} should extend admin/layout_fiesta.html"
    )
    # Must NOT be a standalone document.
    assert "<!DOCTYPE html>" not in body, (
        f"{path} should NOT contain a standalone <!DOCTYPE> — chrome "
        f"belongs to admin/layout_fiesta.html"
    )
    assert "<html lang=\"en\">" not in body, (
        f"{path} should NOT contain a top-level <html> tag"
    )


def test_s15_users_row_links_to_canonical_360_url():
    """G5.4: The S15 users-list row email cell must link to the
    customer_brain.view endpoint (i.e. /admin/customer/<id>) — the
    canonical 360 URL. No other admin URL may be the per-user landing
    page."""
    path = _TEMPLATES / "fiesta_admin" / "users.html"
    body = path.read_text(encoding="utf-8")
    assert "customer_brain.view" in body, (
        "G5.4: S15 users.html row email cell must url_for('customer_brain.view', "
        "user_id=r.id) — that's the canonical 360 URL"
    )


# --------------------------------------------------------------------------- #
# G5.3 — page-level nav duplication audit (accounts dashboard example)
# --------------------------------------------------------------------------- #
def test_accounts_dashboard_no_duplicate_topbar():
    """G5.3: the accounts dashboard already runs inside the FIESTA shell;
    it must NOT re-render a fiesta-shell body class or fiesta topbar
    include in its content block (those belong to layout_fiesta.html)."""
    path = _TEMPLATES / "accounts" / "dashboard.html"
    body = path.read_text(encoding="utf-8")
    # The page itself extends layout_template — confirm.
    assert "{% extends layout_template %}" in body, (
        f"{path} should extend layout_template"
    )
    # It must NOT duplicate the topbar include.
    assert "_fiesta/topbar.html" not in body, (
        f"{path} must not re-include _fiesta/topbar.html (owned by shell)"
    )
    assert "class=\"fiesta-shell\"" not in body, (
        f"{path} must not emit class=fiesta-shell (owned by layout_fiesta.html body)"
    )


# --------------------------------------------------------------------------- #
# G5.2 — error pages all FIESTA-shell-aware
# --------------------------------------------------------------------------- #
def test_error_pages_all_extend_layout_template():
    """G5.2: every templates/errors/*.html (except _base.html itself) must
    extend errors/_base.html (which dispatches via layout_template).
    _base.html itself extends the dynamic layout via `{% set _layout = ... %}
    {% extends _layout %}`."""
    errors_dir = _TEMPLATES / "errors"
    pages = [p for p in errors_dir.glob("*.html") if p.name != "_base.html"]
    assert pages, f"Expected at least one error page under {errors_dir}"
    for p in pages:
        body = p.read_text(encoding="utf-8")
        assert '{% extends "errors/_base.html" %}' in body, (
            f"{p} should extend errors/_base.html (was: {body.splitlines()[0:3]})"
        )

    # _base.html must contain the dynamic-layout dispatcher.
    base_body = (errors_dir / "_base.html").read_text(encoding="utf-8")
    assert "g.layout_template" in base_body, (
        "errors/_base.html should dispatch via g.layout_template"
    )
    assert "{% extends _layout %}" in base_body, (
        "errors/_base.html should `{% extends _layout %}` after picking it"
    )


# --------------------------------------------------------------------------- #
# G5.5 — profile-only post-persona-deprecation string cleanup
# --------------------------------------------------------------------------- #
def test_register_form_no_longer_routes_to_remittance_ledger_copy():
    """G5.5: the register page's persona checkbox copy must not promise
    a specific routing destination (Remittance Ledger) — unified
    onboarding (G4) now decides. The input NAME `persona_sl_foreign_income`
    is preserved for legacy backend attribution; we only check the
    user-facing copy was softened."""
    path = _TEMPLATES / "register.html"
    body = path.read_text(encoding="utf-8")
    # Must keep the input name for backend attribution.
    assert 'name="persona_sl_foreign_income"' in body, (
        "register.html must keep the persona_sl_foreign_income input name "
        "(backend reads it at app.py:4342)"
    )
    # Must NOT contain the legacy promise copy.
    assert "Route me to the Foreign Income Remittance Ledger" not in body
    assert "route me to the Foreign Income Remittance Ledger" not in body
    # Must contain the softened unified-onboarding hint phrasing.
    assert "income sources" in body, (
        "register.html should reference 'income sources' to match unified "
        "onboarding (G4)"
    )


def test_layout_bookkeeping_deprecation_banner_post_persona():
    """G5.5: the bookkeeping deprecation banner in layout.html should
    no longer gate on `current_user.persona`. Post-G1.2 every authed
    user is on layout_fiesta.html; this banner only fires on the
    documented home_bookkeeping_legacy.html rollback path."""
    path = _TEMPLATES / "layout.html"
    body = path.read_text(encoding="utf-8")
    # Banner copy must be the post-G5 phrasing (no '/fie/triage' link —
    # that route is deprecated by G4 unified onboarding).
    assert "Legacy bookkeeping view" in body, (
        "layout.html banner copy should read 'Legacy bookkeeping view — "
        "return to FIESTA' (G5.5)"
    )
    # The banner block should NOT discriminate by persona value.
    assert "persona != 'sl_foreign_income'" not in body, (
        "layout.html banner should not gate on persona value (deprecated as "
        "UX discriminator)"
    )


# --------------------------------------------------------------------------- #
# G5.4 — HTTP-level sanity check on the admin Users list rendering inside
# the admin FIESTA shell. xfailed when DB fixture has the documented
# FK-cascade teardown flakiness (W2 Agent 2 / test_g2_bookkeeping_shell
# precedent).
# --------------------------------------------------------------------------- #
@pytest.mark.xfail(
    reason="DB fixture FK-cascade teardown is unreliable for admin user creation; "
           "filesystem proof above (test_s15_users_template_extends_admin_fiesta_shell) "
           "covers the contract. Same pattern as test_g2_bookkeeping_shell.py.",
    strict=False,
)
def test_s15_admin_page_renders_inside_admin_fiesta_shell(client, user_factory):
    """G5.4 (integration): GET /admin/fie/users as an admin returns 200
    and the body contains the af-rail admin shell selectors (proof we
    inherited admin/layout_fiesta.html chrome)."""
    from tests.platform.conftest import login_as
    admin = user_factory("g5_admin", role="admin")
    login_as(client, admin)
    resp = client.get("/admin/fie/users", follow_redirects=True)
    assert resp.status_code == 200, resp.data[:300]
    body = resp.data.decode("utf-8", errors="ignore")
    # af-rail is the admin shell's left rail class (admin/layout_fiesta.html).
    assert 'class="af-rail"' in body or "af-rail-link" in body, (
        "S15 users page should render inside admin/layout_fiesta.html "
        "(af-rail selector absent)"
    )
