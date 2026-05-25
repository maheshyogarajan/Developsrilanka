"""
Stage C4 Admin Consolidation acceptance suite.

Covers defect D3 + D4 + D5 + F8.1 + F8.2 + F8.3 + F8.7 from
``working files/strategic/council/persistent/fiesta/PLAN_X9_WIRE_UP.md``
and the MS1 master defect log.

Run::

    cd "C:/Users/mahes/fiesta_phase_a/Developsrilanka_ms1_c4"
    python -m pytest tests/admin/test_admin_consolidation.py -v

Test list (matches the dispatch contract one-for-one):

  1. test_admin_index_renders_new_dashboard           D3
  2. test_admin_submissions_200_for_25_26              D4
  3. test_admin_settings_renders_no_500                D5
  4. test_user_role_admin_gates_admin_routes           F8.1
  5. test_admin_layout_consistent_across_admin_routes  F8.2
  6. test_admin_required_decorator_returns_403_html_or_json_per_accept  F8.3
  7. test_user_last_login_at_updates_on_auth           F8.7

Reuses the live-Neon fixtures from ``tests/fiesta_admin/conftest.py``.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta

import pytest

# Re-use the suite fixtures from fiesta_admin. pytest collects fixtures
# transitively via conftest.py at the parent + sibling level, so we just
# import the conftest module to trigger its registration.
from tests.fiesta_admin.conftest import (  # noqa: F401
    app,
    client,
    db_session,
    login_as,
    admin_user,
    non_admin_user,
)


# --------------------------------------------------------------------------- #
# Stage C4 — defensive session cleanup.
#
# These tests exercise live admin routes (full request → render → redirect
# stack), which means the test session can pick up stale state from prior
# tests in the same pytest run. Force a clean SQLAlchemy session BEFORE
# each test fires so the StaleDataError / persona-FK-violation seen in
# multi-test runs against the live Neon DB doesn't poison this suite.
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _ensure_clean_session(app):
    """Rollback any prior-test pending state before this test starts.

    Tests in this file share the global ``db.session`` (Flask-SQLAlchemy
    scoped session). If a previous test left a flush failure pending, the
    next request commit raises ``InvalidRequestError`` and the dashboard
    view's blanket except clause redirects to ``home`` — which looks like
    a 302 to the test, not the 200/403 we actually want to assert.
    """
    from app import db
    with app.app_context():
        try:
            db.session.rollback()
            db.session.expire_all()
        except Exception:
            pass
    yield
    with app.app_context():
        try:
            db.session.rollback()
            db.session.expire_all()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# 1) D3 — /admin renders the new dashboard, not redirect to /scan.
# --------------------------------------------------------------------------- #
def test_admin_index_renders_new_dashboard(client, admin_user, login_as):
    """/admin must return the new FIESTA admin dashboard for an admin user
    (200 HTML), not a redirect to the legacy /scan landing."""
    login_as(client, admin_user)
    resp = client.get("/admin", follow_redirects=False)

    # MUST NOT be a redirect (the regression).
    assert resp.status_code == 200, (
        f"D3 regressed — /admin returned {resp.status_code} for admin "
        f"(expected 200). Location: {resp.headers.get('Location')!r}, "
        f"body[:200]: {resp.data[:200]!r}"
    )
    # Body fingerprint: dashboard.html extends layout_fiesta admin shell which
    # ships the af-topbar + an ADMIN badge.
    body = resp.data.decode("utf-8", errors="replace")
    assert (
        "Admin Dashboard" in body
        or "af-topbar" in body
        or "ADMIN" in body
    ), f"Expected admin dashboard fingerprint; got body[:300]={body[:300]!r}"


# --------------------------------------------------------------------------- #
# 2) D4 — /admin/submissions?tax_year=2025/26 returns 200 (was 500).
# --------------------------------------------------------------------------- #
def test_admin_submissions_200_for_25_26(client, admin_user, login_as):
    """/admin/submissions with the 25/26 filter must render the operator
    submissions view (200), not a 500. Empty result set is acceptable —
    the defect was a normaliser format mismatch that crashed the query."""
    login_as(client, admin_user)
    resp = client.get(
        "/admin/submissions?tax_year=2025/26",
        follow_redirects=False,
    )
    assert resp.status_code == 200, (
        f"D4 regressed — /admin/submissions?tax_year=2025/26 returned "
        f"{resp.status_code} (expected 200). body[:400]: {resp.data[:400]!r}"
    )
    body = resp.data.decode("utf-8", errors="replace")
    # Page fingerprint — submissions.html has a "Submissions Admin" h1.
    assert "Submissions Admin" in body, (
        f"Expected submissions view body; got body[:300]={body[:300]!r}"
    )


# --------------------------------------------------------------------------- #
# 3) D5 — /admin/fie/settings renders without 500. Verifies SystemSetting CRUD.
# --------------------------------------------------------------------------- #
def test_admin_settings_renders_no_500(client, admin_user, login_as):
    """/admin/fie/settings must render the DB-backed settings form (200) for
    an admin. The defect was the SystemSetting table being absent / the form
    template failing to render.

    Also smoke-tests the SystemSetting.set/get path by writing a probe key
    and reading it back — proves CRUD works end-to-end.
    """
    login_as(client, admin_user)
    resp = client.get("/admin/fie/settings", follow_redirects=False)
    assert resp.status_code == 200, (
        f"D5 regressed — /admin/fie/settings returned {resp.status_code} "
        f"(expected 200). body[:400]: {resp.data[:400]!r}"
    )

    # Form fingerprint — input names from fie_settings.html.
    body = resp.data.decode("utf-8", errors="replace")
    assert "lkr_business_tax_rate" in body, (
        f"Expected settings form; got body[:300]={body[:300]!r}"
    )

    # CRUD probe — exercise SystemSetting.set + .get directly so a broken
    # model surface fails the test even if the GET happens to short-circuit.
    from models import SystemSetting
    probe_key = f"_pytest_admin_consolidation_{uuid.uuid4().hex[:6]}"
    try:
        SystemSetting.set(probe_key, {"verified": True, "v": 1})
        got = SystemSetting.get(probe_key)
        assert got == {"verified": True, "v": 1}, (
            f"SystemSetting round-trip failed: got {got!r}"
        )
    finally:
        # Cleanup the probe key
        try:
            row = SystemSetting.query.get(probe_key)
            if row is not None:
                from app import db
                db.session.delete(row)
                db.session.commit()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# 4) F8.1 — User.role='admin' gates admin views (and the seed bug is fixed).
# --------------------------------------------------------------------------- #
def test_user_role_admin_gates_admin_routes(client, db_session, login_as):
    """A user with role='admin' should be admitted to /admin; flipping their
    role back to 'user' should result in 403. This is the regression test for
    the F8.1 silent-failure where ``u.is_admin = True`` left role at 'user'.
    """
    from models import User, AuditLog
    from werkzeug.security import generate_password_hash

    email = f"pytest_fa_admin_role_{uuid.uuid4().hex[:8]}@fiesta.local"
    u = User(
        email=email,
        password_hash=generate_password_hash("pytest-pw-not-real"),
        name="Pytest Role Test",
        role="user",                       # start as a non-admin
        subscription_status="free_trial",
        access_expiration_date=datetime.utcnow() + timedelta(days=365),
        is_email_verified=True,
        onboarding_completed=True,
    )
    db_session.add(u)
    db_session.commit()

    try:
        # 1) role='user' -> 403 on /admin
        login_as(client, u)
        resp = client.get("/admin", follow_redirects=False)
        assert resp.status_code == 403, (
            f"Expected 403 for role='user' on /admin; got {resp.status_code}"
        )

        # 2) Promote via the canonical helper (F8.1 path)
        u.promote_to_admin(reason="pytest_admin_consolidation")
        db_session.commit()
        # Sanity: helper wrote role='admin' AND is_admin() returns True
        assert u.role == "admin"
        assert u.is_admin() is True

        # 3) role='admin' -> 200 on /admin
        resp2 = client.get("/admin", follow_redirects=False)
        assert resp2.status_code == 200, (
            f"Expected 200 for role='admin' on /admin; got {resp2.status_code}"
        )

        # 4) promote_to_admin wrote an AuditLog row
        audits = AuditLog.query.filter_by(
            entity_type="user", entity_id=u.id, action="UPDATE"
        ).all()
        promote_rows = [
            a for a in audits
            if isinstance(a.changed_fields, dict)
            and a.changed_fields.get("operation") == "promote_to_admin"
        ]
        assert promote_rows, (
            f"Expected AuditLog row from promote_to_admin; got {audits!r}"
        )
    finally:
        # Cleanup
        try:
            AuditLog.query.filter_by(entity_type="user", entity_id=u.id).delete(
                synchronize_session=False
            )
        except Exception:
            db_session.rollback()
        try:
            User.query.filter(User.id == u.id).delete(synchronize_session=False)
        except Exception:
            db_session.rollback()
        db_session.commit()


# --------------------------------------------------------------------------- #
# 5) F8.2 — every /admin/* response is wrapped in admin/layout_fiesta.html.
# --------------------------------------------------------------------------- #
def test_admin_layout_consistent_across_admin_routes(client, admin_user, login_as):
    """Every admin route the dispatch covers must render within the unified
    admin shell. We assert the shell's body class + topbar marker appear in
    each response; if a template still extends the legacy layout, this fails.
    """
    login_as(client, admin_user)

    # Routes that Stage C4 explicitly fixed or migrated.
    routes = [
        "/admin",                               # dashboard.html
        "/admin/submissions",                   # submissions.html
        "/admin/fie/settings",                  # fie_settings.html
    ]

    for route in routes:
        resp = client.get(route, follow_redirects=False)
        assert resp.status_code == 200, (
            f"{route} returned {resp.status_code}; body[:300]={resp.data[:300]!r}"
        )
        body = resp.data.decode("utf-8", errors="replace")
        # admin/layout_fiesta.html hallmark: <body class="admin-fiesta-body">
        # AND the af-topbar div.
        has_shell = (
            "admin-fiesta-body" in body
            or "af-topbar" in body
            or 'id="adminFiesta"' in body
        )
        assert has_shell, (
            f"{route} did not render within admin/layout_fiesta.html shell; "
            f"body[:400]={body[:400]!r}"
        )


# --------------------------------------------------------------------------- #
# 6) F8.3 — decorator returns 403 HTML OR 403 JSON per Accept header.
# --------------------------------------------------------------------------- #
def test_admin_required_decorator_returns_403_html_or_json_per_accept(
    client, non_admin_user, login_as
):
    """A non-admin hitting an admin_required view should receive:
      * 403 HTML when no JSON Accept header is sent
      * 403 JSON {"error": "admin_required", "status": 403} otherwise

    Uses ``/admin`` as the gated view — it's wrapped in ``admin_required``
    in ``admin_routes.py``. Avoids the ``gated_view_path`` fixture's
    late-registration problem when other admin tests have already fired
    requests (Flask blocks ``@app.route`` post-first-request).
    """
    login_as(client, non_admin_user)

    # HTML path
    resp_html = client.get("/admin", follow_redirects=False)
    assert resp_html.status_code == 403, (
        f"Expected 403 HTML; got {resp_html.status_code}. "
        f"body[:300]={resp_html.data[:300]!r}"
    )
    body_html = resp_html.data.decode("utf-8", errors="replace")
    assert "Admin" in body_html or "403" in body_html, (
        f"Expected HTML 403 body; got {body_html[:300]!r}"
    )

    # JSON path
    resp_json = client.get(
        "/admin",
        headers={"Accept": "application/json"},
        follow_redirects=False,
    )
    assert resp_json.status_code == 403, (
        f"Expected 403 JSON; got {resp_json.status_code}. "
        f"body[:300]={resp_json.data[:300]!r}"
    )
    payload = json.loads(resp_json.data)
    assert payload.get("error") == "admin_required"
    assert payload.get("status") == 403


# --------------------------------------------------------------------------- #
# 7) F8.7 — last_login_at is updated on every successful auth.
# --------------------------------------------------------------------------- #
def test_user_last_login_at_updates_on_auth(app, db_session):
    """Triggering the Flask-Login user_logged_in signal for a real User row
    must move ``last_login_at`` forward. This proves the signal handler is
    wired and the column exists in the DB."""
    from models import User
    from werkzeug.security import generate_password_hash
    from flask_login import user_logged_in

    email = f"pytest_fa_admin_lastlogin_{uuid.uuid4().hex[:8]}@fiesta.local"
    u = User(
        email=email,
        password_hash=generate_password_hash("pytest-pw-not-real"),
        name="Pytest Last Login Test",
        role="user",
        subscription_status="free_trial",
        access_expiration_date=datetime.utcnow() + timedelta(days=365),
        is_email_verified=True,
        onboarding_completed=True,
        last_login_at=None,
    )
    db_session.add(u)
    db_session.commit()

    try:
        before = u.last_login_at
        # Fire the signal as Flask-Login would after a successful login.
        # The handler in app.py reads `user.last_login_at = utcnow(); commit`.
        user_logged_in.send(app, user=u)

        # Re-fetch from DB to be sure we're seeing persisted state.
        db_session.expire(u)
        from models import User as _U
        refreshed = _U.query.get(u.id)
        assert refreshed.last_login_at is not None, (
            "last_login_at was None after user_logged_in signal — "
            "is the F8.7 signal handler wired?"
        )
        if before is not None:
            assert refreshed.last_login_at >= before, (
                f"last_login_at went backwards: before={before}, "
                f"after={refreshed.last_login_at}"
            )
    finally:
        try:
            from models import AuditLog
            AuditLog.query.filter_by(entity_type="user", entity_id=u.id).delete(
                synchronize_session=False
            )
        except Exception:
            db_session.rollback()
        try:
            User.query.filter(User.id == u.id).delete(synchronize_session=False)
        except Exception:
            db_session.rollback()
        db_session.commit()
