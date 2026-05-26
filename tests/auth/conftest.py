"""
S2 signup test fixtures — Wave 1 (2026-05-20).

Reuses the validated `app` + `client` + `db_session` fixtures from
`tests/remittance/conftest.py` (env loading, sys.path bootstrap, CSRF
disable, Flask app construction with all blueprints registered).

Adds a `cleanup_signup_users` autouse fixture that removes any User rows
this suite created at end of test, identified by the conventional
`pytest_s2_signup_*@fiesta.local` email prefix.
"""
from __future__ import annotations

import pytest

# Re-export shared fixtures so pytest discovers them in this scope.
from tests.remittance.conftest import (  # noqa: F401
    app,
    client,
    db_session,
)


# Email prefix used by every test in this module so cleanup is safe and scoped.
SIGNUP_TEST_PREFIX = "pytest_s2_signup_"


@pytest.fixture(autouse=True)
def _cleanup_s2_signup_users(db_session):
    """Reset rate-limit + delete any User rows created by this suite.

    Runs BEFORE and AFTER each test:
      - BEFORE: clears the registration_rate_limit row for 127.0.0.1 in the
        current window. Without this, running >5 tests in the same UTC hour
        trips the 5-attempts-per-IP gate and the later tests fail spuriously.
      - AFTER: deletes any User rows whose email begins with the suite-specific
        prefix (and their AuditLog FK rows).
    """
    from models import RegistrationRateLimit, User, AuditLog
    # Clear localhost rate-limit so each test gets a clean budget.
    RegistrationRateLimit.query.filter(
        RegistrationRateLimit.ip_address.in_(("127.0.0.1", "localhost"))
    ).delete(synchronize_session=False)
    db_session.commit()

    yield

    test_users = User.query.filter(
        User.email.like(f"{SIGNUP_TEST_PREFIX}%")
    ).all()
    if test_users:
        ids = [u.id for u in test_users]
        AuditLog.query.filter(AuditLog.user_id.in_(ids)).delete(synchronize_session=False)
        User.query.filter(User.id.in_(ids)).delete(synchronize_session=False)
    # Reset rate-limit again so the next test starts clean.
    RegistrationRateLimit.query.filter(
        RegistrationRateLimit.ip_address.in_(("127.0.0.1", "localhost"))
    ).delete(synchronize_session=False)
    db_session.commit()


# F1.6 auth-surface tests (2026-05-26): small factory for creating a verified
# user with a known password. Tracks created users for autouse cleanup.
_AUTH_SUITE_PREFIX = "pytest_auth_surface_"


@pytest.fixture
def user_factory(db_session):
    """Yield a callable that creates+commits a User row with a known password.

    Usage:
        user = user_factory("f1_6_redirect",
                            is_email_verified=True,
                            onboarding_completed=True)
        # User.email is f"{_AUTH_SUITE_PREFIX}f1_6_redirect@fiesta.local"
        # User.password_hash is set from "pytest-pw-not-real" by default; test
        # callers can override by setting user.password_hash + db.session.commit()
        # before acting (the F1.6 tests do this to set a known plain password).
    """
    from werkzeug.security import generate_password_hash
    from models import User
    created_ids: list[int] = []

    def _make(
        slug: str,
        *,
        is_email_verified: bool = True,
        onboarding_completed: bool = True,
        role: str = "user",
        password: str = "pytest-pw-not-real",
    ):
        email = f"{_AUTH_SUITE_PREFIX}{slug}@fiesta.local"
        # Drop any prior row with this email so re-runs don't trip uniqueness.
        existing = User.query.filter_by(email=email).first()
        if existing is not None:
            db_session.delete(existing)
            db_session.commit()
        u = User(
            email=email,
            password_hash=generate_password_hash(password),
            role=role,
        )
        if hasattr(u, "is_email_verified"):
            u.is_email_verified = is_email_verified
        if hasattr(u, "onboarding_completed"):
            u.onboarding_completed = onboarding_completed
        db_session.add(u)
        db_session.commit()
        created_ids.append(u.id)
        return u

    yield _make

    # Teardown: remove all users the factory created during this test.
    if created_ids:
        from models import AuditLog
        AuditLog.query.filter(AuditLog.user_id.in_(created_ids)).delete(
            synchronize_session=False
        )
        User.query.filter(User.id.in_(created_ids)).delete(
            synchronize_session=False
        )
        db_session.commit()
