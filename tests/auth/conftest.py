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
