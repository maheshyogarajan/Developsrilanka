"""
Tests for ``fiesta.auth.decorators.admin_required`` — Wave 6 (2026-05-20).

Coverage:
  1. test_anonymous_user_redirected_to_login
  2. test_anonymous_redirect_preserves_next_url
  3. test_non_admin_user_redirected_to_index_with_flash
  4. test_admin_user_passes_through
  5. test_decorator_is_robust_to_callable_is_admin_attr
  6. test_decorator_treats_missing_is_admin_as_false
  7. test_decorator_treats_property_is_admin_attr_correctly

Backed by the live Neon DB via ``tests/remittance/conftest.py`` fixtures and
the suite-scoped ``tests/fiesta_admin/conftest.py`` helpers.

Run:
    cd /c/Users/mahes/fiesta_replit_source/DevelopSriLanka
    python -m pytest tests/fiesta_admin/test_admin_required.py -v
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


# --------------------------------------------------------------------------- #
# 1) Anonymous → 302 to /login
# --------------------------------------------------------------------------- #
def test_anonymous_user_redirected_to_login(client, gated_view_path):
    """An unauthenticated GET should redirect to the login screen, never
    invoke the wrapped view, never set a flash with 'Admin access required.'"""
    resp = client.get(gated_view_path, follow_redirects=False)
    assert resp.status_code in (301, 302), (
        f"Expected redirect; got {resp.status_code}. Body: {resp.data[:200]!r}"
    )
    location = resp.headers.get("Location", "")
    # Either '/login' or '/login?next=...' is acceptable.
    assert "/login" in location, (
        f"Expected Location to point at /login; got {location!r}"
    )
    # The wrapped view's body MUST NOT leak.
    assert b"ADMIN_VIEW_OK" not in resp.data


def test_anonymous_redirect_preserves_next_url(client, gated_view_path):
    """The login redirect URL should carry a ``next`` query param so the user
    lands back on the gated page after authenticating."""
    resp = client.get(gated_view_path, follow_redirects=False)
    location = resp.headers.get("Location", "")
    # Both '?next=' and url-encoded forms are acceptable.
    assert "next=" in location, (
        f"Expected ?next= in redirect; got {location!r}"
    )


# --------------------------------------------------------------------------- #
# 2) Non-admin → 403 (HTML or JSON per Accept)
#
# F8.3 (Stage C4): the canonical decorator now returns a styled 403 page for
# HTML callers and ``{"error": "admin_required", "status": 403}`` JSON for API
# callers. The earlier 302-with-flash behaviour was Wave 6; these tests are
# updated to match the F8.3 contract.
# --------------------------------------------------------------------------- #
def test_non_admin_user_gets_403_html(client, non_admin_user,
                                       gated_view_path, login_as):
    """Signed-in but non-admin should receive a styled 403 page. The wrapped
    view body MUST NOT leak."""
    login_as(client, non_admin_user)
    resp = client.get(gated_view_path, follow_redirects=False)

    assert resp.status_code == 403, (
        f"Expected 403; got {resp.status_code}. "
        f"Did the decorator let a non-admin through? Body: {resp.data[:200]!r}"
    )
    # 403 page identity — admin/403.html says "403 — Admin Only".
    assert b"Admin" in resp.data or b"403" in resp.data, (
        f"Expected admin 403 page body; got {resp.data[:200]!r}"
    )
    # And critically: the wrapped view's body MUST NOT leak.
    assert b"ADMIN_VIEW_OK" not in resp.data


def test_non_admin_user_gets_403_json_when_requested(client, non_admin_user,
                                                      gated_view_path, login_as):
    """JSON callers (Accept: application/json) receive the canonical JSON
    error shape, not the HTML page."""
    import json as _json
    login_as(client, non_admin_user)
    resp = client.get(
        gated_view_path,
        headers={"Accept": "application/json"},
        follow_redirects=False,
    )
    assert resp.status_code == 403, (
        f"Expected 403; got {resp.status_code}. Body: {resp.data[:200]!r}"
    )
    payload = _json.loads(resp.data)
    assert payload.get("error") == "admin_required", (
        f"Expected error=admin_required; got {payload!r}"
    )
    assert payload.get("status") == 403


# --------------------------------------------------------------------------- #
# 3) Admin → 200 + view body
# --------------------------------------------------------------------------- #
def test_admin_user_passes_through(client, admin_user, gated_view_path, login_as):
    """Signed-in admin should receive the wrapped view's response unchanged."""
    login_as(client, admin_user)
    resp = client.get(gated_view_path, follow_redirects=False)
    assert resp.status_code == 200, (
        f"Admin was blocked. Body: {resp.data[:300]!r}"
    )
    assert b"ADMIN_VIEW_OK" in resp.data


# --------------------------------------------------------------------------- #
# 4) Decorator unit-tests against the callable / property / missing shapes.
#
# These don't go through HTTP — they call _user_is_admin directly with stub
# users so we can prove the callable-vs-attribute fallback works.
# --------------------------------------------------------------------------- #
class _StubUserCallable:
    """Mirrors the *current* User model: is_admin is a method."""
    is_authenticated = True
    def is_admin(self):  # pragma: no cover - used by the production path
        return True


class _StubUserCallableFalse:
    is_authenticated = True
    def is_admin(self):
        return False


class _StubUserBoolean:
    """Mirrors the *future* User model: is_admin is a column / property."""
    is_authenticated = True
    is_admin = True


class _StubUserBooleanFalse:
    is_authenticated = True
    is_admin = False


class _StubUserMissing:
    """No is_admin attribute at all — must default to False."""
    is_authenticated = True


class _StubUserAnonymous:
    is_authenticated = False


def test_decorator_is_robust_to_callable_is_admin_attr():
    """When the model exposes ``is_admin`` as a method, the helper must call
    it and read its return value — not rely on the bound method's truthiness."""
    from fiesta.auth.decorators import _user_is_admin
    assert _user_is_admin(_StubUserCallable()) is True
    assert _user_is_admin(_StubUserCallableFalse()) is False


def test_decorator_treats_property_is_admin_attr_correctly():
    """When ``is_admin`` is a direct attribute (boolean column / property),
    the helper must read it as a bool — not call it."""
    from fiesta.auth.decorators import _user_is_admin
    assert _user_is_admin(_StubUserBoolean()) is True
    assert _user_is_admin(_StubUserBooleanFalse()) is False


def test_decorator_treats_missing_is_admin_as_false():
    """Defensive: a user without an ``is_admin`` attribute must NOT be
    elevated. Belt-and-braces against future model refactors."""
    from fiesta.auth.decorators import _user_is_admin
    assert _user_is_admin(_StubUserMissing()) is False


def test_decorator_treats_anonymous_user_as_not_admin():
    """Anonymous users are always non-admin, regardless of attribute shape."""
    from fiesta.auth.decorators import _user_is_admin
    assert _user_is_admin(_StubUserAnonymous()) is False


def test_decorator_swallows_exception_in_is_admin_method():
    """If ``is_admin()`` raises (e.g. DB error reading role), the helper must
    return False — never elevate due to a transient failure."""
    from fiesta.auth.decorators import _user_is_admin

    class _StubExploding:
        is_authenticated = True
        def is_admin(self):
            raise RuntimeError("transient DB failure")

    assert _user_is_admin(_StubExploding()) is False
