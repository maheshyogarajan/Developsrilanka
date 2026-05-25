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


_GATED_VIEW_PATH = "/_test/admin/gated"


def _register_gated_view(app):
    """Register the ``/_test/admin/gated`` view via ``add_url_rule`` so the
    fixture can mount it BEFORE any test request fires.

    Earlier versions used ``@app.route`` inside the fixture — Flask blocks
    that decorator after the first request handles, so the fixture errored
    when other suites had already issued requests in the same pytest run.
    ``add_url_rule`` is the documented escape hatch and works equally
    well pre- and post-first-request in modern Flask (it does the same
    setup-finished check, so we mount eagerly at fixture-import time
    rather than lazily inside the fixture).
    """
    from fiesta.auth.decorators import admin_required

    if any(rule.rule == _GATED_VIEW_PATH for rule in app.url_map.iter_rules()):
        return

    @admin_required
    def _gated():
        return ("ADMIN_VIEW_OK", 200, {"Content-Type": "text/plain"})

    try:
        app.add_url_rule(
            _GATED_VIEW_PATH,
            endpoint="_test_admin_gated",
            view_func=_gated,
            methods=["GET"],
        )
    except AssertionError:
        # Flask's setup-finished guard fired — the app already handled a
        # request before the fixture loaded. The route registered earlier
        # in the same pytest session is the same callable, so we can rely
        # on the no-op early-return at the top.
        pass


@pytest.fixture(autouse=True, scope="session")
def _register_gated_view_once(app):
    """Mount the gated view at session-start so it's present before ANY
    test in any suite fires a request against the shared app.

    Without this, running tests/admin/ before tests/fiesta_admin/ in the
    same session causes the gated_view_path fixture to fail with
    "setup method 'route' can no longer be called" — the app already
    handled its first request earlier.
    """
    _register_gated_view(app)
    return None


@pytest.fixture
def gated_view_path(app):
    """Return the path of the admin_required-wrapped /_test/admin/gated view.

    The route is registered at session-start via _register_gated_view_once,
    so this fixture is now a thin path-string accessor.
    """
    _register_gated_view(app)
    return _GATED_VIEW_PATH
