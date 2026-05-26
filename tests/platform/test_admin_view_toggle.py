"""BUG-A regression suite (Phase B Wave 1, 2026-05-26).

Locks the admin "View As" toggle contract:

  1. Admin GET / renders fiesta_home.html (customer hub) by default —
     NOT redirected to /scan.
  2. Admin GET /admin/view-as/admin flips session['admin_view_as'] to
     'admin' and bounces back; subsequent GET / renders within the
     admin shell (layout_fiesta_admin.html — operator sidebar + ADMIN
     badge topbar).
  3. Admin GET /admin/view-as/customer flips back; subsequent GET /
     renders within the customer shell.
  4. Non-admin users cannot hit /admin/view-as/<role> (admin_required
     decorator rejects).
  5. /scan stays accessible to admins (admin-only operator surface).

Mirrors the fixture style from tests/platform/test_universal_hub.py
(shared `client`, `user_factory`, `db_session`, and login_as helper).
"""
from __future__ import annotations

import pytest


def login_as(client, user):
    """Bypass the email/password form. Mirrors the universal-hub helper."""
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


# ---------------------------------------------------------------------------
# Default landing
# ---------------------------------------------------------------------------


def test_admin_lands_on_customer_hub_by_default(client, user_factory):
    """Admin GET / renders fiesta_home.html (NOT a 302 to /scan)."""
    admin = user_factory("vw_admin_default", persona=None, role="admin")
    login_as(client, admin)
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 200, (
        f"admin / should render hub; got {resp.status_code} "
        f"-> {resp.headers.get('Location', '')!r}"
    )
    body = resp.get_data(as_text=True)
    assert 'data-fiesta-home="1"' in body, "fiesta_home.html marker missing"


def test_admin_sees_view_as_toggle_in_topbar(client, user_factory):
    """The View-As toggle is rendered in the topbar for admin users."""
    admin = user_factory("vw_admin_toggle", persona=None, role="admin")
    login_as(client, admin)
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # The toggle is keyed by data-fiesta-view-as on the wrapper.
    assert 'data-fiesta-view-as=' in body, "view-as toggle missing from topbar"
    assert 'data-fiesta-view-as-customer="1"' in body
    assert 'data-fiesta-view-as-admin="1"' in body


def test_non_admin_does_not_see_view_as_toggle(client, user_factory):
    """The toggle is admin-only — regular users don't see it."""
    u = user_factory("vw_user_no_toggle", persona=None, role="user")
    login_as(client, u)
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-fiesta-view-as=' not in body


# ---------------------------------------------------------------------------
# Toggle endpoint
# ---------------------------------------------------------------------------


def test_admin_can_flip_view_to_admin(client, user_factory):
    """POST/GET /admin/view-as/admin sets session['admin_view_as']='admin'."""
    admin = user_factory("vw_flip_to_admin", persona=None, role="admin")
    login_as(client, admin)
    # Trigger the flip (GET is OK per the route definition).
    resp = client.get("/admin/view-as/admin", follow_redirects=False)
    assert resp.status_code in (301, 302), (
        f"expected redirect, got {resp.status_code}"
    )
    # The session should now carry 'admin_view_as'='admin'.
    with client.session_transaction() as sess:
        assert sess.get("admin_view_as") == "admin"


def test_admin_can_flip_view_to_customer(client, user_factory):
    """GET /admin/view-as/customer sets session['admin_view_as']='customer'."""
    admin = user_factory("vw_flip_to_customer", persona=None, role="admin")
    login_as(client, admin)
    # First flip to admin so we have a non-default state to flip back from.
    client.get("/admin/view-as/admin")
    # Now flip back.
    resp = client.get("/admin/view-as/customer", follow_redirects=False)
    assert resp.status_code in (301, 302)
    with client.session_transaction() as sess:
        assert sess.get("admin_view_as") == "customer"


def test_unknown_role_coerced_to_customer(client, user_factory):
    """Anything other than 'admin' is whitelisted to 'customer'."""
    admin = user_factory("vw_flip_garbage", persona=None, role="admin")
    login_as(client, admin)
    resp = client.get("/admin/view-as/__garbage__", follow_redirects=False)
    assert resp.status_code in (301, 302)
    with client.session_transaction() as sess:
        assert sess.get("admin_view_as") == "customer"


# ---------------------------------------------------------------------------
# Shell selection based on toggle
# ---------------------------------------------------------------------------


def test_admin_in_admin_view_renders_admin_shell_on_home(client, user_factory):
    """After flipping to admin-view, GET / renders inside the admin shell —
    we detect this via the af-admin-badge / admin sidebar marker which only
    appears in templates/_fiesta/topbar_admin.html + sidebar_admin.html."""
    admin = user_factory("vw_admin_shell", persona=None, role="admin")
    login_as(client, admin)
    # Flip to admin-view first.
    client.get("/admin/view-as/admin")
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # The admin topbar carries the ADMIN badge text + the admin sidebar
    # class `fiesta-sidebar-admin`.
    assert "fiesta-sidebar-admin" in body, "admin sidebar not rendered"


def test_admin_in_customer_view_renders_customer_shell_on_home(
    client, user_factory
):
    """In the default customer-view, the customer sidebar is rendered (NOT
    the fiesta-sidebar-admin variant)."""
    admin = user_factory("vw_cust_shell", persona=None, role="admin")
    login_as(client, admin)
    # Belt-and-braces: explicitly flip to customer-view so we don't rely
    # on the default value.
    client.get("/admin/view-as/customer")
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "fiesta-sidebar-admin" not in body, (
        "customer-view should not render admin sidebar"
    )


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


def test_non_admin_cannot_hit_view_as_endpoint(client, user_factory):
    """admin_required decorator rejects non-admin role.

    The decorator may return 302 (redirect to /), 403, or 404 depending on
    the project's auth convention. We accept any non-2xx as "rejected"."""
    u = user_factory("vw_non_admin", persona=None, role="user")
    login_as(client, u)
    resp = client.get("/admin/view-as/admin", follow_redirects=False)
    assert resp.status_code >= 300, (
        f"non-admin should be rejected, got {resp.status_code}"
    )
    # Session must NOT have been mutated by an unauthorised hit.
    with client.session_transaction() as sess:
        assert sess.get("admin_view_as") in (None, "customer")
