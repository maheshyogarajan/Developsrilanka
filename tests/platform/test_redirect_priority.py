"""F-Platform-3 — redirect priority regression tests.

The bug (B-001): ``app.py::index()`` (the ``/scan`` handler) used to check
``current_user.organizations`` BEFORE the persona reroute. An
``sl_foreign_income`` user has no business org by design, so they fell
through to::

    /scan -> /onboarding -> onboarding_completed=True -> / -> /scan -> ...

— an infinite loop that blocked end-to-end regression for every other
X9 fix. The fix (commit 48918b6) reordered the checks so the persona
reroute fires immediately after the email-verification gate.

These tests lock the corrected ordering in place. They are intentionally
narrow: they assert WHERE the user goes after a single GET, not what the
destination page renders. Destination behaviour is owned by the
remittance / onboarding / verify-email-reminder suites.

Cases (per the F-Platform-3 task brief):

  1. ``sl_foreign_income`` user with NO orgs hits /scan -> 302 to
     /remittance/dashboard (NOT to /onboarding). This is the bug fix.
  2. Non-foreign-income user (legacy bookkeeping persona) with no orgs
     still routes to /onboarding. Legacy behaviour preserved.
  3. Admin role bypasses the persona reroute branch — admins always
     reach the admin / scan surface, never get bounced into the
     remittance dashboard meant for customers.
  4. Unverified-email user still routes to /verify-email-reminder. The
     email-verify gate is BEFORE the persona check by design, so it must
     still win for an sl_foreign_income user who hasn't verified yet.
"""
from __future__ import annotations

from urllib.parse import urlsplit


def login_as(client, user):
    """Bypass the email/password form by setting the Flask-Login session
    cookie directly. Inlined (rather than imported from .conftest) because
    a relative import would resolve the package name as ``platform`` and
    collide with Python's stdlib ``platform`` module on test collection."""
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def _redirect_path(response) -> str:
    """Extract the path component of a 302 Location header.

    Flask's test client returns absolute URLs in Location; we only care
    about the path so the assertions stay host-agnostic.
    """
    assert response.status_code == 302, (
        f"expected 302, got {response.status_code} (body={response.get_data(as_text=True)[:200]!r})"
    )
    location = response.headers.get("Location", "")
    return urlsplit(location).path


# -------------------------------------------------------------------- #
# Case 1: THE BUG FIX. sl_foreign_income + zero orgs must NOT loop
# through /onboarding. It must 302 straight to /remittance/dashboard.
# -------------------------------------------------------------------- #
def test_sl_foreign_income_user_no_org_does_not_loop(
    app, client, user_factory
):
    user = user_factory(
        "fie_noorg",
        persona="sl_foreign_income",
        is_email_verified=True,
        onboarding_completed=True,
    )
    login_as(client, user)

    resp = client.get("/scan")
    path = _redirect_path(resp)

    assert path == "/remittance/dashboard", (
        f"sl_foreign_income persona with no orgs redirected to {path!r}; "
        "expected /remittance/dashboard (F-Platform-3 reorder)"
    )
    # Explicit anti-regression: the legacy bug sent these users to
    # /onboarding. If this ever returns true again, the persona reroute
    # has slipped back below the org check.
    assert path != "/onboarding", (
        "regression: sl_foreign_income persona is being routed to /onboarding "
        "(the org-check is firing before the persona reroute)"
    )


# -------------------------------------------------------------------- #
# Case 2: Legacy bookkeeping behaviour preserved. A user WITHOUT the
# foreign-income persona, with no orgs, still gets the onboarding wizard.
# -------------------------------------------------------------------- #
def test_non_foreign_income_user_no_org_still_routes_to_onboarding(
    app, client, user_factory
):
    user = user_factory(
        "bookkeeping_noorg",
        persona=None,  # legacy bookkeeping persona has no persona value
        is_email_verified=True,
        onboarding_completed=True,
    )
    login_as(client, user)

    resp = client.get("/scan")
    path = _redirect_path(resp)

    assert path == "/onboarding", (
        f"legacy persona with no orgs redirected to {path!r}; "
        "expected /onboarding (org-check should still fire for non-FIESTA personas)"
    )


# -------------------------------------------------------------------- #
# Case 3: Admin bypass. Admins are operators, not customers — they
# never see the FIESTA hub, even if their (anomalous) admin row has
# the sl_foreign_income persona set. The admin surface must remain
# reachable from /scan for admins regardless of persona.
# -------------------------------------------------------------------- #
def test_admin_user_does_not_get_persona_redirect(
    app, client, user_factory
):
    # NOTE: this test deliberately stresses the EDGE case — an admin whose
    # row happens to also carry the sl_foreign_income persona value. The
    # contract under F-Platform-3 + the existing admin bypass is:
    #   - admin role bypasses the empty-org check (so /scan renders)
    #   - admin role MUST bypass the persona-reroute too, otherwise the
    #     legitimate operator gets pushed into the customer hub
    # If admins are intentionally allowed into the customer hub later,
    # this test should be updated, not silently softened.
    admin = user_factory(
        "admin_fie_persona",
        persona="sl_foreign_income",
        role="admin",
        is_email_verified=True,
        onboarding_completed=True,
    )
    login_as(client, admin)

    resp = client.get("/scan")

    # The acceptable outcomes for an admin hitting /scan are:
    #   (a) 200 — admin reaches the legacy scan page
    #   (b) 302 to any non-customer surface (NOT /remittance/dashboard)
    # The unacceptable outcome is the persona reroute firing and sending
    # the admin into the customer hub.
    if resp.status_code == 302:
        path = _redirect_path(resp)
        assert path != "/remittance/dashboard", (
            "regression: admin role is being routed to /remittance/dashboard via "
            "the persona reroute; the persona branch must not fire for admins"
        )
    else:
        assert resp.status_code == 200, (
            f"admin hitting /scan got unexpected status {resp.status_code}"
        )


# -------------------------------------------------------------------- #
# Case 4: The email-verify gate is BEFORE the persona check (this is
# intentional in the F-Platform-3 spec). An unverified sl_foreign_income
# user must still be sent to /verify-email-reminder, not the hub.
# -------------------------------------------------------------------- #
def test_unverified_email_still_routes_to_verify(
    app, client, user_factory
):
    user = user_factory(
        "fie_unverified",
        persona="sl_foreign_income",
        is_email_verified=False,
        onboarding_completed=True,
    )
    login_as(client, user)

    resp = client.get("/scan")
    path = _redirect_path(resp)

    assert path == "/verify-email-reminder", (
        f"unverified sl_foreign_income user redirected to {path!r}; "
        "expected /verify-email-reminder (the verify gate must win over the persona reroute)"
    )
    assert path != "/remittance/dashboard", (
        "regression: persona reroute is firing before the email-verify gate; "
        "unverified users must NOT reach the FIESTA hub"
    )
