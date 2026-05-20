"""
Fixtures for the Wave 6 FIESTA admin surface tests (middleware + S15).

Reuses the validated app + client + db_session fixtures from
``tests/remittance/conftest.py`` (env loading, sys.path bootstrap, CSRF
disable, Flask app construction with every blueprint registered).

Adds:
  * ``admin_user`` / ``non_admin_user`` / ``anonymous_client``
  * a teardown that deletes any User rows the suite created (identified by
    the conventional ``pytest_fa_admin_*@fiesta.local`` email prefix), plus
    their FK-dependent AuditLog rows.

The fixtures speak to the *live Neon DB* (same pattern as the other suites
in this repo). The user prefix is unique to this suite so concurrent runs
don't trip on each other.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest

from werkzeug.security import generate_password_hash

# Re-export the shared fixtures so pytest finds them in this scope.
# Note: ``login_as`` in remittance.conftest is a helper *function*, not a
# pytest fixture. We import it under a private name and re-expose it as a
# fixture below so individual tests can request it like any other fixture.
from tests.remittance.conftest import (  # noqa: F401
    app,
    client,
    db_session,
)
from tests.remittance.conftest import login_as as _login_as_helper


@pytest.fixture
def login_as():
    """Expose the ``login_as(client, user)`` helper from remittance.conftest
    as a pytest fixture so tests can request it by name."""
    return _login_as_helper


ADMIN_TEST_PREFIX = "pytest_fa_admin_"


def _make_user(*, db_session, is_admin: bool = False, role: str = "user",
               persona: str | None = None, subscription_status: str = "free_trial",
               stripe_customer_id: str | None = None,
               tos_accepted_version: str | None = "v0.1-draft",
               tos_accepted_at: datetime | None = None,
               onboarding_completed: bool = True):
    """Create a User row for the test and return it. Caller owns teardown
    via the autouse ``_cleanup_admin_users`` fixture below."""
    from models import User
    email = f"{ADMIN_TEST_PREFIX}{uuid.uuid4().hex[:8]}@fiesta.local"
    u = User(
        email=email,
        password_hash=generate_password_hash("pytest-pw-not-real"),
        name=f"Pytest Admin {email[:20]}",
        role="admin" if is_admin else role,
        subscription_status=subscription_status,
        access_expiration_date=datetime.utcnow() + timedelta(days=365),
        is_email_verified=True,
        onboarding_completed=onboarding_completed,
        persona=persona,
        tos_accepted_version=tos_accepted_version,
        tos_accepted_at=(tos_accepted_at if tos_accepted_at is not None
                         else (datetime.utcnow() if tos_accepted_version else None)),
    )
    # Best-effort: set the new boolean column / cached stripe id if the model
    # exposes them. Wave 6 ships these as raw DB columns even though the
    # model class isn't redeclared — set them via __dict__ for the post-migration
    # case, fall back to silent no-op otherwise.
    if hasattr(u.__class__, "stripe_customer_id") or stripe_customer_id is not None:
        try:
            setattr(u, "stripe_customer_id", stripe_customer_id)
        except Exception:
            pass
    db_session.add(u)
    db_session.commit()
    return u


@pytest.fixture
def admin_user(db_session):
    """A user with ``role='admin'`` (and therefore ``is_admin()`` returns True).

    Cleanup mirrors the remittance fixture pattern: best-effort FK purge,
    then the User row, all committed inside the same db_session.
    """
    u = _make_user(db_session=db_session, is_admin=True,
                   subscription_status="self_file")
    yield u
    _cleanup_user(db_session, u.id)


@pytest.fixture
def non_admin_user(db_session):
    """A standard signed-in user. ``role='user'`` → ``is_admin()`` returns False."""
    u = _make_user(db_session=db_session, is_admin=False,
                   subscription_status="free_trial")
    yield u
    _cleanup_user(db_session, u.id)


@pytest.fixture
def stripe_admin_user(db_session):
    """An admin who has a cached Stripe customer id (for the Stripe-link cell)."""
    u = _make_user(db_session=db_session, is_admin=True,
                   subscription_status="self_file",
                   stripe_customer_id="cus_PYTEST_FAKE_CUST_001")
    yield u
    _cleanup_user(db_session, u.id)


def _cleanup_user(db_session, user_id: int) -> None:
    """Delete a single test user + their AuditLog rows."""
    from models import User, AuditLog
    try:
        AuditLog.query.filter(AuditLog.user_id == user_id).delete(
            synchronize_session=False
        )
    except Exception:
        db_session.rollback()
    try:
        User.query.filter(User.id == user_id).delete(synchronize_session=False)
    except Exception:
        db_session.rollback()
    db_session.commit()


@pytest.fixture(autouse=True)
def _cleanup_orphan_admin_users(db_session):
    """Belt-and-braces sweep: delete any prefix-matching rows that earlier
    suite runs leaked (e.g. crashed mid-test before fixture teardown ran)."""
    from models import User, AuditLog
    yield
    leftovers = User.query.filter(
        User.email.like(f"{ADMIN_TEST_PREFIX}%")
    ).all()
    if leftovers:
        ids = [u.id for u in leftovers]
        try:
            AuditLog.query.filter(AuditLog.user_id.in_(ids)).delete(
                synchronize_session=False
            )
        except Exception:
            db_session.rollback()
        try:
            User.query.filter(User.id.in_(ids)).delete(synchronize_session=False)
        except Exception:
            db_session.rollback()
        db_session.commit()


@pytest.fixture
def gated_view_path(app):
    """Mount a one-off ``/_test/admin/gated`` view wrapped in our admin_required
    decorator. SESSION-scoped — the route is added BEFORE any test request
    fires (Flask blocks post-first-request route registration).

    The view returns plain text so tests can assert the *string body* without
    HTML parsing, plus a 200 to distinguish "decorator allowed through" from
    the various 302 redirect cases.
    """
    from fiesta.auth.decorators import admin_required
    path = "/_test/admin/gated"

    if not any(rule.rule == path for rule in app.url_map.iter_rules()):
        @app.route(path, methods=["GET"], endpoint="_test_admin_gated")
        @admin_required
        def _gated():
            return ("ADMIN_VIEW_OK", 200, {"Content-Type": "text/plain"})

    return path
