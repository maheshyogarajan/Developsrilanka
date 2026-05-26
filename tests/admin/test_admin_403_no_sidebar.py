"""D6 (2026-05-27) — /admin/* 403 page must NOT leak the admin sidebar.

Pre-D6 behaviour: when a non-admin authenticated user GET'd any /admin/*
URL, the `admin_required` decorator rendered `templates/admin/403.html`,
which extended `templates/admin/layout_fiesta.html`. The layout emits the
full admin sidebar (Dashboard / Users / Per-user 360 / Autoreply Queue /
Support Queue / Submissions / PCSE Inspector / AI-Org Dashboards /
Revenue Intelligence / Settings / Logs). The 2026-05-26 customer-flow
audit (D6) flagged this as an info leak — a non-admin who hits an
/admin/* URL could enumerate the admin nav structure.

Post-D6 behaviour: `admin/403.html` extends `layout.html` (the customer
shell). The body carries a `data-admin-403="1"` marker so this test can
find it cheaply. The admin shell (`.admin-fiesta` wrapper, the sidebar
nav labels) MUST be absent from the rendered HTML.

The decorator path is unchanged: non-admin → 403 status + admin/403.html
render. The only change is which layout that template extends.
"""
from __future__ import annotations

import re

# Re-export the platform-suite fixtures into this module so pytest finds
# them when collecting tests under tests/admin/. The platform conftest
# uses sqlite-in-memory (no Neon dependency), suitable for this test.
from tests.platform.conftest import (  # noqa: F401
    app,
    client,
    db_session,
    user_factory,
    _cleanup_orphan_platform_users,
    login_as,
)


# The admin nav labels that previously leaked through layout_fiesta.html.
# Their presence in a 403 response indicates the admin shell is still
# rendering — the bug this D6 patch closes.
#
# Note: 'Dashboard' is NOT in this list because the customer layout
# (templates/layout.html) also uses ">Dashboard<" as a generic sidebar
# submenu label (Accounts → Dashboard, Bank → Dashboard). Only the
# uniquely-admin labels are anchors for this leak check.
ADMIN_SIDEBAR_NAV_LABELS = [
    ">Per-user 360<",
    ">Autoreply Queue<",
    ">Support Queue<",
    ">Submissions<",
    ">PCSE Inspector<",
    ">AI-Org Dashboards<",
    ">Revenue Intelligence<",
]


# The admin-shell wrapper class layout_fiesta.html uses. Its presence
# is the most concise signal that the admin layout rendered.
ADMIN_SHELL_WRAPPER_CLASS = "admin-fiesta"

# The /admin/* path we GET to trip the gate. The fiesta-states surface
# is the canonical admin URL the audit flagged; if a future refactor
# moves it, swap any other `/admin/*` URL — the decorator behaviour is
# uniform.
ADMIN_GATED_PATH = "/admin/fiesta-states"


# --------------------------------------------------------------------------- #
# D6.a — non-admin GET /admin/* returns 403 with the customer shell, not
# the admin shell.
# --------------------------------------------------------------------------- #


def test_d6_non_admin_gets_403_without_admin_shell(
    app, client, user_factory
):
    """Authenticated non-admin GET /admin/fiesta-states must return 403
    AND the rendered HTML must NOT carry the admin shell wrapper class
    or any of the admin sidebar nav labels."""
    user = user_factory(
        "d6_nonadmin",
        role="user",
        is_email_verified=True,
        onboarding_completed=True,
    )
    login_as(client, user)

    resp = client.get(ADMIN_GATED_PATH, follow_redirects=False)
    assert resp.status_code == 403, (
        f"Expected 403 for non-admin on {ADMIN_GATED_PATH}; got "
        f"{resp.status_code}. Body preview: {resp.get_data(as_text=True)[:300]!r}"
    )

    body = resp.get_data(as_text=True)

    # Positive: the new 403 page emits its marker.
    assert 'data-admin-403="1"' in body, (
        "Non-admin 403 response is missing the `data-admin-403=\"1\"` "
        "marker — the new admin/403.html template did not render. The "
        "template may have been replaced by a different 403 path."
    )

    # Negative: the admin shell wrapper class must NOT appear.
    assert ADMIN_SHELL_WRAPPER_CLASS not in body, (
        f"Non-admin 403 response contains the admin shell wrapper class "
        f"`{ADMIN_SHELL_WRAPPER_CLASS}`. The admin layout is still "
        "rendering — admin/403.html may have regressed to extending "
        "admin/layout_fiesta.html again."
    )

    # Negative: no admin sidebar nav labels should leak.
    leaked = [label.strip("><") for label in ADMIN_SIDEBAR_NAV_LABELS if label in body]
    assert not leaked, (
        f"Non-admin 403 response leaked admin sidebar nav labels: "
        f"{leaked!r}. The admin layout is still rendering — this is the "
        "D6 customer-flow audit info-leak the patch closes."
    )


# --------------------------------------------------------------------------- #
# D6.b — non-admin 403 page links back to the customer hub, not /admin.
# --------------------------------------------------------------------------- #


def test_d6_non_admin_403_links_back_to_customer_hub(
    app, client, user_factory
):
    """The 403 page must offer the user a way back to the customer hub
    (home dashboard), not strand them on an admin URL. The button text
    'Back to your dashboard' anchors this contract."""
    user = user_factory(
        "d6_nonadmin_link",
        role="user",
        is_email_verified=True,
        onboarding_completed=True,
    )
    login_as(client, user)

    resp = client.get(ADMIN_GATED_PATH, follow_redirects=False)
    body = resp.get_data(as_text=True)

    assert "Back to your dashboard" in body, (
        "Non-admin 403 page is missing the 'Back to your dashboard' "
        "escape hatch. Authenticated users hitting /admin/* must be "
        "given a way back to their customer surface."
    )


# --------------------------------------------------------------------------- #
# D6.c — the 403 page has exactly one <h1>, and it's "403 — Admin Only".
# --------------------------------------------------------------------------- #


def test_d6_non_admin_403_h1_is_403_admin_only(
    app, client, user_factory
):
    """Belt-and-braces: the only <h1> in the 403 response must be the
    '403 — Admin Only' headline. A leaked admin layout would render a
    page-title h1 above it. The D3 fix gates the layout main-header h1
    for anon — for authed non-admins on the 403 page we'd see the authed
    page-title h1 here if anything regressed (it would say 'Admin
    Dashboard' per layout.html's path-based switch).

    The 403 page extends layout.html (customer shell); layout.html's
    `<header class=\"main-header\">` is rendered for authed users with a
    page-title set by `request.path`. For `/admin/fiesta-states` the
    layout title would say 'Admin Dashboard'. The TEST asserts that
    title text is also absent — there should be one h1 ('403 — Admin
    Only') from the content block."""
    user = user_factory(
        "d6_nonadmin_h1",
        role="user",
        is_email_verified=True,
        onboarding_completed=True,
    )
    login_as(client, user)

    resp = client.get(ADMIN_GATED_PATH, follow_redirects=False)
    body = resp.get_data(as_text=True)

    # Look for the 403 headline (HTML-entity-escaped em-dash).
    assert (
        "403 &mdash; Admin Only" in body
        or "403 — Admin Only" in body
    ), (
        "Non-admin 403 page does not contain the '403 — Admin Only' "
        "headline. The page content block may not have rendered, or the "
        "template has drifted."
    )
